"""
WebSocket endpoints for handling real-time audio streams between Twilio and Deepgram
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import asyncio
import logging
import json
import os
import uuid
from dotenv import load_dotenv
import traceback

# Import services and handlers
from app.services.deepgram_service import DeepgramService
from app.handlers.audio_handler import AudioHandler
from app.handlers.chinese_audio_handler import ChineseAudioHandler
from app.utils.constants import get_restaurant_config, get_restaurant_menu

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api", tags=["websocket"])

# --- Global Storage for Active Handlers and Caller Info ---
# Use call_sid as the primary key
active_handlers: dict[str, asyncio.Task] = {}
active_call_info: dict[str, dict] = {}

# Function to safely store caller info
def store_caller_info(call_sid: str, phone: str, language: str):
    if not call_sid:
        logger.error("Attempted to store caller info with empty CallSid")
        return
    active_call_info[call_sid] = {"phone": phone, "language": language}
    logger.info(f"Stored caller info for {call_sid}: phone={phone}, language={language}")

# Function to safely retrieve caller phone
def get_caller_phone(call_sid: str) -> str:
    return active_call_info.get(call_sid, {}).get("phone")

# Function to safely retrieve language
def get_language(call_sid: str) -> str:
    return active_call_info.get(call_sid, {}).get("language")

# Function to retrieve the active handler instance by call_sid
def get_handler_instance(call_sid: str):
    task = active_handlers.get(call_sid)
    if task and hasattr(task, 'handler_instance'): # Check if the instance is stored on the task
        return task.handler_instance
    logger.warning(f"No active handler instance found for CallSid: {call_sid}")
    return None

# Function to cleanup call data
def cleanup_call_data(call_sid: str):
    if call_sid in active_handlers:
        item = active_handlers.pop(call_sid)
        task_to_cancel = None
        
        if isinstance(item, asyncio.Task):
            task_to_cancel = item
            logger.info(f"Retrieved direct task for CallSid {call_sid} for cleanup.")
        elif isinstance(item, dict) and "handler_instance" in item:
            handler_instance = item.get("handler_instance")
            if handler_instance and hasattr(handler_instance, 'google_stt_responses_task'):
                task_to_cancel = handler_instance.google_stt_responses_task
                logger.info(f"Retrieved google_stt_responses_task from handler_instance for CallSid {call_sid} for cleanup.")
            elif handler_instance:
                logger.info(f"Handler instance for {call_sid} found but no google_stt_responses_task attribute.")
            else:
                logger.warning(f"'handler_instance' key found for {call_sid}, but instance is None.")
        else:
            logger.warning(f"Popped item for CallSid {call_sid} is neither a direct task nor a recognized handler structure: {type(item)}")

        if task_to_cancel and not task_to_cancel.done():
            task_to_cancel.cancel()
            logger.info(f"Cancelled task for CallSid: {call_sid}")
        elif task_to_cancel:
            logger.info(f"Task for CallSid {call_sid} was already done or did not exist.")
            
    if call_sid in active_call_info:
        info = active_call_info.pop(call_sid)
        logger.info(f"Removed caller info for CallSid: {call_sid} - Info: {info}")

# --- End Global Storage ---

# Add this new WebSocket endpoint for the /ws/{call_sid} route
@router.websocket("/ws/{call_sid}")
async def websocket_call_handler(websocket: WebSocket, call_sid: str):
    """
    Handle WebSocket connections for Twilio Media Streams with call_sid in the URL.
    
    This endpoint is specifically designed to work with the Chinese voice agent flow,
    which uses Twilio's built-in transcription + our custom handler that doesn't use Deepgram.
    
    Args:
        websocket: The WebSocket connection
        call_sid: The Twilio Call SID from the URL path
    """
    logger.info(f"WebSocket connection request received for call_sid: {call_sid}")
    handler_instance = None
    handler_task = None
    msg_counter = 0
    
    try:
        # Accept the connection
        await websocket.accept()
        logger.info(f"WebSocket connection accepted for call_sid: {call_sid}")
        
        # Get the first message (should be the "connected" event from Twilio)
        first_message = await websocket.receive_text()
        logger.info(f"First message received for call_sid {call_sid}: {first_message[:100]}...")
        
        # Get the language preference (should be stored by handle_language_selection)
        language = get_language(call_sid)
        logger.info(f"Language retrieved for call_sid {call_sid}: {language}")
        
        # Get restaurant configuration 
        restaurant_id = os.getenv("RESTAURANT_ID", "default-restaurant")
        
        # For Chinese, use the ChineseAudioHandler without Deepgram
        if language == "chinese":
            logger.info(f"Creating ChineseAudioHandler for call_sid: {call_sid}")
            
            # Get OpenAI API key
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key:
                logger.error("OpenAI API key not found in environment variables.")
                # Close connection if cannot proceed
                await websocket.close(code=1008, reason="Configuration error")
                return
                
            # Create a system message (basic for now, can be enhanced later)
            restaurant_config = get_restaurant_config(restaurant_id)
            system_message = restaurant_config.get("SYSTEM_MESSAGE", "")
            
            # Retrieve caller_phone to pass to the handler
            caller_phone_num = get_caller_phone(call_sid)
            logger.info(f"Retrieved caller_phone for ChineseAudioHandler: {caller_phone_num} for call_sid: {call_sid}")

            # Create the Chinese handler instance
            handler_instance = ChineseAudioHandler(
                websocket=websocket,
                client_id=call_sid, # Using call_sid as client_id for this handler
                openai_api_key=openai_api_key,
                system_message=system_message,
                send_welcome_message=True,  # Enable the new agent-side welcome message
                caller_phone=caller_phone_num # Pass the retrieved caller_phone
            )
            
            # Store the handler in the active_handlers dictionary for later retrieval
            # We don't need to create a task since the Chinese handler doesn't need to 
            # process incoming audio (Twilio handles transcription)
            active_handlers[call_sid] = {"handler_instance": handler_instance}
            
            # Simple loop to keep the WebSocket open and handle any messages
            # This is mainly to process "start", "stop", etc. events from Twilio
            try:
                while True:
                    message = await websocket.receive() 
                    msg_counter += 1
                    
                    # Log message type and content (truncated) for debugging
                    msg_type = message.get("type")
                    log_content = ""
                    if msg_type == "websocket.receive":
                        if "text" in message:
                            log_content = message["text"][:150] # Log start of text
                        elif "bytes" in message:
                            log_content = f"{len(message['bytes'])} bytes" # Log byte length
                    logger.debug(f"WS Recv (Msg #{msg_counter}): Type={msg_type}, Content='{log_content}...'") # Changed to DEBUG

                    if message["type"] == "websocket.receive":
                        # Process the message with the handler
                        if "text" in message:
                            # This should handle 'start', 'media', 'stop', 'mark' events (JSON strings)
                            await handler_instance.process_twilio_message(message["text"])
                        elif "bytes" in message:
                            # Twilio Media Streams sends JSON strings, not raw bytes for media.
                            logger.error("Received raw bytes message, which is unexpected for Twilio Media Streams.")
                            # await handler_instance.process_twilio_message(message["bytes"]) # Commenting out incorrect handling
                    
                    elif message["type"] == "websocket.disconnect":
                        logger.info(f"Disconnect message received for {call_sid}")
                        break
            except WebSocketDisconnect:
                logger.warning(f"WebSocket disconnected for {call_sid}")
            except Exception as e:
                logger.error(f"Error in message loop for {call_sid}: {str(e)}", exc_info=True)
            finally:
                if handler_instance:
                    await handler_instance.cleanup()
        
        else:
            # For English or unrecognized languages, we could either:
            # 1. Close the connection with an error
            # 2. Fall back to the existing handle_media_stream logic
            # Let's just log an error and close for now
            logger.error(f"Unsupported language '{language}' for WebSocket call handler. Use /media-stream for Deepgram-based handling.")
            await websocket.close(code=1003, reason="Unsupported language")
    
    except WebSocketDisconnect:
        logger.warning(f"WebSocket disconnected for call_sid: {call_sid}")
        if handler_instance:
            await handler_instance.cleanup()
    
    except asyncio.CancelledError:
        logger.info(f"WebSocket handler task cancelled for call_sid: {call_sid}")
        if handler_instance:
            await handler_instance.cleanup()
    
    except Exception as e:
        logger.error(f"Error in WebSocket call handler for call_sid {call_sid}: {str(e)}", exc_info=True)
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except:
            pass
    
    finally:
        # Ensure resources are cleaned up
        cleanup_call_data(call_sid)
        logger.info(f"WebSocket connection closed for call_sid: {call_sid}")

# Redundant caller_info and its functions were removed.
# All language and caller phone retrieval should use the global
# active_call_info dictionary and its helper functions:
# get_language(call_sid) and get_caller_phone(call_sid)

def get_system_message(restaurant_id: str) -> str:
    """
    Get the system message for the given restaurant ID
    
    Args:
        restaurant_id: The ID of the restaurant
        
    Returns:
        str: The system message
    """
    restaurant_config = get_restaurant_config(restaurant_id)
    
    # Get the base system message from the restaurant_config (defined in app/constants.py)
    base_system_message = restaurant_config.get("SYSTEM_MESSAGE", "") # Simpler fallback to empty string

    menu_items = get_restaurant_menu(restaurant_id)
    formatted_menu_text = ""

    if menu_items:
        formatted_menu_text = "\n\nMENU ITEMS:\n" # Start the menu section
        for item in menu_items:
            name = item.get("name", "N/A")
            price = item.get("price", "N/A") 
            variations_str = ""
            if isinstance(item.get("variations"), dict) and item.get("variations"):
                var_list = [f"{v_name} (${v_price})" for v_name, v_price in item.get("variations").items()]
                variations_str = " (Variations: " + ", ".join(var_list) + ")"
            elif isinstance(item.get("variations"), list) and item.get("variations"):
                var_list = []
                for var_item_dict in item.get("variations"):
                    v_name = var_item_dict.get("name")
                    v_price = var_item_dict.get("price")
                    if v_name and v_price:
                        var_list.append(f"{v_name} (${v_price})")
                if var_list:
                    variations_str = " (Variations: " + ", ".join(var_list) + ")"
            
            if variations_str:
                 formatted_menu_text += f"- {name}{variations_str}\n"
            else:
                 formatted_menu_text += f"- {name}: ${price}\n"
    else:
        formatted_menu_text = "\n\nMENU ITEMS:\nNo items currently available.\n"
        
    # Append the dynamically formatted menu to the base system message.
    final_system_message = base_system_message + formatted_menu_text
        
    return final_system_message.strip()

@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """
    Handle Twilio Media Streams via WebSocket
    
    Args:
        websocket: The WebSocket connection
    """
    # Track current handler and language state
    current_handler = None
    current_language = None
    handler_tasks = []
    restaurant_id = None
    call_sid = None
    stream_sid = None
    
    try:
        # Accept the WebSocket connection
        await websocket.accept()
        logger.info("WebSocket connection accepted")
        
        # Get the first message to determine call parameters
        first_message = await websocket.receive_text()
        data = json.loads(first_message)
        logger.info(f"First message received: {json.dumps(data)}")
        
        # Extract basic parameters
        stream_sid = data.get("streamSid")
        custom_parameters = data.get("customParameters", {})
        logger.info(f"Stream SID: {stream_sid}, Custom parameters: {custom_parameters}")
        
        # Initialize variables
        language = "english"  # Default language
        start_data = None
        call_sid = None # Initialize call_sid
        
        # Process the first message (which should be 'connected', but we need 'start' for call_sid and custom_parameters)
        # The 'data' variable already holds the first message (json.loads(first_message))
        
        # Ensure we get the 'start' event to extract call_sid and custom parameters
        if data.get("event") == "start":
            start_data = data
        else: # If first message wasn't 'start', loop to find it
            logger.info("First message was not 'start', looking for start event...")
            for _ in range(5): # Try a few times
                try:
                    message_text = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                    start_data_candidate = json.loads(message_text)
                    if start_data_candidate.get("event") == "start":
                        start_data = start_data_candidate
                        logger.info(f"Found 'start' event: {json.dumps(start_data)}")
                        break
                except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect) as e:
                    logger.warning(f"Error or timeout waiting for start event: {e}")
                    break # Exit loop on error or disconnect
            if not start_data:
                logger.error("Could not find 'start' event. Closing connection.")
                await websocket.close(code=1008, reason="Start event not received")
                return

        # Extract call_sid from start_data
        call_sid = start_data.get("start", {}).get("callSid")
        if not call_sid:
            logger.error("call_sid missing in start event. Closing connection.")
            await websocket.close(code=1008, reason="call_sid missing")
            return

        # Determine restaurant_id: Custom Params > Env Var > Default
        # For testing, we will temporarily ignore customParameters and force LIMF or ENV
        custom_params_from_start = start_data.get("start", {}).get("customParameters", {})
        
        # For testing purposes, unconditionally use "LIMF" for the English agent path
        # This overrides customParameters and environment variables for restaurant_id selection.
        restaurant_id = "LIMF"
        logger.info(f"Hardcoding restaurant_id to 'LIMF' for testing purposes.")

        # Determine language
        language_from_custom = custom_params_from_start.get("language")
        if language_from_custom:
            language = language_from_custom
            logger.info(f"Using language from customParameters: {language}")
        elif call_sid: # Fallback to global active_call_info if call_sid is available
             # This assumes store_caller_info was called by a preceding endpoint (e.g., /select-language)
            language_from_global = get_language(call_sid)
            if language_from_global:
                language = language_from_global
                logger.info(f"Retrieved language preference from global active_call_info: {language} for call {call_sid}")
            else:
                logger.info(f"No language in customParameters or global_active_info for {call_sid}, using default: {language}")
        else:
            logger.info(f"No language in customParameters and no call_sid for global lookup, using default: {language}")
                
        # Update current language
        # Since this /media-stream endpoint is now exclusively for English after initial selection,
        # we will enforce English here.
        current_language = "english"
        logger.info(f"Ensuring language is English for /media-stream handler: {current_language}")
        
        # Get Deepgram API key from environment
        deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        if not deepgram_api_key:
            logger.error("Deepgram API key not found in environment")
            return
            
        # Get OpenAI API key from environment (for Chinese processing)
        openai_api_key = os.getenv("OPENAI_API_KEY")
        
        # Prepare the system message
        enhanced_system_message = get_system_message(restaurant_id)
        
        # Define function definitions based on language
        function_def_english = {
            "name": "order_summary", # Corrected name
            "description": "Provides a structured summary of the customer's order for backend processing. Use 'IN PROGRESS' for partial orders and 'DONE' when the order is complete and confirmed by the customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of items in the order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Name of the item."},
                                "quantity": {"type": "integer", "description": "Quantity of the item."},
                                "variation": {"type": ["string", "null"], "description": "Selected variation of the item, if any."}
                            },
                            "required": ["name", "quantity"]
                        }
                    },
                    "total_price": {
                        "type": "number",
                        "description": "The total price of the order."
                    },
                    "summary": {
                        "type": "string",
                        "description": "Status of the order summary.",
                        "enum": ["IN PROGRESS", "DONE"]
                    }
                },
                "required": ["items", "total_price", "summary"]
            }
        }
        
        
        # Initialize based on language
        # This block is now simplified as /media-stream is English-only.
        # The 'if current_language == "chinese":' block is removed.

        # English - use Deepgram
        logger.info("Creating English audio handler with Deepgram")
        
        # Create Deepgram configuration
        deepgram_config = {
            "type": "SettingsConfiguration",
            "audio": {
                "input": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                },
                "output": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                    "container": "none",
                }
            },
            "agent": {
                "listen": {
                    "model": "nova-3"  # Always use Nova-3 for English
                },
                "think": {
                    "provider": {
                        "type": "open_ai"
                    },
                    "model": "gpt-4o",
                    "instructions": enhanced_system_message,
                },
                "speak": {
                    "model": "aura-asteria-en"  # English voice model
                }
            }
        }
        
        # Add function definitions for English
        deepgram_config["agent"]["think"]["functions"] = [function_def_english]
        
        # Create DeepgramService
        deepgram_service = DeepgramService(
            api_key=deepgram_api_key,
            config=deepgram_config
        )
        
        # Initialize AudioHandler
        current_handler = AudioHandler(
            websocket=websocket,
            client_id=restaurant_id, # Use the correctly determined restaurant_id
            deepgram_api_key=deepgram_api_key,
            system_message=enhanced_system_message, # This is already enhanced by get_system_message
            function_definitions=[function_def_english],
            language="english"
        )
        
        # Set the DeepgramService
        current_handler.deepgram_service = deepgram_service
        
        # Initialize with start event if available
        if start_data:
            await current_handler._handle_start_event(start_data)
        
        # Connect to Deepgram
        await deepgram_service.connect()
        
        # Start deepgram processing task
        deepgram_task = asyncio.create_task(current_handler.process_deepgram_responses())
        handler_tasks.append(deepgram_task)
            
        # Main message processing loop - this is the only place that reads from the WebSocket
        while True:
            try:
                # This is the single point of receiving from the WebSocket
                message = await websocket.receive()
                
                # Handle message based on type
                if "text" in message:
                    try:
                        # Parse JSON message
                        data = json.loads(message["text"])
                        logger.debug(f"Received message: {json.dumps(data)}")
                        
                        # In-call language switching is removed.
                        # The 'switch_handler' message type is no longer processed here.
                        
                        # Forward all other messages to the current handler
                        await current_handler.process_twilio_message(message["text"])
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse JSON message: {message['text']}")
                    except Exception as e:
                        logger.error(f"Error processing text message: {e}", exc_info=True)
                elif "bytes" in message:
                    # Handle binary data (containing audio)
                    try:
                        # Process binary messages which contain the audio data
                        binary_data = message["bytes"]
                        
                        # Different handling based on handler type
                        if isinstance(current_handler, ChineseAudioHandler):
                            # For Chinese handler, pass raw binary data directly
                            logger.debug(f"Forwarding raw binary data to Chinese handler ({len(binary_data)} bytes)") # Changed to DEBUG
                            await current_handler.process_twilio_message(binary_data)
                        else:
                            # For other handlers, use the standard format
                            import base64
                            # Convert to base64 as Twilio expects
                            payload = base64.b64encode(binary_data).decode('utf-8')
                            
                            # Create a media message format
                            media_message = {
                                "event": "media",
                                "media": {
                                    "payload": payload
                                }
                            }
                            
                            # Forward to audio handler
                            logger.debug(f"Forwarding binary message as media event ({len(binary_data)} bytes)") # Changed to DEBUG
                            await current_handler.process_twilio_message(json.dumps(media_message))
                    except Exception as e:
                        logger.error(f"Error processing binary message: {e}", exc_info=True)
                else:
                    # Unknown message type
                    logger.warning(f"Received unknown message type: {message.keys()}")
            except RuntimeError as e:
                # Handle disconnect message gracefully
                if "disconnect message has been received" in str(e):
                    logger.info("WebSocket disconnected by client after sending data")
                    break
                else:
                    # Re-raise other RuntimeErrors
                    raise
    
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except asyncio.CancelledError:
        logger.info("WebSocket connection cancelled")
    except Exception as e:
        logger.error(f"Error in handle_media_stream: {e}", exc_info=True)
    finally:
        # Clean up tasks
        for task in handler_tasks:
            if not task.done():
                task.cancel()
        
        try:
            await asyncio.gather(*handler_tasks, return_exceptions=True)
        except:
            pass
        
        # Close the websocket
        try:
            await websocket.close(code=1000, reason="Connection closed")
        except:
            pass
        
        # Clean up call data
        cleanup_call_data(call_sid)

async def initialize_handler(language, websocket, restaurant_id, deepgram_api_key, 
                             openai_api_key, enhanced_system_message, function_def_english,
                             function_def_chinese, start_data):
    """
    Initialize the appropriate audio handler based on language
    
    Args:
        language: The language to use (english or chinese)
        websocket: The WebSocket connection
        restaurant_id: The restaurant ID
        deepgram_api_key: Deepgram API key
        openai_api_key: OpenAI API key (for Chinese processing)
        enhanced_system_message: System message for the handler
        function_def_english: Function definition for English
        function_def_chinese: Function definition for Chinese
        start_data: Start event data if available
    
    Returns:
        tuple: (handler, tasks) - The created handler and associated tasks
    """
    handler_tasks = []
    
    if language == "chinese":
        # Check required API keys for Chinese processing
        if not openai_api_key:
            logger.error("OpenAI API key missing - required for Chinese audio processing")
            await websocket.close(code=1000, reason="Missing OpenAI API key")
            return None, []
            
        # Create Chinese audio handler
        logger.info("Creating Chinese audio handler with OpenAI")
        chinese_handler = ChineseAudioHandler(
            websocket=websocket,
            client_id=restaurant_id,
            openai_api_key=openai_api_key,
            system_message=enhanced_system_message,
            verbose_logging=False  # Disable verbose logging by default to reduce noise
        )
        
        # Handle start event if provided
        if start_data:
            await chinese_handler._handle_start_event(start_data)
        
        # Process in separate task
        chinese_task = asyncio.create_task(chinese_handler.process_audio_stream())
        handler_tasks.append(chinese_task)
        
        return chinese_handler, handler_tasks
    else:
        # English language path - use Deepgram
        logger.info("Creating English audio handler with Deepgram")
        
        # Choose appropriate function definition
        function_def = function_def_english
        
        # Create Deepgram configuration
        deepgram_config = {
            "type": "SettingsConfiguration",
            "audio": {
                "input": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                },
                "output": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                    "container": "none",
                }
            },
            "agent": {
                "listen": {
                    "model": "nova-3"  # Always use Nova-3 for English
                },
                "think": {
                    "provider": {
                        "type": "open_ai"
                    },
                    "model": "gpt-4o",
                    "instructions": enhanced_system_message,
                },
                "speak": {
                    "model": "aura-asteria-en"  # English voice model
                }
            }
        }
        
        # Add function definitions for English
        deepgram_config["agent"]["think"]["functions"] = [function_def]
        
        # Create DeepgramService
        deepgram_service = DeepgramService(
            api_key=deepgram_api_key,
            config=deepgram_config
        )
        
        # Initialize AudioHandler
        audio_handler = AudioHandler(
            websocket=websocket,
            client_id=restaurant_id,
            deepgram_api_key=deepgram_api_key,
            system_message=enhanced_system_message,
            function_definitions=[function_def],
            language="english"
        )
        
        # Set the DeepgramService
        audio_handler.deepgram_service = deepgram_service
        
        # Handle the start event if provided
        if start_data:
            await audio_handler._handle_start_event(start_data)
        
        # Connect to Deepgram
        await deepgram_service.connect()
        
        # Create tasks for parallel processing
        twilio_task = asyncio.create_task(audio_handler.process_twilio_messages())
        deepgram_task = asyncio.create_task(audio_handler.process_deepgram_responses())
        
        handler_tasks.extend([twilio_task, deepgram_task])
        
        return audio_handler, handler_tasks
