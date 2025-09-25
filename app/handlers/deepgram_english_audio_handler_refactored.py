"""
Audio Handler - Process audio streams between Twilio and Deepgram
"""
import asyncio
import base64
import json
import logging
import functools
from fastapi import WebSocket
from typing import Optional, Dict, Any
import traceback
import time
import uuid

from app.services.deepgram_service import DeepgramService
from app.utils.twilio import redirect_call
from app.config import settings
from app.handlers.english_tool_logic import FINAL_AUDIO_MARK_NAME, handle_function_call
from app.utils.twilio import end_call
from app.services.call_state_service import remove_call_state, get_and_clear_next_tool, clear_next_tool, register_call, register_media_event, register_tts_started
from app.services.database_service import save_call_start, save_utterance, save_call_end
from app.utils.database import upload_audio_to_s3
from starlette.websockets import WebSocketState

from app.handlers.english_tool_logic import clean_text_for_tts
from app.handlers.combo_order_manager import combo_order_manager
from app.handlers.order_manager import order_manager
from app.handlers.common_tool_defs import (
    ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
    CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
    LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
    RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
    GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
    SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
    PROCESS_ORDER_SELECTION_TOOL_SCHEMA,
    PROCESS_COMBO_SELECTION_TOOL_SCHEMA
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FINAL_HANGUP_MARK_NAME = "final_hangup_mark"

class DeepgramEnglishAudioHandler():
    """Handler for processing audio streams between Twilio and Deepgram for English"""
    
    def __init__(self, websocket, client_id=None, deepgram_api_key=None, system_message=None, function_definitions=None, language=None):
        """Initialize the audio handler"""
        self.websocket = websocket
        self.client_id = client_id or "LIMF" # Default to LIMF if not provided
        self.deepgram_api_key = deepgram_api_key
        self.deepgram_service = None
        self.menu_sms_sent = False # For tracking initial menu SMS
        
        # Track if Deepgram is ready
        self.deepgram_ready = False
        
        # Initialize default language to English or override with parameter
        self._language = language if language else "english"
        logger.info(f"AudioHandler initialized with language: {self._language} and client_id: {self.client_id}")
        
        # Store configuration for future language switching
        self.system_message = system_message # Will be populated with LIMF default if None in _update_deepgram_language
        self.function_definitions = function_definitions or [
            ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
            CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
            LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
            RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
            GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
            SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
            PROCESS_ORDER_SELECTION_TOOL_SCHEMA, # This will be renamed in the schema file
            PROCESS_COMBO_SELECTION_TOOL_SCHEMA # This will be renamed in the schema file
        ]
        
        # Initialize caller information
        self.stream_sid = None
        self.call_sid = None
        self.caller_phone = None
        
        # Initialize audio processing parameters
        self.sample_rate = 8000  # 8 kHz for Twilio mulaw audio
        self.send_interval_ms = 400 # Send every 400ms to Deepgram
        self.buffer_size_bytes = int(self.sample_rate * (self.send_interval_ms / 1000) * 1)  # 1 byte per sample
        
        # Initialize audio buffers
        self.inbuffer = bytearray()
        
        # Initialize order state tracking
        self.order_processed = False
        self.is_final_confirmation = False
        self.stop_event_handled = False
        self._stop_processing_deepgram = False # For graceful shutdown of process_deepgram_responses
        self.agent_is_speaking = False
        self.stop_event = asyncio.Event()
        self.cleared_event = asyncio.Event()
        self.connection_closed = asyncio.Event()
        
        # Initialize a queue for sharing stream SID with other tasks
        self.streamsid_queue = asyncio.Queue()
        
        # Prepare a list of keywords for speech recognition
        from app.utils.constants import get_keywords
        self.keywords = get_keywords()
        
        # Complete call audio buffer for S3 upload
        self.complete_audio_buffer = bytearray()
        
        logger.info(f"Audio handler initialized with buffer size: {self.buffer_size_bytes} bytes " +
                   f"({self.send_interval_ms}ms at {self.sample_rate}Hz)")

    @property
    def language(self):
        return self._language

    async def process_twilio_messages(self):
        """Process messages from Twilio WebSocket"""
        logger.info("Starting to process Twilio messages")
        try:
            async for message in self.websocket.iter_text():
                await self.process_twilio_message(message)
        except asyncio.CancelledError:
            logger.info("Twilio message processing task cancelled")
            await self._handle_stop_event({"event": "stop"})
        except Exception as e:
            logger.error(f"Error in process_twilio_messages: {e}")
            await self._handle_stop_event({"event": "stop"})
        finally:
            if not self.stop_event_handled:
                logger.info("Twilio message processing loop ended, ensuring stop event is handled for S3 upload.")
                await self._handle_stop_event({"event": "stop"}) # Synthesize stop if needed
                
            if self.call_sid:
                logger.info(f"Removing call state for {self.call_sid} at end of processing loop.")
                await remove_call_state(self.call_sid) 
                logger.info(f"Removed call state for {self.call_sid}")
                
            logger.info("WebSocket session ended")

    async def process_twilio_message(self, message):
        """Process a single message from Twilio WebSocket"""
        try:
            try:
                data = json.loads(message)
                event_type = data.get("event")
                
                if event_type == "connected":
                    logger.info(f"Received unhandled Twilio event type: {event_type}")
                elif event_type == "start":
                    await self._handle_start_event(data)
                elif event_type == "media":
                    # Pass the full data object to _handle_media_event
                    await self._handle_media_event(data)
                elif event_type == "stop":
                    logger.info(f"Received 'stop' event from Twilio")
                    await self._handle_stop_event(data)
                elif event_type == "mark":
                    await self._handle_mark_event(data)
                elif event_type == "dtmf":
                    await self._handle_dtmf_event(data)
                else:
                    logger.info(f"Received unhandled Twilio event type: {event_type}")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse Twilio message: {message}")
            except Exception as e:
                logger.error(f"Error processing Twilio message: {e}")
        except Exception as e:
            logger.error(f"Error in process_twilio_message: {e}")

    async def _handle_dtmf_event(self, data: Dict[str, Any]):
        """
        Handle DTMF events received from Twilio Media Streams
        
        Args:
            data: DTMF event message from Twilio
        """
        try:
            dtmf_data = data.get('dtmf', {})
            digit = dtmf_data.get('digit')
            
            if not digit:
                logger.warn("Received DTMF event without digit")
                return
                
            logger.info(f"Received DTMF digit: {digit}")
            
            # Language switching via DTMF is now disabled.
            # The initial language selection is final.
            # We can keep logging the DTMF or add other DTMF-based actions here if needed in the future.
            logger.info(f"DTMF digit {digit} received. In-call language switching is disabled.")

            if digit == "0":
                logger.info(f"Human handoff requested for call {self.call_sid}.")

                handoff_url = f"{settings.PUBLIC_BASE_URL}/api/v1/human-handoff-twiml"
                
                # Redirect the call to the new TwiML endpoint
                await asyncio.get_event_loop().run_in_executor(None, functools.partial(redirect_call, self.call_sid, handoff_url))
                
                # Clean up the handler
                await self._handle_stop_event({"event": "stop"})
                return
            
        except Exception as e:
            logger.error(f"Error handling DTMF event: {e}")

    async def _handle_start_event(self, data: Dict[str, Any]):
        """Handle Twilio start event"""
        logger.info(f"Received start event data: {json.dumps(data)}")
        try:
            self.stream_sid = data.get("streamSid")
            self.call_sid = data.get("start", {}).get("callSid")
            
            if self.call_sid is None or self.call_sid == "":
                logger.critical(f"CRITICAL ERROR: callSid is missing, None, or empty in start event data for stream {self.stream_sid}. Raw data: {json.dumps(data)}. Cannot proceed.")
                return # Stop processing this event
            
            self.call_connected = True
            
            from app.api.websocket import get_caller_phone
            self.caller_phone = get_caller_phone(self.call_sid)
            
            if not self.caller_phone:
                if "customParameters" in data and "callerId" in data["customParameters"]:
                    self.caller_phone = data["customParameters"]["callerId"]
                    
            logger.info(f"Call started: {self.call_sid}, Stream: {self.stream_sid}, Caller: {self.caller_phone}")
            
            try:
                await register_call(self.call_sid, self.stream_sid, self.caller_phone)
                logger.info(f"Registered call {self.call_sid} with call state service")
            except Exception as e:
                logger.error(f"Error registering call with state service: {e}")
            
            try:
                await save_call_start(self.call_sid, self.caller_phone)
                logger.info(f"Saved call start: {self.call_sid}")
            except Exception as e:
                logger.error(f"Error saving call start: {e}")
            
            # Language is already set during __init__ and is fixed for this handler.
            # No need to set self.language = "english" here.
            
            # IMPORTANT: Set a flag to send the welcome message once Deepgram is ready
            self.send_welcome_on_connection = True

        except Exception as e:
            logger.error(f"Error handling start event: {e}")

    async def _speak_text(self, text: str):
        """
        Send text to Deepgram for TTS
        
        Args:
            text: The text to speak
        """
        try:
            if not self.deepgram_service or not self.deepgram_service.socket:
                logger.error("Cannot speak text: Deepgram connection not established")
                return
            
            # Language is fixed as English for this handler, so no specific Chinese character check needed here.
                
            message = {
                "type": "InjectAgentMessage",
                "message": text
            }
            
            await self.deepgram_service.send_json(message)
            logger.info(f"Sent text to Deepgram TTS: {text[:50]}{'...' if len(text) > 50 else ''}")
        except Exception as e:
            logger.error(f"Error sending text to Deepgram: {e}")

    async def _handle_media_event(self, data: Dict[str, Any]):
        """Handle Twilio media event"""
        try:
            if "media" in data:
                media_data = data.get("media", {})
                track = media_data.get("track")
                state = media_data.get("state")
                
                if state and state in ["ended", "completed"]:
                    logger.info(f"Media track {track} state changed to {state} - potential TTS completion")
                    try:
                        if self.stream_sid:
                            await register_media_event(self.stream_sid, "media", media_data)
                            logger.info(f"Registered media completion event for {self.stream_sid}")
                    except Exception as e:
                        logger.error(f"Error registering media event: {e}")
        except Exception as e:
            logger.error(f"Error processing media event for TTS tracking: {e}")
        
        try:
            if "media" in data and "payload" in data.get("media", {}) and data.get("media", {}).get("track") == "inbound":
                payload = data.get("media", {}).get("payload")
                if payload:
                    chunk = base64.b64decode(payload)
                    logger.debug(f"Decoded media chunk size: {len(chunk)}")
                    self.inbuffer.extend(chunk)
                    self.complete_audio_buffer.extend(chunk) # Accumulate for S3 upload
                    
                    if len(self.inbuffer) >= self.buffer_size_bytes:
                        if self.deepgram_service:
                            # ensure_alive is expected to be called within deepgram_service.send_audio
                            success = await self.deepgram_service.send_audio(bytes(self.inbuffer))
                            if success:
                                self.inbuffer.clear()
                            else:
                                logger.warning("Failed to send audio to Deepgram (send_audio returned False). Audio remains in buffer.")
                        else:
                            logger.error("Cannot send audio to Deepgram: deepgram_service is not initialized.")
                    
        except Exception as e:
            logger.error(f"Error processing audio data: {e}")
    
    async def _handle_stop_event(self, data: Dict[str, Any]):
        """Handle Twilio stop event"""
        logger.info("Received 'stop' event from Twilio")
        
        if self.stop_event_handled:
            logger.info("Stop event already handled, skipping S3 upload")
            return
        
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
                logger.error(f"Error uploading audio to S3: {e}")
                import traceback
                logger.error(f"S3 upload traceback: {traceback.format_exc()}")
        else:
            logger.warning(f"Not uploading audio to S3: call_sid={self.call_sid}, buffer_size={len(self.complete_audio_buffer) if self.complete_audio_buffer else 0}")
        
        if self.call_sid:
            try:
                await save_call_end(self.call_sid, audio_url)
                logger.info(f"Saved call end with audio URL: {self.call_sid}")
            except Exception as e:
                logger.error(f"Failed to save call end: {e}")
        
        if self.inbuffer:
            try:
                await self.deepgram_service.send_audio(self.inbuffer)
                self.inbuffer.clear()
            except Exception as e:
                logger.error(f"Error sending final audio buffer: {e}")
        
        self.stop_event_handled = True
    
    async def _handle_mark_event(self, data: Dict[str, Any]):
        """Handle incoming mark events from Twilio."""
        mark_name = data.get("mark", {}).get("name")
        sequence_number = data.get("sequenceNumber")
        stream_sid = data.get("streamSid")
        logger.info(f"Received mark event: Name='{mark_name}', Seq={sequence_number}, Stream={stream_sid}")
        
        if "digit" in data.get("mark", {}):
            digit = data.get("mark", {}).get("digit")
            logger.info(f"Received DTMF digit via mark event: {digit}, but language switching is handled at call start")
        
        if mark_name == FINAL_HANGUP_MARK_NAME:
            logger.info(f"Final hangup mark received. Scheduling hangup.")
            async def schedule_hangup():
                await asyncio.sleep(1.5) # Wait 1.5 seconds
                logger.info(f"Executing scheduled hangup for call {self.call_sid}")
                result = end_call(self.call_sid)
                logger.info(f"Hangup result: {result}")
            asyncio.create_task(schedule_hangup())
        elif mark_name == FINAL_AUDIO_MARK_NAME:
            logger.info(f"Received final message mark '{mark_name}'. This is now deprecated in favor of '{FINAL_HANGUP_MARK_NAME}'. Ignoring.")

    async def process_deepgram_responses(self):
        """Process responses from Deepgram"""
        if not self.stream_sid:
            logger.info("Waiting for Stream SID before processing Deepgram responses")
            self.stream_sid = await self.streamsid_queue.get()
            logger.info(f"Got Stream SID: {self.stream_sid[:8]}...")
        
        # Deepgram service is expected to be initialized and set externally (e.g., in websocket.py)
        # before this method is called. Language is fixed at initialization.
        if not self.deepgram_service:
            logger.error("Deepgram service not initialized. Aborting Deepgram processing.")
            return
        
        self.deepgram_service.add_message_handler(self._handle_deepgram_message)
        
        # Robust loop for receiving messages with reconnection logic
        active = True
        while active and not self._stop_processing_deepgram:
            try:
                is_connected = await self.deepgram_service.ensure_alive() # Ensure connection before listening
                if not is_connected:
                    logger.error("Failed to ensure Deepgram connection. Waiting before retrying listener.")
                    await asyncio.sleep(5) 
                    continue

                logger.info("Starting/Resuming Deepgram message receiving...")
                await self.deepgram_service.receive_messages() # Blocks until error/close
                
                # If it returns, connection was likely closed by server or an issue occurred.
                logger.warning("Deepgram receive_messages exited. Will attempt to re-ensure connection in the next loop iteration if not stopping.")
                if not self._stop_processing_deepgram: # Avoid sleep if we are trying to stop
                    await asyncio.sleep(1) 

            except asyncio.CancelledError:
                logger.info("Deepgram response processing task cancelled.")
                active = False
            except Exception as e:
                logger.error(f"Critical error in process_deepgram_responses loop: {e}")
                active = False 
        logger.info("Exited Deepgram response processing loop.")


    async def _handle_deepgram_close(self): # This might be redundant if DeepgramService handles its state
        """Handle Deepgram connection closure."""
        logger.info(f"Deepgram connection closed for call_sid: {self.call_sid}")
        self.deepgram_ready = False
        # Optionally, you might want to set self.deepgram_service = None or add reconnection logic
    
    async def _handle_deepgram_message(self, message):
        """Handle message received from Deepgram"""
        if isinstance(message, dict):
            await self._handle_deepgram_json(message)
        elif isinstance(message, bytes):
            await self._handle_deepgram_audio(message)

        # Check for connection ready indicators
        if isinstance(message, dict) and message.get("type") == "SettingsApplied":
            logger.info("Deepgram connection is ready (SettingsApplied received)")
            # Mark the Deepgram connection as ready
            self.deepgram_ready = True

            # Send welcome message if this is the first connection
            if getattr(self, 'send_welcome_on_connection', False):
                self.send_welcome_on_connection = False
                # Use a small delay to ensure everything is initialized
                await asyncio.sleep(1.0)
                try:
                    await self._send_welcome_message()
                except Exception as e:
                    logger.error(f"Error sending welcome message after connection ready: {e}")
        
        # Specific event handling is now primarily in _handle_deepgram_json
        # However, AgentAudioDone might still be processed here if it's not exclusively a JSON message type
        # or if _handle_deepgram_message is the sole dispatcher.
        # For now, assuming AgentAudioDone is a JSON message handled in _handle_deepgram_json.

    async def _handle_deepgram_json(self, message: Dict[str, Any]):
        """Handle JSON messages from Deepgram"""
        message_type = message.get("type", "unknown")
        
        if message_type == "UserStartedSpeaking":
            logger.info(f"Barge-in detected: User started speaking. Call SID: {self.call_sid}")
            clear_message = {
                "event": "clear",
                "streamSid": self.stream_sid
            }
            try:
                await self.websocket.send_text(json.dumps(clear_message))
                logger.info(f"Sent 'clear' event to Twilio for stream {self.stream_sid}.")
            except Exception as e:
                logger.error(f"Error sending 'clear' event to Twilio: {e}")
        
        elif message_type == "SpeechRecognitionResult":
            speech_data = message.get("speech", {})
            alternatives = speech_data.get("alternatives", [])
            transcript = alternatives[0].get("transcript", "") if alternatives else ""
            is_final = speech_data.get("is_final", False) # Or check for interim flags

            # Continue with existing transcript processing logic
            if transcript:
                confidence = alternatives[0].get("confidence", 0.0) if alternatives else 0.0
                logger.info(f"TRANSCRIPT (user): {transcript} (confidence: {confidence:.2f})")
                if self.call_sid:
                    try:
                        await save_utterance(self.call_sid, "user", transcript, confidence)
                    except Exception as e:
                        logger.error(f"Error saving utterance: {e}")
        
        elif message_type == "ConversationText" and message.get("role") == "assistant":
            if message.get("content"): # Ensure there's actual content
                if not self.agent_is_speaking:
                    self.agent_is_speaking = True
                    logger.info(f"Agent is now speaking (ConversationText from assistant). Call SID: {self.call_sid}")
            # Process the actual content of AgentResponse / ConversationText
            response_text = message.get("content", "") # Use "content" for ConversationText
            if response_text:
                response_text = clean_text_for_tts(response_text)
                logger.info(f"AGENT RESPONSE (ConversationText): {response_text}")
                if "goodbye" in response_text.lower():
                    if self.deepgram_service:
                        self.deepgram_service.is_final_confirmation_sent = True
                # Metadata handling might be different for ConversationText vs AgentResponse
                # For now, just save the utterance
                if self.call_sid:
                    try:
                        await save_utterance(self.call_sid, "agent", response_text) # role is "assistant"
                    except Exception as e:
                        logger.error(f"Error saving agent ConversationText utterance: {e}")

        elif message_type == "AgentResponse": # Original AgentResponse handling for other purposes (e.g. metadata)
            response_text = message.get("response", "")
            # This block might now be partially redundant if ConversationText is the primary way agent speech is conveyed
            # However, AgentResponse might carry other metadata or be used for non-speech responses.
            if response_text:
                # agent_is_speaking state is now primarily set by ConversationText from assistant
                logger.info(f"AGENT RESPONSE (raw AgentResponse type): {response_text}")
                
                metadata = message.get("metadata", {})
                is_final_message_flag = metadata.get("is_final_message", False)
                utterance_id = metadata.get("utterance_id")
                
                if is_final_message_flag and utterance_id and self.call_sid:
                    logger.info(f"Detected final TTS message with utterance_id: {utterance_id}")
                    try:
                        await register_tts_started(self.stream_sid, utterance_id)
                        logger.info(f"Registered TTS start for final message: {utterance_id}")
                    except Exception as e:
                        logger.error(f"Error registering TTS start: {e}")
                
                if self.call_sid: # Save if not already saved by ConversationText handler
                    try:
                        await save_utterance(self.call_sid, "agent", response_text)
                    except Exception as e:
                        logger.error(f"Error saving agent AgentResponse utterance: {e}")

        elif message_type == "AgentAudioDone":
            logger.info(f"Received AgentAudioDone for call {self.call_sid}.")
            if self.agent_is_speaking:
                self.agent_is_speaking = False
                logger.info(f"Agent finished speaking (AgentAudioDone). Call SID: {self.call_sid}")
            
            if self.deepgram_service and self.deepgram_service.is_final_confirmation_sent:
                logger.info(f"Final confirmation audio finished. Sending hangup mark to Twilio.")
                if self.websocket and self.stream_sid and self.websocket.client_state == WebSocketState.CONNECTED:
                    mark_message = {
                        "event": "mark",
                        "streamSid": self.stream_sid,
                        "mark": {
                            "name": FINAL_HANGUP_MARK_NAME
                        }
                    }
                    await self.websocket.send_text(json.dumps(mark_message))
                    logger.info(f"Sent final hangup mark: {FINAL_HANGUP_MARK_NAME}")
                else:
                    logger.error("Cannot send hangup mark: WebSocket is not connected or stream_sid is missing.")
                self.deepgram_service.is_final_confirmation_sent = False # Reset flag

        elif message_type == "Cleared":
            logger.info(f"Received 'Cleared' acknowledgement from Deepgram. Call SID: {self.call_sid}")
            self.cleared_event.set()

        elif message_type == "FunctionCallRequest":
            functions_to_call = message.get("functions", [])
            if not functions_to_call:
                logger.warning("Received FunctionCallRequest with no functions listed.")
                return

            for func_data in functions_to_call:
                function_call_id = func_data.get("id")
                function_name = func_data.get("name")
                arguments_str = func_data.get("arguments", "{}")
                client_side = func_data.get("client_side", True) # Default to True if missing

                if not client_side:
                    logger.info(f"Skipping server-side function call: {function_name} (ID: {function_call_id})")
                    continue

                try:
                    input_data = json.loads(arguments_str)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse arguments for function {function_name} (ID: {function_call_id}): {arguments_str}")
                    if function_call_id:
                        error_response = {
                            "type": "FunctionCallResponse",
                            "function_call_id": function_call_id,
                            "output": "Invalid arguments format provided by the agent."
                        }
                        if self.deepgram_service:
                            await self.deepgram_service.send_json(error_response)
                        else:
                            logger.error("Cannot send FunctionCallResponse for JSON parse error: deepgram_service not available.")
                    continue

                logger.info(f"Dispatching function call: {function_name} with ID: {function_call_id}")

                reformatted_function_request = {
                    "function_name": function_name,
                    "function_call_id": function_call_id,
                    "input": input_data
                }

                try:
                    # --- COMBO FINALIZATION GATEKEEPER (MOVED FROM english_tool_logic.py) ---
                    # This logic must run *before* the state override check.
                    if function_name == "order_summary" and combo_order_manager.is_awaiting_final_confirmation(self.call_sid):
                        logger.info(f"Finalizing pending combo for call {self.call_sid} before dispatching tool.")
                        completed_combo = combo_order_manager.finalize_and_get_combo(self.call_sid)
                        if completed_combo:
                            order_manager.add_item_to_cart(
                                self.call_sid,
                                completed_combo.get("name"),
                                completed_combo.get("quantity", 1),
                                completed_combo.get("options", {})
                            )
                            logger.info(f"Successfully finalized and moved combo '{completed_combo.get('name')}' to main cart.")
                            await clear_next_tool(self.call_sid) # Clear any pending state override after successful finalization
                        else:
                            logger.error(f"Failed to finalize pending combo for call {self.call_sid}. The combo may be lost.")
                    
                    # State override logic
                    next_tool_override = await get_and_clear_next_tool(self.call_sid)
                    if next_tool_override:
                        logger.info(f"STATE OVERRIDE: Forcing use of tool '{next_tool_override}' instead of agent-selected '{function_name}' for call {self.call_sid}")
                        reformatted_function_request["function_name"] = next_tool_override
                    
                    await handle_function_call(
                        reformatted_function_request,
                        self.deepgram_service,
                        self.websocket,
                        self.stream_sid,
                        self.caller_phone,
                        self.call_sid,
                        self.client_id
                    )
                    if function_name == "order_summary" and input_data.get("summary") == "DONE":
                        self.is_final_confirmation = True
                except Exception as e:
                    logger.error(f"Error handling function call request for {function_name} (ID: {function_call_id}): {e}", exc_info=True)
                    if function_call_id:
                        error_response = {
                            "type": "FunctionCallResponse",
                            "function_call_id": function_call_id,
                            "output": "Sorry, there was an error processing your request."
                        }
                        if self.deepgram_service:
                            await self.deepgram_service.send_json(error_response)
                        else:
                            logger.error("Cannot send FunctionCallResponse for unhandled exception: deepgram_service not available.")


        # The original generic "ConversationText" handler (which included order parsing)
        # is now effectively replaced by:
        # 1. The specific "ConversationText" handler for `role == "assistant"` (for agent_is_speaking logic and saving agent text).
        # 2. The "SpeechRecognitionResult" handler (for saving user text).
        # 3. The "FunctionCallRequest" handler (for structured actions like order processing).
        # This should cover all previous functionalities in a more organized way.

    async def _handle_deepgram_audio(self, audio_data: bytes):
        """Handle binary audio data from Deepgram"""
        if self.connection_closed.is_set():
            logger.warning("Connection is closing, skipping sending audio to Twilio.")
            return

        if not self.stream_sid:
            logger.warning("Received audio from Deepgram but no Stream SID available")
            return
        
        try:
            payload = base64.b64encode(audio_data).decode('ascii')
            
            media_message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": payload
                }
            }
            
            await self.websocket.send_json(media_message)
            
            # Append the agent's audio (audio_data from Deepgram, confirmed to be mulaw) 
            # to the complete_audio_buffer for S3 recording.
            if audio_data:
                self.complete_audio_buffer.extend(audio_data)
                
        except Exception as e:
            if "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'" in str(e):
                logger.warning(f"Tried to send audio after websocket was closed: {e}")
            else:
                logger.error(f"Error sending agent audio to Twilio or appending to S3 buffer: {e}")

    async def _send_welcome_message(self):
        """Send welcome message to the caller"""
        try:
            # Wait 1 second after connection to ensure everything is ready
            await asyncio.sleep(1)
            
            logger.info("Attempting to send welcome message through Deepgram")
            
            # Get restaurant name from configuration using self.client_id
            from app.utils.constants import get_restaurant_config
            # Use self.client_id which is set during __init__ and defaults to "LIMF"
            restaurant_config = get_restaurant_config(self.client_id) 
            logger.info(f"Retrieved restaurant configuration for client_id: {self.client_id}")
            # Fetch RESTAURANT_NAME, fallback to "our restaurant" if not found in config
            restaurant_name = restaurant_config.get("RESTAURANT_NAME", "our restaurant")
            
            # Language is fixed for this handler (English).
            # The Deepgram service should be configured accordingly when instantiated.
            logger.info(f"Language for welcome message: {self.language}")
            
            # English welcome message
            welcome_message = settings.ENGLISH_WELCOME_MESSAGE.format(restaurant_name=restaurant_name)
            logger.info(f"Using English welcome message: {welcome_message}")
            
            # Send welcome message
            message = {
                "type": "InjectAgentMessage",
                "message": welcome_message
            }
            
            # Debug: Log the exact message being sent
            logger.info(f"Sending message to Deepgram with language={self.language}, content={welcome_message}")
            
            if self.deepgram_service:
                await self.deepgram_service.send_json(message)
                logger.info(f"Successfully sent welcome message: {welcome_message[:50]}{'...' if len(welcome_message) > 50 else ''}")
            else:
                logger.error("Cannot send welcome message: Deepgram service not initialized")
        except Exception as e:
            logger.error(f"Error in _send_welcome_message: {str(e)}")

    async def _send_direct_message(self, text):
        """Send a message directly to Deepgram without additional checks"""
        try:
            if not self.deepgram_service:
                logger.error("Cannot send message - Deepgram service not available")
                return
                
            message = {
                "type": "InjectAgentMessage",
                "message": text
            }
            
            await self.deepgram_service.send_json(message)
            logger.info(f"Sent direct message: {text[:50]}{'...' if len(text) > 50 else ''}")
        except Exception as e:
            logger.error(f"Error sending direct message: {e}")
