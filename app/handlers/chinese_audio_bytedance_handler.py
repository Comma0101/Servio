from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
import struct
import functools
from typing import Any, Dict, List, Optional, AsyncGenerator

import audioop
from fastapi import WebSocket, WebSocketDisconnect # type: ignore
import websockets # For Bytedance WebSocket client connections
from thefuzz import process as fuzz_process, fuzz # For fuzzy string matching

from app.core.barge_in import BargeInController, BargeInState, TelephonyAdapter, TTSAdapter
from app.services.bytedance.stt_service import BytedanceSTTService
from app.services.bytedance.tts_service import BytedanceTTSService
from app.services.nlu_processor import NLUProcessor
from app.handlers.common_tool_defs import (
    ORDER_SUMMARY_TOOL_SCHEMA_CN_BYTEDANCE,
    CHECK_MENU_ITEM_TOOL_SCHEMA_CN_BYTEDANCE,
    RECOMMEND_DISHES_TOOL_SCHEMA_CN_BYTEDANCE,
    LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_CN_BYTEDANCE,
    GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_CN_BYTEDANCE, # Added new tool
    CATEGORY_ALIASES_CN,
    DISHES_ALIASES_CN
)
from app.services.database_service import save_order_details, save_call_start, save_call_end, save_utterance
from app.utils.twilio import send_sms, end_call
from app.utils.database import upload_audio_to_s3
from app.utils.constants import get_restaurant_config
from app.constants import THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT, THIRTY_NINE_MILES_PORTAL_ID_DINE_IN
from app.config import settings
from app.utils.thirty_nine_miles import (
    find_dish_by_chinese_name,
    get_extracted_dishes,
    add_order as add_thirty_nine_miles_order,
    ApiOrderDto,
    ApiOrderItem,
    TextStore,
    LangCode
)
from app.handlers.chinese_tool_logic import execute_tool_logic

logger = logging.getLogger(__name__)

FINAL_HANGUP_MARK_NAME = "final_hangup_mark"

