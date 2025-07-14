# #
# Tech stack used: Python, FastAPI, OpenAI Realtime API (STT, NLU, TTS)
# #

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import io
from typing import Any, Dict, List, Optional, AsyncGenerator
import functools # Added for running sync SMS in executor

import audioop
from fastapi import WebSocket, WebSocketDisconnect # type: ignore
from pydub import AudioSegment # type: ignore
import openai # AsyncOpenAI, still needed for client setup and potentially types
import websockets # For WebSocket client connection to OpenAI Realtime API
import random # For dish recommendations

from app.handlers.common_tool_defs import (
    ORDER_SUMMARY_TOOL_SCHEMA_CN_OPENAI,
    CHECK_MENU_ITEM_TOOL_SCHEMA_CN_OPENAI,
    RECOMMEND_DISHES_TOOL_SCHEMA_CN_OPENAI,
    LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_CN_OPENAI,
    CATEGORY_ALIASES_CN,
    DISHES_ALIASES_CN
)
from app.services.database_service import save_order_details
from app.utils.twilio import send_sms, end_call # Added end_call
from app.utils.constants import get_restaurant_config # To get portal_id
from app.constants import THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT, THIRTY_NINE_MILES_PORTAL_ID_DINE_IN # Import portal IDs
from app.utils.thirty_nine_miles import ( # Updated to user's specified filename
    find_dish_by_chinese_name,
    get_extracted_dishes, # This will be key for category filtering
    add_order as add_thirty_nine_miles_order, # Renamed for clarity
    ApiOrderDto,
    ApiOrderItem,
    TextStore,
    LangCode
)
from app.handlers.chinese_tool_logic import execute_tool_logic # Import the common tool logic


logger = logging.getLogger(__name__)


