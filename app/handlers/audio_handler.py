"""
Audio Handler - Process audio streams between Twilio and Deepgram
"""
import asyncio
import base64
import json
import logging
import re
from fastapi import WebSocket
import os
from typing import Optional, Dict, Any
import traceback
import time

from app.services.deepgram_service import DeepgramService
from app.handlers.function_handler import FINAL_AUDIO_MARK_NAME
from app.utils.twilio import end_call
from app.services.call_state_service import remove_call_state

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AudioHandler:
    """Handler for processing audio streams between Twilio and Deepgram"""
    
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
        self.function_definitions = function_definitions or []
        
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

    # Removed language.setter as language is fixed on initialization

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
                from app.services.call_state_service import remove_call_state
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
                from app.services.call_state_service import register_call
                await register_call(self.call_sid, self.stream_sid, self.caller_phone)
                logger.info(f"Registered call {self.call_sid} with call state service")
            except Exception as e:
                logger.error(f"Error registering call with state service: {e}")
            
            try:
                from app.services.database_service import save_call_start
                await save_call_start(self.call_sid, self.caller_phone)
                logger.info(f"Saved call start: {self.call_sid}")
            except Exception as e:
                logger.error(f"Error saving call start: {e}")
            
            # Language is already set during __init__ and is fixed for this handler.
            # No need to set self.language = "english" here.
            
            # IMPORTANT: Set a flag to send the welcome message once Deepgram is ready
            self.send_welcome_on_connection = True

            # Send menu via SMS if applicable (similar to GitHub repo)
            if self.client_id and not self.menu_sms_sent and self.caller_phone:
                try:
                    from app.utils.constants import get_restaurant_menu
                    from app.utils.menu_formatter import format_menu_for_sms
                    from app.utils.twilio import send_sms

                    menu_items = get_restaurant_menu(self.client_id)
                    if menu_items:
                        logger.info(f"Formatting menu with {len(menu_items)} items for SMS for client {self.client_id}")
                        menu_text_sms = format_menu_for_sms(menu_items, self.client_id)
                        send_sms(self.caller_phone, menu_text_sms, self.client_id)
                        self.menu_sms_sent = True
                        logger.info(f"Sent initial menu via SMS to {self.caller_phone} for client {self.client_id}")
                    else:
                        logger.warning(f"No menu items found for client {self.client_id}, not sending menu SMS.")
                except Exception as e_sms:
                    logger.error(f"Error sending initial menu via SMS: {e_sms}")
            
        except Exception as e:
            logger.error(f"Error handling start event: {e}")

    # Removed _update_deepgram_language method as language is fixed on initialization

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
                        from app.services.call_state_service import register_media_event
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
                    
                    if len(self.inbuffer) >= self.buffer_size_bytes:
                        await self.deepgram_service.send_audio(bytes(self.inbuffer))
                        self.inbuffer.clear()
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
                from app.utils.database import upload_audio_to_s3
                
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
                from app.services.database_service import save_call_end
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
        
        if mark_name == FINAL_AUDIO_MARK_NAME:
            logger.info(f"Received final message mark '{mark_name}'. Initiating immediate hangup.")
            if self.call_sid:
                result = end_call(self.call_sid) 
                logger.info(f"Hangup initiated via REST API due to mark event. Result: {result}")
            else:
                logger.error("Cannot hang up after mark event: call_sid is missing.")

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
        
        await self.deepgram_service.receive_messages()

    async def _handle_deepgram_close(self):
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
        
        # Handle AgentAudioDone event
        if isinstance(message, dict) and message.get("type") == "AgentAudioDone":
            logger.info(f"Received AgentAudioDone for call {self.call_sid}.")
            if self.is_final_confirmation: 
                logger.info(f"Final confirmation received for call {self.call_sid}. Scheduling hangup.")
                if self.call_sid:
                    async def schedule_hangup(): 
                        await asyncio.sleep(2) # Wait 2 seconds
                        logger.info(f"Executing scheduled hangup for call {self.call_sid}")
                        result = end_call(self.call_sid)
                        logger.info(f"Hangup result: {result}")
                    
                    asyncio.create_task(schedule_hangup())
                else:
                    logger.error("Cannot schedule hangup after AgentAudioDone: call_sid is missing.")
                    self.is_final_confirmation = False # Reset flag even on error

    async def _handle_deepgram_json(self, message: Dict[str, Any]):
        """Handle JSON messages from Deepgram"""
        message_type = message.get("type", "unknown")
        logger.info(f"Handling Deepgram message of type: {message_type}")
        
        if message_type == "SpeechRecognitionResult":
            speech_data = message.get("speech", {})
            is_final = speech_data.get("is_final", False)
            alternatives = speech_data.get("alternatives", [])
            
            if alternatives and is_final:
                transcript = alternatives[0].get("transcript", "")
                confidence = alternatives[0].get("confidence", 0.0)
                
                if transcript:
                    logger.info(f"TRANSCRIPT: {transcript} (confidence: {confidence:.2f})")
                    
                    if self.call_sid:
                        try:
                            from app.services.database_service import save_utterance
                            await save_utterance(self.call_sid, "user", transcript, confidence)
                        except Exception as e:
                            logger.error(f"Error saving utterance: {e}")
        
        elif message_type == "AgentResponse":
            response_text = message.get("response", "")
            
            if response_text:
                logger.info(f"AGENT RESPONSE: {response_text}")
                
                metadata = message.get("metadata", {})
                is_final = metadata.get("is_final_message", False)
                utterance_id = metadata.get("utterance_id")
                
                if is_final and utterance_id and self.call_sid:
                    logger.info(f"Detected final TTS message with utterance_id: {utterance_id}")
                    try:
                        from app.services.call_state_service import register_tts_started
                        await register_tts_started(self.stream_sid, utterance_id)
                        logger.info(f"Registered TTS start for final message: {utterance_id}")
                    except Exception as e:
                        logger.error(f"Error registering TTS start: {e}")
                
                if self.call_sid:
                    try:
                        from app.services.database_service import save_utterance
                        await save_utterance(self.call_sid, "agent", response_text)
                    except Exception as e:
                        logger.error(f"Error saving utterance: {e}")
        
        elif message_type == "FunctionCallRequest":
            function_name = message.get("function_name", "")
            function_call_id = message.get("function_call_id", "")
            input_data = message.get("input", {})
            
            logger.info(f"FUNCTION CALL REQUEST: {function_name} with ID: {function_call_id}")
            logger.info(f"Function input data: {json.dumps(input_data)}")
            
            if self.call_sid:
                try:
                    from app.services.database_service import save_utterance
                    await save_utterance(
                        self.call_sid,
                        "system_function",
                        f"Function: {function_name}, Input: {json.dumps(input_data)}"
                    )
                except Exception as e:
                    logger.error(f"Error saving function call to database: {e}")
            
            try:
                from app.handlers.function_handler import handle_function_call
                logger.info(f"Calling handle_function_call with call_sid: {self.call_sid}")
                await handle_function_call(
                    message,
                    self.deepgram_service,
                    self.websocket,
                    self.stream_sid,
                    self.caller_phone,
                    self.call_sid
                )
                if message.get("function_name") == "order_summary" and message.get("input", {}).get("summary") == "DONE":
                    self.is_final_confirmation = True
            except Exception as e:
                logger.error(f"Error handling function call request: {e}")
                try:
                    error_response = {
                        "type": "FunctionCallResponse",
                        "function_call_id": function_call_id,
                        "output": "Sorry, there was an error processing your request."
                    }
                    await self.deepgram_service.send_json(error_response)
                    logger.info(f"Sent error response for function call {function_call_id}")
                except Exception as e2:
                    logger.error(f"Error sending error response: {e2}")
        
        elif message_type == "ConversationText":
            role = message.get("role", "")
            content = message.get("content", "")
            
            logger.info(f"{role.upper()} TEXT: {content}")
            
            if role == "assistant" and "{" in content and "}" in content and not self.order_processed:
                try:
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    if json_start >= 0 and json_end > json_start:
                        json_str = content[json_start:json_end]
                        logger.info(f"Extracted JSON data: {json_str}")
                        
                        input_data = json.loads(json_str)
                        logger.info(f"Parsed order data: {input_data}")
                        
                        if "items" in input_data and ("total_price" in input_data or "total" in input_data):
                            logger.info("Detected order data in conversation text")
                            
                            if not self.order_processed:
                                logger.info("Setting order_processed flag - actual processing happens in function_handler.py")
                                self.order_processed = True
                                
                                order_items = input_data.get("items", [])
                                total_price = input_data.get("total_price", input_data.get("total", 0))
                                summary_status = input_data.get("summary", input_data.get("status", "IN PROGRESS"))
                                
                                logger.info(f"Detected order - Items: {order_items}, Total: {total_price}, Status: {summary_status}")
                                logger.info("Order will not be processed here - using function_handler.py instead")
                            else:
                                logger.info("Order already processed, skipping duplicate detection")
                except Exception as e:
                    logger.error(f"Error processing potential order data: {e}")
                    logger.error(f"Exception details: {traceback.format_exc()}")
            
            if self.call_sid:
                try:
                    from app.services.database_service import save_utterance
                    await save_utterance(self.call_sid, role, content)
                except Exception as e:
                    logger.error(f"Error saving utterance: {e}")

    async def _handle_deepgram_audio(self, audio_data: bytes):
        """Handle binary audio data from Deepgram"""
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
        except Exception as e:
            logger.error(f"Error sending audio to Twilio: {e}")

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
            welcome_message = f"Welcome to {restaurant_name}. I'm your voice assistant, how can I help you today?"
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