class ChineseAudioByteDanceHandler(TelephonyAdapter, TTSAdapter):
    MAX_HISTORY_MESSAGES = 100
    TARGET_TWILIO_BUFFER_SIZE_BYTES = 640

    def __init__(
        self,
        websocket: WebSocket,
        openai_api_key: str,
        bytedance_stt_app_key: str,
        bytedance_stt_access_key: str,
        bytedance_stt_resource_id: str,
        bytedance_tts_appid: str,
        bytedance_tts_token: str,
        *,
        send_welcome_message: bool = True,
        caller_phone: Optional[str] = None,
        client_id: Optional[str] = None,
        system_message: Optional[str] = None,
        verbose_logging: bool = False,
    ) -> None:
        self.twilio_ws = websocket
        self.loop = asyncio.get_event_loop()
        self._stop_event_handled = False

        # Core properties
        self.call_sid: Optional[str] = None
        self.stream_sid: Optional[str] = None
        self.caller_phone = caller_phone
        self.client_id = client_id
        self.system_message = system_message or "你是餐馆电话助理，帮助顾客点餐。"
        self.verbose_logging = verbose_logging
        self.send_welcome = send_welcome_message
        
        # Services
        self.stt_service = BytedanceSTTService(
            app_key=bytedance_stt_app_key,
            access_key=bytedance_stt_access_key,
            resource_id=bytedance_stt_resource_id,
            caller_phone=self.caller_phone,
            call_sid=self.call_sid
        )
        self.tts_service = BytedanceTTSService(
            app_id=bytedance_tts_appid,
            auth_token=bytedance_tts_token,
            caller_phone=self.caller_phone,
            call_sid=self.call_sid
        )
        self.nlu_processor = NLUProcessor(
            openai_api_key=openai_api_key,
            client_id=self.client_id,
            call_sid=self.call_sid,
            caller_phone=self.caller_phone
        )

        # State Management
        self.history: List[Dict[str, Any]] = [{"role": "system", "content": self.system_message}]
        self.stt_processing_lock = asyncio.Lock()
        self.last_handled_definite_stt_transcript: Optional[str] = None
        self.stt_resample_state_cv = None
        self.twilio_audio_buffer = b''
        self.nlu_commit_task: Optional[asyncio.Task] = None
        self.complete_audio_buffer = bytearray()
        self.connection_closed_event = asyncio.Event()
        
        # Barge-in
        self.barge_controller = BargeInController(tel=self, tts=self, call_sid=self.call_sid)
        
        logger.info(">>> ChineseAudioByteDanceHandler initialised <<<")

    async def clear_down(self) -> None:
        if self.twilio_ws and self.stream_sid and self.twilio_ws.client_state == WebSocketState.CONNECTED:
            logger.info(f"TelephonyAdapter: Sending 'clear' event to Twilio for stream {self.stream_sid}. Call {self.call_sid}")
            try:
                await self.twilio_ws.send_text(json.dumps({"event": "clear", "streamSid": self.stream_sid}))
                logger.info(f"TelephonyAdapter: Twilio 'clear' event sent successfully for call {self.call_sid}.")
            except Exception as e:
                logger.error(f"TelephonyAdapter: Failed to send 'clear' event to Twilio for call {self.call_sid}: {e}", exc_info=True)
        else:
            logger.warning(f"TelephonyAdapter: Cannot send Twilio 'clear' event for call {self.call_sid} (Twilio WS not connected or stream_sid missing).")

    async def provider_interrupt(self) -> None:
        logger.info(f"TTSAdapter: Attempting to interrupt Bytedance TTS for call {self.call_sid}.")
        await self.tts_service.interrupt()

    def _normalize_text_for_deduplication(self, text: Optional[str]) -> str:
        if text is None: return ""
        text = text.strip()
        if text and text[-1] in "。？！?!.": text = text[:-1].strip() 
        return text

    async def _speak(self, text: str, latency_tracker: dict = None, transcript: str = "", end_of_speech_time: Optional[float] = None, is_final_message: bool = False):
        """Sends text to the TTS service and streams the audio to Twilio."""
        if self._stop_event_handled:
            return

        req_id = str(uuid.uuid4())
        logger.info(f"Handler: Speaking TTS for reqid {req_id}: '{text[:50]}...'")
        
        # Use the new TTS service
        audio_generator, request_time = await self.tts_service.speak(text)
        if latency_tracker:
            latency_tracker['tts_request_time'] = request_time
        
        # Inform the barge-in controller that TTS is starting
        await self.barge_controller.on_tts_audio_start(req_id)
        
        first_chunk = True
        async for audio_chunk_mulaw in audio_generator:
            if first_chunk and latency_tracker and end_of_speech_time:
                first_chunk = False
                # This is the moment the user hears the first sound.
                total_response_latency_ms = (time.perf_counter() - end_of_speech_time) * 1000

                # Keep old calculation for breakdown details
                ttfa_ms = (time.perf_counter() - latency_tracker['tts_request_time']) * 1000
                latency_tracker['tts_ttfa'] = ttfa_ms
                stt_latency = latency_tracker.get('stt', 0) * 1000
                nlu_latency = latency_tracker.get('total_nlu_turn', 0) * 1000

                # Log the final, consolidated latency metric.
                logger.info(
                    f"RESPONSE_LATENCY CallSid: {self.call_sid}, Transcript: '{transcript}', "
                    f"TotalLatency: {total_response_latency_ms:.2f} ms, "
                    f"Breakdown: {{'stt': {stt_latency:.2f}, 'nlu': {nlu_latency:.2f}, 'tts_ttfa': {ttfa_ms:.2f}}}"
                )

            if self._stop_event_handled or self.tts_service.tts_interrupt_event.is_set():
                logger.info(f"Handler: TTS for reqid {req_id} was interrupted or stopped.")
                break
            
            self.complete_audio_buffer.extend(audio_chunk_mulaw)
            if self.twilio_ws and self.stream_sid:
                payload = base64.b64encode(audio_chunk_mulaw).decode('utf-8')
                await self.twilio_ws.send_text(json.dumps({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": payload, "track": "outbound"}
                }))

        logger.info(f"Handler: Finished speaking TTS for reqid {req_id}.")
        await self.barge_controller.on_tts_audio_end(req_id)
        
        # Send a final mark to Twilio to signal completion
        if self.twilio_ws and self.stream_sid:
            mark_name = f"tts_stream_ended_{req_id}"
            await self.twilio_ws.send_text(json.dumps({
                "event": "mark", "streamSid": self.stream_sid, "mark": {"name": mark_name}
            }))
        
        if is_final_message and self.twilio_ws and self.stream_sid:
            logger.info(f"Sending final hangup mark to Twilio: {FINAL_HANGUP_MARK_NAME}")
            await self.twilio_ws.send_text(json.dumps({
                "event": "mark", "streamSid": self.stream_sid, "mark": {"name": FINAL_HANGUP_MARK_NAME}
            }))

    # Add this new method
    async def _process_final_transcript(self, transcript: str, latency_tracker: dict, end_of_speech_time: float):
        """Processes a single final transcript using the NLU service."""
        turn_start_time = time.perf_counter()
        normalized_transcript = transcript.strip()
        if self._stop_event_handled or not normalized_transcript:
            return

        # Barge-in must be checked BEFORE the lock to prevent deadlocks.
        current_barge_in_state = self.barge_controller.get_current_state()
        if current_barge_in_state in (BargeInState.TTS_PLAY, BargeInState.WAIT_MARK):
            logger.info(f"Handler: Barge-in detected on transcript in state {current_barge_in_state.name}. Interrupting TTS.")
            await self.barge_controller.speech_detected()

            # Cancel any pending commit timer to prevent it from firing after the barge-in.
            if self.nlu_commit_task and not self.nlu_commit_task.done():
                logger.info("Cancelling pending NLU commit timer due to barge-in.")
                self.nlu_commit_task.cancel()
            
            # Pop the interrupted assistant message from history to prevent re-responding.
            if self.history and self.history[-1].get("role") == "assistant":
                interrupted_msg = self.history.pop()
                logger.info(f"Popped interrupted assistant message from history: {interrupted_msg.get('content', '')[:50]}...")


        async with self.stt_processing_lock:
            if self._stop_event_handled:
                return

            # Preserve deduplication logic
            fully_normalized_current = self._normalize_text_for_deduplication(normalized_transcript)
            fully_normalized_last = self._normalize_text_for_deduplication(self.last_handled_definite_stt_transcript)
            if fully_normalized_current and fully_normalized_current == fully_normalized_last:
                logger.warning(f"Handler: Duplicate final transcript detected. Ignoring: '{fully_normalized_current}'")
                return
            
            logger.info(f"Handler: Processing final transcript for NLU: '{normalized_transcript}'")
            
            await save_utterance(self.call_sid, "user", normalized_transcript)
            
            response_text, new_history, nlu_latency = await self.nlu_processor.get_response(
                transcript=normalized_transcript,
                history=self.history
            )
            latency_tracker.update(nlu_latency)

            # Re-check barge-in state after the NLU call to prevent "zombie" responses
            if self.barge_controller.get_current_state() != BargeInState.IDLE:
                logger.warning(f"Handler: Barge-in detected after NLU response. Discarding response for '{normalized_transcript}'")
                return
            
            if response_text:
                await save_utterance(self.call_sid, "agent", response_text)
                self.history.extend(new_history)
                self.last_handled_definite_stt_transcript = normalized_transcript
                
                # Log the total turn time before speaking
                total_turn_time_ms = (time.perf_counter() - turn_start_time) * 1000
                latency_tracker['total_turn'] = total_turn_time_ms
                
                is_final = any(kw in response_text for kw in ["再见", "再見"])
                await self._speak(response_text, latency_tracker, normalized_transcript, end_of_speech_time, is_final_message=is_final)

            else:
                logger.warning(f"Handler: NLU processor returned no response for: '{normalized_transcript}'")

    # Add this new method
    async def _run_orchestration_loop(self):
        """The main orchestration loop using the new services."""
        logger.info(f"Handler: Starting orchestration loop for call {self.call_sid}")
        
        candidate_transcript = ""
        # This latency tracker is now only for turns that are marked as 'final' by the STT service.
        final_turn_latency_tracker = {}

        async def commit_after_delay(transcript, stt_latency_for_commit):
            """Processes a transcript after a delay, capturing the specific STT latency for the turn."""
            await asyncio.sleep(0.4)
            end_of_speech_time = time.perf_counter()
            logger.info(f"NLU Commit Timer: Fired for transcript: '{transcript}'")
            # Create a fresh tracker for this timer-based turn to avoid race conditions.
            timer_latency_tracker = {'stt': stt_latency_for_commit}
            await self._process_final_transcript(transcript, timer_latency_tracker, end_of_speech_time)

        try:
            async for transcript, is_final, stt_latency in self.stt_service.transcripts():
                if self._stop_event_handled:
                    break
                
                # Always update the latency for the 'is_final' case.
                if stt_latency is not None:
                    final_turn_latency_tracker['stt'] = stt_latency

                # Normalize transcripts to decide if we have a meaningful update.
                normalized_new = self._normalize_text_for_deduplication(transcript)
                normalized_candidate = self._normalize_text_for_deduplication(candidate_transcript)

                # If the new transcript is not a significant change, do nothing.
                if normalized_new == normalized_candidate:
                    continue

                # If we have a new, better transcript, cancel the old commit task.
                if self.nlu_commit_task and not self.nlu_commit_task.done():
                    self.nlu_commit_task.cancel()

                candidate_transcript = transcript

                if is_final:
                    # If we get a final result, cancel any pending commit timer.
                    if self.nlu_commit_task and not self.nlu_commit_task.done():
                        logger.info("Final transcript received, cancelling pending NLU commit timer.")
                        self.nlu_commit_task.cancel()
                    
                    end_of_speech_time = time.perf_counter()
                    # If we get a final result, process it immediately using the main tracker.
                    await self._process_final_transcript(candidate_transcript, final_turn_latency_tracker, end_of_speech_time)
                    candidate_transcript = ""
                    final_turn_latency_tracker = {} # Reset for the next 'final' turn.
                else:
                    # If it's a new partial result, start the timer, passing the current STT latency.
                    self.nlu_commit_task = asyncio.create_task(commit_after_delay(candidate_transcript, stt_latency))

        except Exception as e:
            logger.error(f"Handler: Error in orchestration loop: {e}", exc_info=True)
        finally:
            if self.nlu_commit_task and not self.nlu_commit_task.done():
                self.nlu_commit_task.cancel()
            logger.info(f"Handler: Exiting orchestration loop for call {self.call_sid}")

    async def on_twilio_message(self, raw_message_str: str):
        if self._stop_event_handled: return
        try:
            event_data = json.loads(raw_message_str)
            event_type = event_data.get("event")

            if event_type == "start":
                self.stream_sid = event_data.get("streamSid")
                self.call_sid = event_data.get("start", {}).get("callSid")
                if self.call_sid:
                    self.barge_controller.call_sid = self.call_sid
                    # Also update SIDs in services
                    self.stt_service.call_sid = self.call_sid
                    self.tts_service.call_sid = self.call_sid
                    self.nlu_processor.call_sid = self.call_sid

                custom_params = event_data.get("start", {}).get("customParameters", {})
                self.caller_phone = custom_params.get("caller_phone", self.caller_phone)
                self.client_id = custom_params.get("client_id", self.client_id)
                self.stt_resample_state_cv = None
                logger.info(f"Call started: SID {self.call_sid}, Stream {self.stream_sid}, Caller {self.caller_phone}, Client {self.client_id}. BargeInController and services SIDs updated.")

                await save_call_start(self.call_sid, self.caller_phone)

                # Connect new services
                await self.stt_service.connect()
                await self.tts_service.connect()

                # Start the new orchestration loop
                asyncio.create_task(self._run_orchestration_loop())

                if self.send_welcome:
                    welcome_text = settings.CHINESE_WELCOME_MESSAGE
                    await save_utterance(self.call_sid, "agent", welcome_text)
                    if self.history[0]["role"] == "system":
                        self.history.insert(1, {"role": "assistant", "content": welcome_text})
                    else:
                        self.history.append({"role": "assistant", "content": welcome_text})
                    # Use the new speak method
                    await self._speak(welcome_text)

            elif event_type == "media":
                media_payload_b64 = event_data.get("media", {}).get("payload")
                if media_payload_b64:
                    raw_audio_chunk_mulaw = base64.b64decode(media_payload_b64)
                    self.complete_audio_buffer.extend(raw_audio_chunk_mulaw)
                    # Restore audio buffering and resampling from original functionality
                    self.twilio_audio_buffer += raw_audio_chunk_mulaw
                    while len(self.twilio_audio_buffer) >= self.TARGET_TWILIO_BUFFER_SIZE_BYTES:
                        chunk_to_process_mulaw = self.twilio_audio_buffer[:self.TARGET_TWILIO_BUFFER_SIZE_BYTES]
                        self.twilio_audio_buffer = self.twilio_audio_buffer[self.TARGET_TWILIO_BUFFER_SIZE_BYTES:]
                        
                        audio_chunk_pcm8k_s16le = audioop.ulaw2lin(chunk_to_process_mulaw, 2)
                        audio_chunk_pcm16k_s16le, self.stt_resample_state_cv = audioop.ratecv(
                            audio_chunk_pcm8k_s16le, 2, 1, 8000, 16000, self.stt_resample_state_cv
                        )
                        if audio_chunk_pcm16k_s16le:
                            await self.stt_service.send_audio(audio_chunk_pcm16k_s16le)

            elif event_type == "mark":
                mark_name = event_data.get('mark', {}).get('name')
                logger.info(f"Twilio mark received: {mark_name}. Call {self.call_sid}")
                if mark_name == FINAL_HANGUP_MARK_NAME:
                    logger.info("Final hangup mark received. Scheduling hangup.")
                    asyncio.create_task(self._schedule_hangup())
                else:
                    reqid_from_mark = None
                    if mark_name and mark_name.startswith("tts_stream_ended_"):
                        try:
                            reqid_from_mark = mark_name.split("tts_stream_ended_")[1]
                        except IndexError:
                            logger.warning(f"Could not parse reqid from mark name: {mark_name}. Call {self.call_sid}")

                    await self.barge_controller.on_telephony_ack(reqid_from_mark)

            elif event_type == "stop":
                logger.info(f"Twilio stop event for call {self.call_sid}. Cleaning up.")
                # Process and send any remaining audio in the buffer
                if self.twilio_audio_buffer:
                    remaining_pcm8k = audioop.ulaw2lin(self.twilio_audio_buffer, 2)
                    self.twilio_audio_buffer = b''
                    if remaining_pcm8k:
                        resampled_remaining, self.stt_resample_state_cv = audioop.ratecv(
                            remaining_pcm8k, 2, 1, 8000, 16000, self.stt_resample_state_cv
                        )
                        if resampled_remaining:
                            await self.stt_service.send_audio(resampled_remaining)
                
                # Flush the resampler
                if self.stt_resample_state_cv:
                    flushed, self.stt_resample_state_cv = audioop.ratecv(b'', 2, 1, 8000, 16000, self.stt_resample_state_cv)
                    if flushed:
                        await self.stt_service.send_audio(flushed)

                # Signal to the STT service that this is the final chunk
                await self.stt_service.send_audio(b'', is_last_chunk=True)
                # The cleanup method will handle closing the services
                await self.cleanup()

        except WebSocketDisconnect:
            logger.warning(f"Twilio WS disconnected for call {self.call_sid}.")
            await self.cleanup()
        except Exception as exc:
            logger.error(f"Error processing Twilio message for call {self.call_sid}: {exc}", exc_info=True)
            await self.cleanup()

    async def _schedule_hangup(self, delay_before_hangup: float = 1.5): 
        if self.call_sid and not self._stop_event_handled:
            logger.info(f"Scheduling hangup for call {self.call_sid} in {delay_before_hangup}s.")
            self.is_final_assistant_message_detected = False 
            await asyncio.sleep(delay_before_hangup)
            if not self._stop_event_handled:
                logger.info(f"Executing scheduled hangup for call {self.call_sid}")
                try: await self.loop.run_in_executor(None, functools.partial(end_call, self.call_sid))
                except Exception as e: logger.error(f"Error during scheduled hangup: {e}", exc_info=True)

    async def cleanup(self):
        if self._stop_event_handled:
            return
        self.connection_closed_event.set()
        logger.info(f"Cleaning up ChineseAudioByteDanceHandler (CallSid: {self.call_sid})")
        self._stop_event_handled = True

        audio_url = None
        if self.call_sid and self.complete_audio_buffer:
            try:
                logger.info(f"Uploading call audio to S3 for call_sid: {self.call_sid}, size: {len(self.complete_audio_buffer)} bytes")
                audio_url = await upload_audio_to_s3(self.call_sid, bytes(self.complete_audio_buffer))
                if audio_url:
                    logger.info(f"Successfully uploaded call audio to S3: {audio_url}")
                else:
                    logger.error("Failed to upload call audio to S3 - no URL returned")
            except Exception as e:
                logger.error(f"Error uploading audio to S3: {e}", exc_info=True)
        
        if self.call_sid:
            await save_call_end(self.call_sid, audio_url)

        # Close the services
        await self.stt_service.close()
        await self.tts_service.close()

        # Close the Twilio WebSocket connection
        if self.twilio_ws and self.twilio_ws.client_state == WebSocketState.CONNECTED:
            try:
                await self.twilio_ws.close(code=1000)
            except Exception as e:
                logger.error(f"Error closing Twilio WebSocket: {e}", exc_info=True)
        
        logger.info(f"Cleanup completed for CallSid: {self.call_sid}.")

    async def _tool_exec(self, function_call_item: dict) -> str:
        return await execute_tool_logic(
            function_call_item=function_call_item,
            client_id=self.client_id,
            call_sid=self.call_sid,
            caller_phone=self.caller_phone,
            loop=self.loop
        )

try:
    from starlette.websockets import WebSocketState
except ImportError:
    # Define a simple WebSocketState if starlette is not available (e.g. for testing)
    class WebSocketState:
        CONNECTING = 0
        CONNECTED = 1
        DISCONNECTED = 2
