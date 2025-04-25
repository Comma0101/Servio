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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AudioHandler:
    """Handler for processing audio streams between Twilio and Deepgram"""
    
    def __init__(self, deepgram_service, websocket: WebSocket):
        """
        Initialize the audio handler
        
        Args:
            deepgram_service: Service for communicating with Deepgram
            websocket: WebSocket connection to Twilio
        """
        self.deepgram_service = deepgram_service
        self.websocket = websocket
        
        # Initialize state
        self.audio_queue = asyncio.Queue()
        self.streamsid_queue = asyncio.Queue()
        self.inbuffer = bytearray()
        
        # Call metadata
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.caller_phone: Optional[str] = None
        self.client_id: str = "LIMF"  # Default restaurant/client ID
        self.menu_sms_sent = False
        
        # Order processing flags
        self.order_processed = False
        self.order_confirmation_sent = False
        self.is_final_confirmation = False
        
        # S3 upload tracking
        self.stop_event_handled = False
        
        # Audio processing configuration
        self.sample_rate = 8000  # 8kHz for Twilio audio streams
        self.audio_buffer_ms = int(os.getenv("AUDIO_BUFFER_SIZE_MS", "20"))  # Twilio sends 20ms chunks
        self.send_interval_ms = int(os.getenv("AUDIO_SEND_INTERVAL_MS", "400"))  # Buffer 400ms before sending
        self.buffer_size_bytes = int(self.send_interval_ms / 1000 * self.sample_rate)
        
        # Complete call audio buffer for S3 upload
        self.complete_audio_buffer = bytearray()
        
        logger.info(f"Audio handler initialized with buffer size: {self.buffer_size_bytes} bytes " +
                   f"({self.send_interval_ms}ms at {self.sample_rate}Hz)")

    async def process_twilio_messages(self):
        """Process messages from Twilio WebSocket"""
        try:
            logger.info("Starting to process Twilio messages")
            async for message in self.websocket.iter_text():
                try:
                    data = json.loads(message)
                    event_type = data.get("event")
                    
                    # logger.info(f"Received event: {event_type}") # Comment out this general log too
                    if event_type == "start":
                        await self._handle_start_event(data)
                    elif event_type == "media":
                        # logger.info("Received event: media") # Commented out verbose log
                        await self._handle_media_event(data)
                    elif event_type == "stop":
                        await self._handle_stop_event(data)
                        break # Exit loop after stop event
                    elif event_type == "mark":
                        await self._handle_mark_event(data)
                        # Check if this is the specific mark indicating final audio played
                        mark_name = data.get("mark", {}).get("name")
                        if mark_name == FINAL_AUDIO_MARK_NAME:
                            logger.info(f"Received final message mark '{mark_name}'. Initiating immediate hangup.")
                            if self.call_sid:
                                # Use the REST API to hang up immediately
                                result = end_call(self.call_sid) 
                                logger.info(f"Hangup initiated via REST API due to mark event. Result: {result}")
                                # Optionally, you might want to ensure S3 upload happens if not already triggered by stop
                                # await self._ensure_s3_upload()
                                break # Exit loop after final mark processing and hangup
                            else:
                                logger.error("Cannot hang up after mark event: call_sid is missing.")
                    else:
                        logger.info(f"Received unhandled Twilio event type: {event_type}")
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse Twilio message: {message}")
                except Exception as e:
                    logger.error(f"Error processing Twilio message: {e}")
        except asyncio.CancelledError:
            # Gracefully handle task cancellation
            logger.info("Twilio message processing task cancelled")
            # Even on cancel, make sure we upload the audio to S3
            logger.info("WebSocket task cancelled, triggering stop event handling for S3 upload")
            await self._handle_stop_event({"event": "stop"})
        except Exception as e:
            logger.error(f"Error in process_twilio_messages: {e}")
            # If there's an error in the WebSocket, still try to upload the audio
            logger.info("WebSocket exception, triggering stop event handling for S3 upload")
            await self._handle_stop_event({"event": "stop"})
        finally:
            # Make absolutely sure we handle the stop event to upload audio when the function completes
            # This handles cases where the WebSocket closes without an error, 
            # but avoid double-upload if already handled by mark/stop break
            if not self.stop_event_handled:
                logger.info("Twilio message processing loop ended, ensuring stop event is handled for S3 upload.")
                await self._handle_stop_event({"event": "stop"}) # Synthesize stop if needed
                
            # Cleanup call state
            if self.call_sid:
                logger.info(f"Removing call state for {self.call_sid} at end of processing loop.")
                await remove_call_state(self.call_sid) 
                logger.info(f"Removed call state for {self.call_sid}")
                
            logger.info("WebSocket session ended")
    
    async def _handle_start_event(self, data: Dict[str, Any]):
        """Handle Twilio start event"""
        # Log the raw start event data for debugging
        logger.info(f"Received start event data: {json.dumps(data)}") 
        try:
            # Extract stream SID and call metadata
            self.stream_sid = data.get("streamSid")
            # Correctly extract nested callSid
            self.call_sid = data.get("start", {}).get("callSid") 
            
            # CRITICAL: Check if call_sid is None or empty after extraction
            if self.call_sid is None or self.call_sid == "":
                logger.critical(f"CRITICAL ERROR: callSid is missing, None, or empty in start event data for stream {self.stream_sid}. Raw data: {json.dumps(data)}. Cannot proceed.")
                # Optionally, close the connection or raise an error if this is unrecoverable
                # await self.websocket.close(code=1011, reason="Missing or invalid callSid") 
                return # Stop processing this event
            
            # Log call_sid immediately after assignment and validation
            logger.info(f"Call started: {self.call_sid}, Stream: {self.stream_sid}")
            
            # Parse caller phone from start event
            from app.api.websocket import get_caller_phone
            self.caller_phone = get_caller_phone(self.call_sid)
            
            if not self.caller_phone:
                if "customParameters" in data and "callerId" in data["customParameters"]:
                    self.caller_phone = data["customParameters"]["callerId"]
                    
            logger.info(f"Call started: {self.call_sid}, Stream: {self.stream_sid}, Caller: {self.caller_phone}")
            
            # Register this call with call state service for TTS completion tracking
            try:
                from app.services.call_state_service import register_call
                await register_call(self.call_sid, self.stream_sid, self.caller_phone)
                logger.info(f"Registered call {self.call_sid} with call state service")
            except Exception as e:
                logger.error(f"Error registering call with state service: {e}")
            
            # Save call start information to the database
            try:
                from app.services.database_service import save_call_start
                await save_call_start(self.call_sid, self.caller_phone)
                logger.info(f"Saved call start: {self.call_sid}")
            except Exception as e:
                logger.error(f"Error saving call start: {e}")
            
            # Make the agent speak first with a greeting
            try:
                # Get restaurant name from config for personalized greeting
                from app.utils.constants import get_restaurant_config
                restaurant_config = get_restaurant_config(self.client_id)
                restaurant_name = restaurant_config.get("RESTAURANT_NAME", "KK Restaurant")
                
                # Create greeting message
                initial_greeting = {
                    "type": "InjectAgentMessage",
                    "message": f"Hello! Welcome to {restaurant_name}. I'm your AI voice assistant. How can I help you today?"
                }
                
                # Send the greeting to Deepgram
                await self.deepgram_service.send_json(initial_greeting)
                logger.info("Sent initial greeting to make agent speak first")
            except Exception as e:
                logger.error(f"Error sending initial greeting: {e}")
            
            # If we have a restaurant ID, send the menu via SMS
            if self.client_id and not self.menu_sms_sent:
                try:
                    # Get restaurant menu
                    from app.utils.constants import get_restaurant_config, get_restaurant_menu
                    restaurant_config = get_restaurant_config(self.client_id)
                    menu_items = get_restaurant_menu(self.client_id)
                    logger.info(f"Formatting menu with {len(menu_items)} items for SMS")
                    
                    # Format the menu data for SMS
                    from app.utils.menu_formatter import format_menu_for_sms
                    menu_text = format_menu_for_sms(menu_items, self.client_id)
                    
                    # Send the SMS
                    from app.utils.twilio import send_sms
                    send_sms(self.caller_phone, menu_text, self.client_id)
                    
                    # Set flag to prevent sending duplicate SMS
                    self.menu_sms_sent = True
                except Exception as e:
                    logger.error(f"Error sending menu via SMS: {e}")
        except Exception as e:
            logger.error(f"Error handling start event: {e}")
    
    async def _handle_media_event(self, data: Dict[str, Any]):
        """Handle Twilio media event"""
        try:
            # Track media events for TTS completion detection
            if "media" in data:
                media_data = data.get("media", {})
                track = media_data.get("track")
                state = media_data.get("state")
                
                # Check for track state changes (common in WebRTC for completion signals)
                if state and state in ["ended", "completed"]:
                    logger.info(f"Media track {track} state changed to {state} - potential TTS completion")
                    try:
                        # Register this event for TTS completion tracking
                        from app.services.call_state_service import register_media_event
                        if self.stream_sid:
                            await register_media_event(self.stream_sid, "media", media_data)
                            logger.info(f"Registered media completion event for {self.stream_sid}")
                    except Exception as e:
                        logger.error(f"Error registering media event: {e}")
        except Exception as e:
            logger.error(f"Error processing media event for TTS tracking: {e}")
        
        # Continue with normal audio processing
        try:
            if "media" in data and "payload" in data.get("media", {}) and data.get("media", {}).get("track") == "inbound":
                payload = data.get("media", {}).get("payload")
                if payload:
                    chunk = base64.b64decode(payload)
                    logger.debug(f"Decoded media chunk size: {len(chunk)}")
                    self.inbuffer.extend(chunk)
                    
                    # If we have enough data, send to Deepgram
                    if len(self.inbuffer) >= self.buffer_size_bytes:
                        await self.deepgram_service.send_audio(bytes(self.inbuffer))
                        self.inbuffer.clear()
        except Exception as e:
            logger.error(f"Error processing audio data: {e}")
    
    async def _handle_stop_event(self, data: Dict[str, Any]):
        """Handle Twilio stop event"""
        logger.info("Received 'stop' event from Twilio")
        
        # Check if we've already handled a stop event
        if self.stop_event_handled:
            logger.info("Stop event already handled, skipping S3 upload")
            return
        
        # Upload audio to S3
        audio_url = None
        if self.call_sid and self.complete_audio_buffer:
            try:
                logger.info(f"Uploading call audio to S3 for call_sid: {self.call_sid}, size: {len(self.complete_audio_buffer)} bytes")
                from app.utils.database import upload_audio_to_s3
                
                # Upload the complete audio buffer to S3
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
        
        # Save call end in database with audio URL if available
        if self.call_sid:
            try:
                from app.services.database_service import save_call_end
                await save_call_end(self.call_sid, audio_url)
                logger.info(f"Saved call end with audio URL: {self.call_sid}")
            except Exception as e:
                logger.error(f"Failed to save call end: {e}")
        
        # Send any remaining audio in buffer to Deepgram
        if self.inbuffer:
            try:
                await self.deepgram_service.send_audio(self.inbuffer)
                self.inbuffer.clear()
            except Exception as e:
                logger.error(f"Error sending final audio buffer: {e}")
        
        # Mark stop event as handled
        self.stop_event_handled = True
    
    async def _handle_mark_event(self, data: Dict[str, Any]):
        """Handle incoming mark events from Twilio."""
        mark_name = data.get("mark", {}).get("name")
        sequence_number = data.get("sequenceNumber")
        stream_sid = data.get("streamSid")
        logger.info(f"Received mark event: Name='{mark_name}', Seq={sequence_number}, Stream={stream_sid}")
        
        # Optional: Add logic here if you need to react to other mark events
        
        # The primary logic for the final mark is handled directly in the process_twilio_messages loop

    async def process_deepgram_responses(self):
        """Process responses from Deepgram"""
        # Wait for stream_sid first if not already available
        if not self.stream_sid:
            logger.info("Waiting for Stream SID before processing Deepgram responses")
            self.stream_sid = await self.streamsid_queue.get()
            logger.info(f"Got Stream SID: {self.stream_sid[:8]}...")
        
        # Register message handlers
        self.deepgram_service.add_message_handler(self._handle_deepgram_message)
        
        # Start receiving messages from Deepgram
        await self.deepgram_service.receive_messages()
    
    async def _handle_deepgram_message(self, message):
        """Handle messages from Deepgram"""
        if isinstance(message, dict):
            # Handle JSON messages
            await self._handle_deepgram_json(message)
        elif isinstance(message, bytes):
            # Handle binary messages (audio)
            await self._handle_deepgram_audio(message)

        # --- Add logic to handle AgentAudioDone --- 
        if isinstance(message, dict) and message.get("type") == "AgentAudioDone":
            logger.info(f"Received AgentAudioDone for call {self.call_sid}.")
            if self.is_final_confirmation: 
                logger.info("Final confirmation flag is set. Scheduling hangup.")
                if self.call_sid:
                    # Schedule hangup after a short delay (e.g., 1 second)
                    async def schedule_hangup(): 
                        await asyncio.sleep(1) # Wait 1 second
                        logger.info(f"Executing scheduled hangup for call {self.call_sid}")
                        result = end_call(self.call_sid)
                        logger.info(f"Hangup result for {self.call_sid}: {result}")
                    
                    asyncio.create_task(schedule_hangup())
                    self.is_final_confirmation = False # Reset the flag
                else:
                    logger.error("Cannot schedule hangup after AgentAudioDone: call_sid is missing.")
                    self.is_final_confirmation = False # Reset flag even on error
            else:
                logger.info("AgentAudioDone received, but final confirmation flag is not set. Not hanging up.")

    async def _handle_deepgram_json(self, message: Dict[str, Any]):
        """Handle JSON messages from Deepgram"""
        message_type = message.get("type", "unknown")
        logger.info(f"Handling Deepgram message of type: {message_type}")
        
        if message_type == "SpeechRecognitionResult":
            # Process speech recognition result
            speech_data = message.get("speech", {})
            is_final = speech_data.get("is_final", False)
            alternatives = speech_data.get("alternatives", [])
            
            if alternatives and is_final:
                transcript = alternatives[0].get("transcript", "")
                confidence = alternatives[0].get("confidence", 0.0)
                
                if transcript:
                    logger.info(f"TRANSCRIPT: {transcript} (confidence: {confidence:.2f})")
                    
                    # Save to database
                    if self.call_sid:
                        try:
                            from app.services.database_service import save_utterance
                            await save_utterance(self.call_sid, "user", transcript, confidence)
                        except Exception as e:
                            # Log the error but don't let it stop execution
                            logger.error(f"Error saving utterance: {e}")
                            # Continue processing even if database save fails
        elif message_type == "AgentResponse":
            # Process agent response
            response_text = message.get("response", "")
            
            if response_text:
                logger.info(f"AGENT RESPONSE: {response_text}")
                
                # Check for final message metadata
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
                
                # Save to database
                if self.call_sid:
                    try:
                        from app.services.database_service import save_utterance
                        await save_utterance(self.call_sid, "agent", response_text)
                    except Exception as e:
                        # Log the error but don't let it stop execution
                        logger.error(f"Error saving utterance: {e}")
                        # Continue processing even if database save fails
        elif message_type == "FunctionCallRequest":
            # Process function call request from Deepgram
            function_name = message.get("function_name", "")
            function_call_id = message.get("function_call_id", "")
            input_data = message.get("input", {})
            
            logger.info(f"FUNCTION CALL REQUEST: {function_name} with ID: {function_call_id}")
            logger.info(f"Function input data: {json.dumps(input_data)}")
            
            # Save function call to database
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
            
            # Handle function call
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
                # Send an error response back to keep the conversation going
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
            # Process conversation text
            role = message.get("role", "")
            content = message.get("content", "")
            
            logger.info(f"{role.upper()} TEXT: {content}")
            
            # Check if this is an order summary embedded in a conversation message
            if role == "assistant" and "{" in content and "}" in content and not self.order_processed:
                # Try to extract JSON from any assistant message containing JSON-like structures
                logger.info("Checking for order data in conversation text")
                try:
                    # Try to extract JSON data from the text
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    if json_start >= 0 and json_end > json_start:
                        json_str = content[json_start:json_end]
                        logger.info(f"Extracted JSON data: {json_str}")
                        
                        # Parse the JSON data
                        input_data = json.loads(json_str)
                        logger.info(f"Parsed order data: {input_data}")
                        
                        # Check if this looks like an order (has items and price)
                        if "items" in input_data and ("total_price" in input_data or "total" in input_data):
                            logger.info("Detected order data in conversation text")
                            
                            # IMPORTANT: Order processing logic has been moved to function_handler.py
                            # This is now just a fallback detection mechanism
                            if not self.order_processed:
                                logger.info("Setting order_processed flag - actual processing happens in function_handler.py")
                                self.order_processed = True
                                
                                # Extract basic order information for logging purposes only
                                order_items = input_data.get("items", [])
                                total_price = input_data.get("total_price", input_data.get("total", 0))
                                summary_status = input_data.get("summary", input_data.get("status", "IN PROGRESS"))
                                
                                # Log order details without processing
                                logger.info(f"Detected order - Items: {order_items}, Total: {total_price}, Status: {summary_status}")
                                logger.info("Order will not be processed here - using function_handler.py instead")
                            else:
                                logger.info("Order already processed, skipping duplicate detection")
                except Exception as e:
                    logger.error(f"Error processing potential order data: {e}")
                    logger.error(f"Exception details: {traceback.format_exc()}")
            
            # Always save the text to database
            if self.call_sid:
                try:
                    from app.services.database_service import save_utterance
                    await save_utterance(self.call_sid, role, content)
                except Exception as e:
                    # Log the error but don't let it stop execution
                    logger.error(f"Error saving utterance: {e}")
                    # Continue processing even if database save fails
    
    async def _handle_deepgram_audio(self, audio_data: bytes):
        """Handle binary audio data from Deepgram"""
        if not self.stream_sid:
            logger.warning("Received audio from Deepgram but no Stream SID available")
            return
        
        try:
            # Encode the audio data to base64 for Twilio
            payload = base64.b64encode(audio_data).decode('ascii')
            
            # Create the media message
            media_message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": payload
                }
            }
            
            # Send to Twilio
            await self.websocket.send_json(media_message)
        except Exception as e:
            logger.error(f"Error sending audio to Twilio: {e}")

from app.handlers.function_handler import FINAL_AUDIO_MARK_NAME
from app.utils.twilio import end_call
from app.services.call_state_service import remove_call_state