class ChineseAudioOpenAIHandler:
    MAX_HISTORY_MESSAGES = 10 # For NLU context if managed locally
    MIN_TRANSCRIPT_LENGTH_FOR_NLU: int = 2
    
    BARGE_GUARD_MS_CONFIG: int = 200 # Recommended: 200 ms
    ECHO_DRAIN_MS_CONFIG: int = 100
    
    OPENAI_REALTIME_URL: str = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17" # Example model
    OPENAI_BETA_HEADER: str = "realtime=v1"

    # Configuration for the session.update message to OpenAI
    OPENAI_SESSION_CONFIG = {
        "modalities": ["text", "audio"],
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.4, # Recommended: 0.4
            "prefix_padding_ms": 300,
            "silence_duration_ms": 300, # Recommended: 300 ms
            "create_response": True,
            "interrupt_response": True
        },
        "voice": "ballad", # Matching user's working config
        "input_audio_transcription": {"model": "whisper-1"}, # Or "gpt-4o-transcribe" if supported
        "input_audio_format": "g711_ulaw", # mulaw
        "output_audio_format": "g711_ulaw", # Matching user's working config
        # "language": "zh", # Specify Chinese language for the session - Removed as it causes an "unknown_parameter" error
    }


    def __init__(
        self,
        websocket: WebSocket, # This is the Twilio WebSocket
        openai_api_key: str, # Direct API key
        *,
        send_welcome_message: bool = True,
        caller_phone: Optional[str] = None,
        client_id: Optional[str] = None,
        system_message: Optional[str] = None, # This might be part of OpenAI session config
        verbose_logging: bool = False,
    ) -> None:
        self.twilio_ws = websocket # Assign the Twilio WebSocket to an instance variable
        self.openai_api_key = openai_api_key
        self.caller_phone = caller_phone
        self.send_welcome = send_welcome_message
        self.client_id = client_id
        self.verbose_logging = verbose_logging
        
        # System message might be part of the initial prompt/config to OpenAI Realtime API
        self.system_message = system_message or "你是餐馆电话助理，帮助顾客点餐。"

        self.loop = asyncio.get_event_loop()
        
        self._stop_event_handled = False
        self.is_speaking = False  # True when client is actively sending bot TTS audio to Twilio for the current_tts_item_id
        self.response_in_progress = False  # True from response.created until response.done (or cancellation)
        self.current_tts_item_id: Optional[str] = None  # The item_id of the assistant message whose audio is currently expected/playing
        self.llm_done_for_current_item: bool = False # True when response.output_item.done received for current_tts_item_id
        self.last_assistant_item_id_for_truncate: Optional[str] = None # For truncate message
        self.response_audio_started_timestamp: Optional[float] = None # Timestamp when bot audio for an item started

        self.tts_interrupt_event = asyncio.Event() # Set to stop current TTS playback
        self.tts_interrupt_event.clear() # Start with it clear

        self.greeting_item_id: Optional[str] = None
        self._waiting_for_greeting_item_id: bool = False
        self.is_final_assistant_message_detected: bool = False
        
        self.guard_active: bool = False
        self.guard_until: float = 0.0
        self.echo_drain_until: float = 0.0

        self.call_sid: str | None = None
        self.stream_sid: str | None = None

        self.openai_ws: Optional[websockets.client.WebSocketClientProtocol] = None
        self.openai_receive_task: Optional[asyncio.Task] = None
        self.openai_session_ready = False # True after session.update is acked or first useful message

        self.nlu_barge_in_event = asyncio.Event() # Used to signal NLU/TTS to stop due to user speech
        self.is_sending_initial_greeting = False
        
        self._last_processed_final_transcript: str = "" # To avoid processing duplicate final transcripts
        self.pending_barge_in_transcript: Optional[str] = None
        
        self.utterance_id_counter: int = 0 # For generating unique mark names for Twilio
        
        self.current_tts_task: Optional[asyncio.Task] = None
        self.nlu_task: Optional[asyncio.Task] = None
        self.stt_task: Optional[asyncio.Task] = None

        self.history: List[Dict[str, Any]] = [{"role": "system", "content": self.system_message}]

        logger.info(
            ">>> ChineseAudioOpenAIHandler v%s initialised (OpenAI Realtime API) <<<", "0.0.2_portal_id_fix"
        )

    async def _connect_openai_realtime(self):
        if self.openai_ws and self.openai_ws.open:
            logger.info("OpenAI Realtime WebSocket already connected.")
            return True
        
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "OpenAI-Beta": self.OPENAI_BETA_HEADER,
        }
        
        try:
            logger.info(f"Attempting to connect to OpenAI Realtime API: {self.OPENAI_REALTIME_URL}")
            self.openai_ws = await websockets.connect(self.OPENAI_REALTIME_URL, extra_headers=headers)
            logger.info("Successfully connected to OpenAI Realtime WebSocket.")
            
            session_data = {
                **self.OPENAI_SESSION_CONFIG,
                "instructions": self.system_message, 
                "tools": [
                    ORDER_SUMMARY_TOOL_SCHEMA_CN_OPENAI,
                    CHECK_MENU_ITEM_TOOL_SCHEMA_CN_OPENAI,
                    RECOMMEND_DISHES_TOOL_SCHEMA_CN_OPENAI,
                    LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_CN_OPENAI
                ]
            }
            
            session_config_payload = {
                "type": "session.update",
                "session": session_data
            }

            await self._send_to_openai(session_config_payload)
            logger.info(f"Sent initial session.update to OpenAI Realtime API with instructions: {self.system_message[:50]}... and config: {json.dumps(self.OPENAI_SESSION_CONFIG)}")
            
            if self.openai_receive_task and not self.openai_receive_task.done():
                self.openai_receive_task.cancel()
            
            task_name = f"openai_realtime_receiver_{self.call_sid or 'uninit'}"
            self.openai_receive_task = asyncio.create_task(self._handle_openai_messages(), name=task_name)
            return True
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"Failed to connect to OpenAI Realtime API: Status {e.status_code} - {e.response.text if e.response else 'No response body'}", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to connect to OpenAI Realtime API: {e}", exc_info=True)
        
        self.openai_ws = None
        self.openai_session_ready = False
        return False

    async def _send_to_openai(self, message: Dict[str, Any]):
        if self.openai_ws and self.openai_ws.open:
            try:
                await self.openai_ws.send(json.dumps(message))
                if self.verbose_logging:
                    logger.debug(f"Sent to OpenAI: {json.dumps(message)[:200]}...")
            except Exception as e:
                logger.error(f"Error sending message to OpenAI: {e}", exc_info=True)
        else:
            logger.warning("Cannot send message to OpenAI: WebSocket not connected or not open.")

    async def _handle_openai_messages(self):
        if not self.openai_ws:
            logger.error("OpenAI WebSocket not available in _handle_openai_messages.")
            return
        
        logger.info(f"Starting to listen for messages from OpenAI Realtime API. Call {self.call_sid}")
        try:
            async for raw_message in self.openai_ws:
                if self._stop_event_handled: break
                try:
                    message_str = raw_message if isinstance(raw_message, str) else raw_message.decode('utf-8')
                    event_data = json.loads(message_str)

                    raw_event_type_for_debug = event_data.get("type")
                    if raw_event_type_for_debug:
                        logger.debug(f"RAW_OPENAI_EVENT_TYPE_RECEIVED: {raw_event_type_for_debug}, EventSnippet: {json.dumps(event_data, ensure_ascii=False)[:200]}...")
                    
                    if self.verbose_logging:
                        logger.debug(f"Received from OpenAI: {json.dumps(event_data)[:200]}...")

                    event_type = event_data.get("type")

                    if event_type == "session.created" or event_type == "session.updated":
                        logger.info(f"OpenAI Realtime session event '{event_type}'. Config: {event_data.get('session')}. Call {self.call_sid}")
                        self.openai_session_ready = True

                    elif event_type == "response.created":
                        self.response_in_progress = True
                        self.current_tts_item_id = None
                        self.is_speaking = False
                        self.llm_done_for_current_item = False
                        self.tts_interrupt_event.clear()
                        self.is_final_assistant_message_detected = False # Reset flag for new response
                        logger.info(f"OpenAI event: response.created. response_in_progress=True. States reset for new utterance. Call {self.call_sid}")
                    
                    elif event_type == "response.done":
                        self.response_in_progress = False
                        self.guard_active = True 
                        self.guard_until  = time.perf_counter() + (self.BARGE_GUARD_MS_CONFIG / 1000.0)
                        logger.info(f"OpenAI event: response.done. response_in_progress=False. Guard activated until {self.guard_until:.2f}. Call {self.call_sid}")

                        if self.is_final_assistant_message_detected:
                            logger.info(f"FINAL_MESSAGE_LOGIC: response.done received and final message was detected. Scheduling hangup. Call {self.call_sid}")
                            # The flag will be reset by _schedule_hangup
                            asyncio.create_task(self._schedule_hangup(delay_before_hangup=3.5)) # Increased delay
                        else:
                            logger.info(f"OpenAI event: response.done received, but no final message was detected for this turn. No hangup scheduled. Call {self.call_sid}")

                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        item_id = event_data.get("item_id")
                        transcript = event_data.get("transcript", "").strip()
                        if transcript:
                            logger.info(f"USER_TRANSCRIPT (item_id: {item_id}): '{transcript}'. Call {self.call_sid}")
                            await self._process_stt_result(transcript)

                    elif event_type == "input_audio_buffer.speech_started":
                        logger.info(f"OpenAI VAD: Speech started. Call {self.call_sid}")
                        await self._handle_speech_started_event()
                    
                    elif event_type == "input_audio_buffer.speech_ended":
                         logger.info(f"OpenAI VAD: Speech ended. Call {self.call_sid}")

                    elif event_type == "response.audio.delta":
                        item_id_of_audio = event_data.get("item_id")
                        logger.info(f"AUDIO_DELTA_EVENT_RECEIVED: item_id={item_id_of_audio}, current_tts_item_id={self.current_tts_item_id}, has_audio_payload={bool(event_data.get('delta'))}. Call {self.call_sid}")
                        # logger.debug(f"RAW_AUDIO_DELTA_RECEIVED: item_id={item_id_of_audio}, current_tts_item_id={self.current_tts_item_id}, is_speaking={self.is_speaking}, response_in_progress={self.response_in_progress}, tts_interrupt_set={self.tts_interrupt_event.is_set()}")

                        if self.tts_interrupt_event.is_set():
                            if self.verbose_logging:
                                logger.info(f"AUDIO_DELTA: Dropping chunk for interrupted item {item_id_of_audio} (current: {self.current_tts_item_id}). Call {self.call_sid}")
                            continue
                        
                        audio_payload_b64 = event_data.get("delta")
                        # item_id_of_audio = event_data.get("item_id") # Already retrieved

                        if not item_id_of_audio:
                            logger.warning("AUDIO_DELTA: Received audio delta without an item_id. Discarding.")
                            continue

                        if self.current_tts_item_id != item_id_of_audio:
                            if self.response_in_progress:
                                logger.info(f"AUDIO_DELTA: New item audio detected. Switching current_tts_item_id from {self.current_tts_item_id} to {item_id_of_audio}. Call {self.call_sid}")
                                self.current_tts_item_id = item_id_of_audio
                                self.is_speaking = False
                                self.llm_done_for_current_item = False
                                self.tts_interrupt_event.clear()
                            else:
                                logger.warning(f"AUDIO_DELTA: Received for item {item_id_of_audio} but no response_in_progress and not matching current_tts_item_id {self.current_tts_item_id}. Discarding. Call {self.call_sid}")
                                continue
                        
                        if item_id_of_audio == self.current_tts_item_id:
                            if not self.is_speaking:
                                self.is_speaking = True
                                self.response_audio_started_timestamp = time.perf_counter()
                                self.last_assistant_item_id_for_truncate = self.current_tts_item_id
                                logger.info(f"SPEECH_STATE: is_speaking set to TRUE for item {self.current_tts_item_id} (first audio.delta) at {self.response_audio_started_timestamp:.4f}. Call {self.call_sid}")
                            
                            if audio_payload_b64 and self.twilio_ws and self.stream_sid:
                                await self.twilio_ws.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": self.stream_sid,
                                    "media": {"payload": audio_payload_b64, "track": "outbound"}
                                }))
                                logger.info(f"AUDIO_DELTA_SENT_TO_TWILIO: item_id={item_id_of_audio}. Call {self.call_sid}")
                                # if self.verbose_logging: # Verbose logging can still provide more details if needed
                                #     logger.debug(f"OpenAI TTS: Sent audio delta for {item_id_of_audio} to Twilio (verbose). Call {self.call_sid}")

                    elif event_type == "response.output_item.done":
                        item = event_data.get("item", {})
                        item_id = item.get("id")
                        role = item.get("role")
                        item_type = item.get("type")

                        logger.info(f"OpenAI NLU/Response (Realtime): response.output_item.done received. Item ID: {item_id}, Role: {role}, Type: {item_type}. Call {self.call_sid}")

                        if role == "assistant" and item_type == "message":
                            logger.info(f"SPEECH_STATE: LLM done for assistant item {item_id}. Preparing for its audio. Call {self.call_sid}")
                            self.current_tts_item_id = item_id
                            self.llm_done_for_current_item = True
                            logger.info(f"RAW_ASSISTANT_ITEM_DONE (item_id: {item_id}): {json.dumps(item, ensure_ascii=False)}")

                            if self._waiting_for_greeting_item_id and self.greeting_item_id is None:
                                self.greeting_item_id = item_id
                                self._waiting_for_greeting_item_id = False
                                logger.info(f"Captured initial greeting_item_id: {self.greeting_item_id}. Call {self.call_sid}")
                            
                            if self.is_sending_initial_greeting and item_id == self.greeting_item_id:
                                self.is_sending_initial_greeting = False
                                logger.info(f"Initial greeting (item_id: {item_id}) LLM text generation is done. is_sending_initial_greeting set to False. Call {self.call_sid}")
                            
                            content_array = item.get("content", [])
                            full_text_response = ""
                            for content_item_content in content_array:
                                content_type = content_item_content.get("type")
                                if content_type == "text":
                                    full_text_response += content_item_content.get("text", "")
                                elif content_type == "audio":
                                    full_text_response += content_item_content.get("transcript", "")
                            full_text_response = full_text_response.strip()
                            
                            logger.info(f"BOT_TRANSCRIPT (item_id: {item_id}): '{full_text_response}'. Call {self.call_sid}")
                            if full_text_response:
                                self.history.append({"role": "assistant", "content": full_text_response})

                                # Check for final message pattern
                                final_message_keywords = ["再见！", "再見！"] # Goodbye in simplified and traditional
                                if any(keyword in full_text_response for keyword in final_message_keywords):
                                    logger.info(f"FINAL_MESSAGE_LOGIC: Detected final assistant message: '{full_text_response}'. Setting flag. Call {self.call_sid}")
                                    self.is_final_assistant_message_detected = True
                                else:
                                    # Ensure it's reset if a new assistant message is not the final one
                                    # This might be redundant if response.created resets it, but safe.
                                    self.is_final_assistant_message_detected = False


                            if not self.tts_interrupt_event.is_set():
                                 logger.info(f"SPEECH_STATE: LLM done for item {self.current_tts_item_id}. is_speaking is {self.is_speaking}. tts_interrupt is {self.tts_interrupt_event.is_set()}. Starting natural ender timer. Call {self.call_sid}")
                                 asyncio.create_task(self._natural_speech_ender_timer(item_id, 1.5))
                            else:
                                 logger.info(f"SPEECH_STATE: LLM done for item {self.current_tts_item_id}, but tts_interrupt_event is SET. Not starting natural ender timer. Call {self.call_sid}")
                        
                        elif item_type == "function_call":
                            logger.info(f"OpenAI NLU (Realtime): Function call request: {item}. Call {self.call_sid}")
                            tool_result_content = await self._tool_exec(item)
                            logger.info(f"OpenAI Function Call Result to be sent: {tool_result_content}. Call {self.call_sid}")
                            
                            self.is_speaking = False
                            self.llm_done_for_current_item = False
                            self.tts_interrupt_event.clear()

                            await self._send_to_openai({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": item.get("call_id"),
                                    "output": tool_result_content,
                                },
                            })
                            await self._send_to_openai({"type": "response.create"})
                        
                    elif event_type == "error":
                        logger.error(f"Error from OpenAI Realtime API: {event_data.get('message', 'Unknown error')}. Details: {event_data}")

                except json.JSONDecodeError:
                    logger.error(f"Failed to parse JSON from OpenAI: {message_str}", exc_info=True)
                except Exception as e:
                    logger.error(f"Error processing message from OpenAI: {e}. Message: {message_str}", exc_info=True)
        
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"OpenAI Realtime WebSocket connection closed: Code {e.code}, Reason: {e.reason}. Call {self.call_sid}")
        except asyncio.CancelledError:
            logger.info(f"OpenAI message listener task cancelled. Call {self.call_sid}")
        except Exception as e:
            logger.error(f"Exception in OpenAI message listener: {e}. Call {self.call_sid}", exc_info=True)
        finally:
            logger.info(f"OpenAI message listener task finished. Call {self.call_sid}")
            self.openai_session_ready = False
            if self.openai_ws and self.openai_ws.open:
                await self.openai_ws.close()
            self.openai_ws = None

    async def _natural_speech_ender_timer(self, item_id_when_timer_started: str, delay_seconds: float):
        await asyncio.sleep(delay_seconds)
        if self.is_speaking and self.current_tts_item_id == item_id_when_timer_started and self.llm_done_for_current_item and not self.tts_interrupt_event.is_set():
            logger.info(f"SPEECH_STATE: Natural speech end timer expired for {item_id_when_timer_started}. Setting is_speaking=False. Call {self.call_sid}")
            self.is_speaking = False
            # Reset these as the bot is no longer considered to be actively speaking this item
            self.last_assistant_item_id_for_truncate = None 
            self.response_audio_started_timestamp = None
            
            # Hangup logic is now primarily triggered by response.done if it's a final message.
            # This timer just cleans up the is_speaking state.

        elif self.current_tts_item_id == item_id_when_timer_started: # Timer expired but conditions changed
             logger.info(f"SPEECH_STATE: Natural speech end timer expired for {item_id_when_timer_started}, but conditions not met to set is_speaking=False (is_speaking: {self.is_speaking}, llm_done: {self.llm_done_for_current_item}, interrupt_set: {self.tts_interrupt_event.is_set()}). Call {self.call_sid}")

    async def _schedule_hangup(self, delay_before_hangup: float = 3.5): # Default delay increased
        if self.call_sid and not self._stop_event_handled:
            logger.info(f"Scheduling hangup for call {self.call_sid} in {delay_before_hangup} seconds because final message was indicated.")
            # Reset the flag immediately to prevent re-triggering if other events occur
            # or if this method is somehow called again before cleanup.
            self.is_final_assistant_message_detected = False 
            
            await asyncio.sleep(delay_before_hangup)
            if not self._stop_event_handled: # Re-check as cleanup might have started
                logger.info(f"Executing scheduled hangup for call {self.call_sid}")
                try:
                    result = end_call(self.call_sid)
                    logger.info(f"Hangup result for call {self.call_sid}: {result}")
                    # Twilio 'stop' event will trigger full cleanup.
                except Exception as e_hangup:
                    logger.error(f"Error during scheduled hangup for call {self.call_sid}: {e_hangup}", exc_info=True)
            else:
                logger.info(f"Hangup for call {self.call_sid} was scheduled but stop event already handled.")
        else:
            logger.warning(f"Cannot schedule hangup: call_sid is {self.call_sid} or stop event already handled ({self._stop_event_handled}).")
            # Still reset the flag if we couldn't schedule, to ensure clean state.
            if self.is_final_assistant_message_detected: # Should have been reset already, but as a safeguard
                 self.is_final_assistant_message_detected = False


    async def _handle_speech_started_event(self):
        current_time = time.perf_counter()
        logger.info(f"SPEECH_HANDLER: Entered. is_speaking: {self.is_speaking}, response_in_progress: {self.response_in_progress}, guard_active: {self.guard_active}, guard_until: {self.guard_until:.2f}, current_time: {current_time:.2f}, current_tts_item_id: {self.current_tts_item_id}. Call {self.call_sid}")
        
        if self.is_speaking: 
            logger.info(f"SPEECH_HANDLER (BARGE_IN_ACTIVE): User speech detected while bot is_speaking=True for item {self.current_tts_item_id}. Cancelling playback. Call {self.call_sid}")
            await self._cancel_playback()
        else:
            is_within_guard_period = self.guard_active and current_time < self.guard_until
            if is_within_guard_period:
                logger.info(f"SPEECH_HANDLER (BARGE_IN_GUARDED): User speech detected while bot NOT speaking, but within guard period. Ignoring. Call {self.call_sid}")
                return 
            logger.info(f"SPEECH_HANDLER (USER_TURN or PRE_SPEECH_INTERRUPT): User speech detected. is_speaking=False. Normal user turn or OpenAI handles interrupt. Call {self.call_sid}")
            if self.response_in_progress:
                logger.info(f"SPEECH_HANDLER: User spoke while response pending but bot not yet audibly speaking. Relying on OpenAI server-side interrupt_response:True.")
    
    async def _process_stt_result(self, transcript: str):
        if self._stop_event_handled or self._last_processed_final_transcript == transcript:
            return
        self._last_processed_final_transcript = transcript
        self.history.append({"role": "user", "content": transcript})
        logger.info(f"STT_RESULT_PROCESSED: Transcript '{transcript[:30]}...' logged. NLU is handled by Realtime API. Call {self.call_sid}")
        if self.pending_barge_in_transcript:
            self.pending_barge_in_transcript = None
        if self.nlu_barge_in_event.is_set():
            self.nlu_barge_in_event.clear()

    async def queue_from_twilio(self, mulaw_audio_chunk_b64: str):
        if self._stop_event_handled or not self.openai_session_ready or not self.openai_ws:
            return
        current_time = time.perf_counter()
        if current_time < self.echo_drain_until:
            if self.verbose_logging: logger.debug(f"ECHO_DRAIN: Ignoring Twilio audio frame. Call {self.call_sid}")
            return
        try:
            audio_bytes = base64.b64decode(mulaw_audio_chunk_b64)
            payload_to_openai = base64.b64encode(audio_bytes).decode('utf-8') 
            await self._send_to_openai({
                "type": "input_audio_buffer.append",
                "audio": payload_to_openai 
            })
        except Exception as e:
            logger.error(f"Error processing/sending Twilio audio to OpenAI: {e}", exc_info=True)

    async def _speak_via_openai_realtime(self, text_to_speak: str, mark_name_for_twilio: Optional[str]):
        if self._stop_event_handled or not self.openai_session_ready:
            return
        logger.info(f"Requesting OpenAI Realtime to speak: '{text_to_speak[:30]}...'. Call {self.call_sid}")
        self.is_speaking = True
        self.tts_interrupt_event.clear()
        self.nlu_barge_in_event.clear()
        await self._send_to_openai({
            "type": "conversation.item.create",
            "item": {
                "type": "message", 
                "role": "assistant", 
                "content": [{"type": "text", "text": text_to_speak}]
            }
        })
        await self._send_to_openai({"type": "response.create"})

    async def _cancel_playback(self):
        entry_time = time.perf_counter()
        logger.info(f"CANCEL_PLAYBACK: Initiating for current_tts_item_id: {self.current_tts_item_id} at {entry_time:.4f}. Call {self.call_sid}")
        self.tts_interrupt_event.set()
        self.is_speaking = False
        self.response_in_progress = False
        self.llm_done_for_current_item = False

        interrupted_item_id_for_truncate = self.last_assistant_item_id_for_truncate
        if interrupted_item_id_for_truncate and self.openai_ws and self.openai_ws.open:
            audio_end_ms = 0
            if self.response_audio_started_timestamp:
                elapsed_since_audio_start = time.perf_counter() - self.response_audio_started_timestamp
                audio_end_ms = max(0, int(elapsed_since_audio_start * 1000))
            truncate_message = {
                "type": "conversation.item.truncate",
                "item_id": interrupted_item_id_for_truncate,
                "content_index": 0,
                "audio_end_ms": audio_end_ms 
            }
            await self._send_to_openai(truncate_message)
            logger.info(f"CANCEL_PLAYBACK: Sent conversation.item.truncate to OpenAI for item {interrupted_item_id_for_truncate} with audio_end_ms: {audio_end_ms}. Call {self.call_sid}")

        if self.twilio_ws and self.stream_sid:
            clear_message = {"event": "clear", "streamSid": self.stream_sid}
            try:
                await self.twilio_ws.send_text(json.dumps(clear_message))
                logger.info(f"CANCEL_PLAYBACK: Sent 'clear' event to Twilio for stream {self.stream_sid}. Call {self.call_sid}")
            except Exception as e_clear:
                logger.error(f"CANCEL_PLAYBACK: Error sending 'clear' event to Twilio: {e_clear}. Call {self.call_sid}")

        self.echo_drain_until = time.perf_counter() + (self.ECHO_DRAIN_MS_CONFIG / 1000.0)
        interrupted_item_id_log = self.current_tts_item_id
        self.current_tts_item_id = None
        self.last_assistant_item_id_for_truncate = None
        self.response_audio_started_timestamp = None
        logger.info(f"CANCEL_PLAYBACK: Local flags updated for interrupted_item_id ({interrupted_item_id_log}). TTS interrupt SET. is_speaking=False, response_in_progress=False. Echo drain until {self.echo_drain_until:.2f}. Call {self.call_sid}")

    async def on_twilio_message(self, raw_message_str: str):
        if self._stop_event_handled: return
        try:
            event_data = json.loads(raw_message_str)
            event_type = event_data.get("event")

            if event_type == "start":
                self.stream_sid = event_data.get("streamSid")
                self.call_sid = event_data.get("start", {}).get("callSid")
                logger.info(f"Call started: SID {self.call_sid}, Stream SID {self.stream_sid}")
                if not await self._connect_openai_realtime():
                    logger.error(f"Failed to establish OpenAI Realtime connection. Closing Twilio WS. Call {self.call_sid}")
                    await self.twilio_ws.close(code=1011, reason="OpenAI connection failed")
                    return

                if self.send_welcome:
                    logger.info(f"Waiting for OpenAI session to be ready before sending welcome message. Call {self.call_sid}")
                    wait_start_time = time.monotonic()
                    while not self.openai_session_ready:
                        await asyncio.sleep(0.05)
                        if time.monotonic() - wait_start_time > 10:
                            logger.error(f"Timeout waiting for OpenAI session to become ready. Call {self.call_sid}")
                            await self.cleanup()
                            return
                    
                    logger.info(f"OpenAI session ready. Sending explicit assistant greeting. Call {self.call_sid}")
                    self.is_sending_initial_greeting = True
                    self._waiting_for_greeting_item_id = True
                    self.current_tts_item_id = None
                    self.is_speaking = False
                    self.llm_done_for_current_item = False
                    self.tts_interrupt_event.clear() 
                    
                    greeting_text = "你应该先和顾客打招呼!" # This can be made configurable
                    logger.info(f"Sending initial assistant greeting to OpenAI: '{greeting_text}'. Call {self.call_sid}")
                    await self._send_to_openai({
                        "type": "conversation.item.create",
                        "item": {"type": "message", "role": "assistant", "content": [{"type": "text", "text": greeting_text}]}
                    })
                    await self._send_to_openai({"type": "response.create"})
            
            elif event_type == "media":
                media_payload_b64 = event_data.get("media", {}).get("payload")
                if media_payload_b64:
                    await self.queue_from_twilio(media_payload_b64)
            
            elif event_type == "mark":
                mark_name_received = event_data.get("mark", {}).get("name")
                logger.info(f"Received Twilio mark: {mark_name_received}. Call {self.call_sid}")
                if self.is_speaking:
                    logger.info(f"MARK_EVENT: Twilio mark '{mark_name_received}' received. Assuming bot speech segment potentially finished. Call {self.call_sid}")
                if mark_name_received == "end_of_initial_greeting":
                     self.is_sending_initial_greeting = False
                     self.is_speaking = False
                     self.guard_active = False
                     logger.info(f"MARK_EVENT: Initial greeting confirmed finished by Twilio mark. Call {self.call_sid}")
                if self.pending_barge_in_transcript:
                    logger.info(f"MARK_EVENT: Processing pending transcript '{self.pending_barge_in_transcript[:30]}...' after Twilio mark. Call {self.call_sid}")
                    await self._send_to_openai({
                        "type": "conversation.item.create",
                        "item": {"type": "user_message", "text": self.pending_barge_in_transcript}
                    })
                    await self._send_to_openai({"type": "response.create"})
                    self.pending_barge_in_transcript = None
            
            elif event_type == "stop":
                logger.info(f"Received Twilio stop event for call {self.call_sid}. Cleaning up.")
                await self.cleanup()

        except WebSocketDisconnect:
            logger.warning(f"Twilio WebSocket disconnected for call {self.call_sid}.")
            await self.cleanup()
        except Exception as exc:
            logger.error(f"Error processing Twilio message for call {self.call_sid}: {exc}", exc_info=True)

    async def _tool_exec(self, function_call_item: dict) -> str:
        tool_name = function_call_item.get("name")
        tool_args_str = function_call_item.get("arguments", "{}")
        logger.info(f"Executing tool '{tool_name}' with args string: {tool_args_str}. Client ID: {self.client_id}. Call {self.call_sid}")
        # The actual logic is now in chinese_tool_logic.py
        return await execute_tool_logic(
            function_call_item=function_call_item,
            client_id=self.client_id,
            call_sid=self.call_sid,
            caller_phone=self.caller_phone,
            loop=self.loop # Pass the event loop
        )

    async def cleanup(self):
        if self._stop_event_handled: return
        logger.info(f"Initiating cleanup for ChineseAudioOpenAIHandler (CallSid: {self.call_sid})")
        self._stop_event_handled = True
        self.nlu_barge_in_event.set()
        self.tts_interrupt_event.set()

        tasks_to_cancel: List[Optional[asyncio.Task]] = [
            self.current_tts_task, self.nlu_task, self.stt_task, self.openai_receive_task,
        ]
        
        if self.openai_ws and self.openai_ws.open:
            try:
                await self.openai_ws.close(code=1000, reason="Client cleanup")
                logger.info(f"OpenAI Realtime WebSocket connection closed. Call {self.call_sid}")
            except Exception as e_openai_close:
                logger.error(f"Error closing OpenAI Realtime WebSocket: {e_openai_close}. Call {self.call_sid}")
        self.openai_ws = None

        valid_tasks_to_await = [t for t in tasks_to_cancel if t is not None and not t.done()]
        for task in valid_tasks_to_await:
            task.cancel()
        
        if valid_tasks_to_await:
            results = await asyncio.gather(*valid_tasks_to_await, return_exceptions=True)
            for i, result in enumerate(results):
                task_name = valid_tasks_to_await[i].get_name() if hasattr(valid_tasks_to_await[i], 'get_name') else f"Task-{i}"
                if isinstance(result, asyncio.CancelledError):
                    logger.info(f"Task '{task_name}' cancelled successfully during cleanup. Call {self.call_sid}")
                elif isinstance(result, Exception):
                    logger.error(f"Exception in task '{task_name}' during cleanup: {result}. Call {self.call_sid}", exc_info=result)
        
        if self.twilio_ws and self.twilio_ws.client_state == WebSocketState.CONNECTED:
            try:
                await self.twilio_ws.close(code=1000, reason="Handler cleanup")
            except Exception: pass
        logger.info(f"ChineseAudioOpenAIHandler cleanup completed for CallSid: {self.call_sid}.")

# Helper for WebSocketState
try:
    from starlette.websockets import WebSocketState
except ImportError:
    class WebSocketState: CONNECTING = 0; CONNECTED = 1; DISCONNECTED = 2
