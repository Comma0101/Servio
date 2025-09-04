"""
WebSocket endpoints for handling real-time audio streams between Twilio and Deepgram
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from websockets.exceptions import ConnectionClosed
import asyncio
import logging
import json
import os
import uuid
from dotenv import load_dotenv
import traceback

# Import services and handlers
from app.services.deepgram_service import DeepgramService
from app.handlers.deepgram_english_audio_handler_refactored import DeepgramEnglishAudioHandler
from app.handlers.chinese_audio_openai_handler import ChineseAudioOpenAIHandler
from app.handlers.chinese_audio_bytedance_handler import ChineseAudioByteDanceHandler
from app.utils.constants import get_restaurant_config, get_restaurant_menu
from app.handlers.common_tool_defs import (
    ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
    CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
    LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
    RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
    GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
    SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
    START_COMBO_ORDER_TOOL_SCHEMA,
    PROCESS_COMBO_SELECTION_TOOL_SCHEMA
)
try:
    from google.cloud import texttospeech_v1 as texttospeech
    GOOGLE_TTS_SDK_AVAILABLE = True
except ImportError:
    GOOGLE_TTS_SDK_AVAILABLE = False
    texttospeech = None # type: ignore
    # Note: Logging for this import failure is handled by the ChineseAudioDeepgramGoogleHandler itself
    # or later in this file if a logger instance is available at that point.
from pathlib import Path # Import Path

# Load environment variables
# Explicitly load .env from the 'app' directory
dotenv_path = Path(__file__).parent.parent / '.env' # Assumes .env is in the 'app' directory
load_dotenv(dotenv_path=dotenv_path)
logger = logging.getLogger(__name__) # Get logger before using it
logger.info(f"Attempting to load .env file from: {dotenv_path}")
if dotenv_path.exists():
    logger.info(".env file found.")
else:
    logger.warning(".env file NOT found at the specified path. Environment variables might not be loaded.")


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api", tags=["websocket"])

import redis

# --- Global Storage for Active Handlers and Caller Info ---
# Use call_sid as the primary key
active_handlers: dict[str, asyncio.Task] = {}

# Initialize Redis client
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_client = redis.Redis(host=redis_host, port=6379, db=0)

# Function to safely store caller info
def store_caller_info(call_sid: str, phone: str, language: str):
    if not call_sid:
        logger.error("Attempted to store caller info with empty CallSid")
        return
    caller_info = json.dumps({"phone": phone, "language": language})
    redis_client.set(call_sid, caller_info)
    logger.info(f"Stored caller info for {call_sid} in Redis: {caller_info}")

# Function to safely retrieve caller phone
def get_caller_phone(call_sid: str) -> str:
    caller_info_json = redis_client.get(call_sid)
    if caller_info_json:
        caller_info = json.loads(caller_info_json)
        return caller_info.get("phone")
    return None

# Function to safely retrieve language
def get_language(call_sid: str) -> str:
    caller_info_json = redis_client.get(call_sid)
    if caller_info_json:
        caller_info = json.loads(caller_info_json)
        return caller_info.get("language")
    return None

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
            
    if redis_client.exists(call_sid):
        redis_client.delete(call_sid)
        logger.info(f"Removed caller info for CallSid: {call_sid} from Redis")

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
        
        # Loop until we get the 'start' event to extract customParameters
        start_event_data = None
        custom_params: Dict[str, Any] = {}
        
        # Try to receive messages until 'start' event is found or timeout/error
        for _ in range(5): # Try a few times to get the start event
            message_text = await websocket.receive_text()
            msg_counter +=1
            # logger.info(f"Received message #{msg_counter} for call_sid {call_sid}: {message_text[:200]}...")
            try:
                message_data = json.loads(message_text)
                event_type = message_data.get("event")

                if event_type == "start":
                    start_event_data = message_data.get("start", {})
                    custom_params = start_event_data.get("customParameters", {})
                    logger.info(f"Found 'start' event with customParameters: {custom_params} for call_sid {call_sid}")
                    # Pass this start message to the handler later
                    # For now, break the loop as we have the params
                    break 
                elif event_type == "connected":
                    logger.info(f"'connected' event received for call_sid {call_sid}. Waiting for 'start' event.")
                else:
                    logger.warning(f"Unexpected event '{event_type}' received while waiting for 'start' for call_sid {call_sid}.")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse message as JSON for call_sid {call_sid} while waiting for 'start': {message_text}")
                await websocket.close(code=1008, reason="Invalid message format")
                return
            except Exception as e_loop:
                logger.error(f"Error in start event loop for call_sid {call_sid}: {e_loop}")
                await websocket.close(code=1011, reason="Error processing initial messages")
                return
        
        if not start_event_data:
            logger.error(f"Did not receive 'start' event for call_sid {call_sid} after {msg_counter} messages. Closing connection.")
            await websocket.close(code=1008, reason="Start event not received")
            return

        restaurant_id = "LIMF" # Default
        language = "chinese" # Default for this handler

        # Parameters are now in custom_params because the loop above ensures start_event_data is populated.
        # Default values for restaurant_id and language are set above.
        
        restaurant_id_from_stream = custom_params.get("restaurant_id")
        if restaurant_id_from_stream:
            restaurant_id = restaurant_id_from_stream
            logger.info(f"Retrieved restaurant_id '{restaurant_id}' from stream customParameters for call_sid {call_sid}.")
        else:
            logger.warning(f"restaurant_id not found in stream customParameters for call_sid {call_sid}. Using default: {restaurant_id}")

        language_from_stream = custom_params.get("language")
        if language_from_stream:
            language = language_from_stream
            logger.info(f"Retrieved language '{language}' from stream customParameters for call_sid {call_sid}.")
        else:
            # Fallback to globally stored language if not in stream params (custom_params might be empty if start event had no customParameters)
            language_from_global = get_language(call_sid)
            if language_from_global:
                language = language_from_global
                logger.info(f"Retrieved language '{language}' from global store for call_sid {call_sid}.")
            else:
                logger.warning(f"Language not found in stream customParameters or global store for call_sid {call_sid}. Using default: {language}")

        if language == "chinese":
            chinese_handler_type = custom_params.get("chinese_handler_type", "bytedance") # Default to bytedance
            logger.info(f"Language is 'chinese'. Selected handler type: {chinese_handler_type} for call_sid: {call_sid}")

            openai_api_key_env = os.getenv("OPENAI_API_KEY")
            if not openai_api_key_env:
                logger.error(f"OPENAI_API_KEY not found. This is required for NLU for all Chinese handlers. Call_sid: {call_sid}.")
                await websocket.close(code=1011, reason="Server configuration error: OpenAI API key for NLU not available.")
                return

            restaurant_config = get_restaurant_config(restaurant_id)
            system_message_cn = restaurant_config.get("SYSTEM_MESSAGE_CN", restaurant_config.get("SYSTEM_MESSAGE", ""))
            caller_phone_num = get_caller_phone(call_sid)

            if chinese_handler_type == "bytedance":
                logger.info(f"Attempting to create ChineseAudioByteDanceHandler for call_sid: {call_sid}")
                # Fetch Bytedance API keys
                bytedance_stt_app_key = os.getenv("BYTEDANCE_STT_APP_KEY")
                bytedance_stt_access_key = os.getenv("BYTEDANCE_STT_ACCESS_KEY")
                bytedance_stt_resource_id = os.getenv("BYTEDANCE_STT_RESOURCE_ID")
                bytedance_tts_appid = os.getenv("BYTEDANCE_TTS_APPID")
                bytedance_tts_token = os.getenv("BYTEDANCE_TTS_TOKEN")

                if not all([bytedance_stt_app_key, bytedance_stt_access_key, bytedance_stt_resource_id, bytedance_tts_appid, bytedance_tts_token]):
                    logger.error(f"One or more Bytedance API keys/IDs not found in environment. Cannot create ChineseAudioByteDanceHandler. Call_sid: {call_sid}")
                    await websocket.close(code=1011, reason="Server configuration error: Bytedance API credentials missing.")
                    return
                
                handler_instance = ChineseAudioByteDanceHandler(
                    websocket=websocket,
                    openai_api_key=openai_api_key_env, # For NLU
                    bytedance_stt_app_key=bytedance_stt_app_key,
                    bytedance_stt_access_key=bytedance_stt_access_key,
                    bytedance_stt_resource_id=bytedance_stt_resource_id,
                    bytedance_tts_appid=bytedance_tts_appid,
                    bytedance_tts_token=bytedance_tts_token,
                    send_welcome_message=True,
                    caller_phone=caller_phone_num,
                    client_id=restaurant_id,
                    system_message=system_message_cn,
                    verbose_logging=True
                )
            else: # Default to OpenAI handler
                logger.info(f"Attempting to create ChineseAudioOpenAIHandler (default) for call_sid: {call_sid}")
                handler_instance = ChineseAudioOpenAIHandler(
                    websocket=websocket,
                    openai_api_key=openai_api_key_env,
                    send_welcome_message=True,
                    caller_phone=caller_phone_num,
                    client_id=restaurant_id,
                    system_message=system_message_cn,
                    verbose_logging=True
                )
            
            active_handlers[call_sid] = {"handler_instance": handler_instance}

            if message_data and message_data.get("event") == "start":
                logger.info(f"Processing pre-fetched 'start' event for call_sid {call_sid} with {type(handler_instance).__name__}.")
                await handler_instance.on_twilio_message(json.dumps(message_data))
            else:
                logger.error(f"Logic error: 'start' event data not available after initial loop for call_sid {call_sid}")

            # Continue processing subsequent messages
            # Ensure starlette.websockets.WebSocketState is available
            try:
                from starlette.websockets import WebSocketState
            except ImportError:
                class WebSocketState: CONNECTING = 0; CONNECTED = 1; DISCONNECTED = 2 # Basic fallback

            try:
                # Keep-alive task
                async def keep_alive(ws: WebSocket):
                    while True:
                        await asyncio.sleep(20)
                        try:
                            await ws.send_text(json.dumps({"event": "keep-alive"}))
                        except (WebSocketDisconnect, ConnectionClosed):
                            logger.info("Keep-alive failed: WebSocket is closed.")
                            break
                        except Exception as e:
                            logger.error(f"Error in keep-alive task: {e}", exc_info=True)
                            break

                async def handle_subsequent_messages(ws: WebSocket, handler, counter, sid):
                    while not handler.connection_closed_event.is_set():
                        try:
                            message = await ws.receive_text()
                            counter += 1
                            # logger.info(f"Received message #{counter} for call_sid {sid}: {message[:200]}...")
                            if not handler.connection_closed_event.is_set():
                                await handler.on_twilio_message(message)
                        except WebSocketDisconnect:
                            logger.warning(f"WebSocket disconnected for {sid} (msg_counter: {counter}) - caught by inner try.")
                            break
                        except Exception as e:
                            logger.error(f"Error in message loop for {sid} (msg_counter: {counter}): {str(e)}", exc_info=True)
                            break
                
                keep_alive_task = asyncio.create_task(keep_alive(websocket))
                message_handling_task = asyncio.create_task(handle_subsequent_messages(websocket, handler_instance, msg_counter, call_sid))

                done, pending = await asyncio.wait(
                    [keep_alive_task, message_handling_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()
                
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            except WebSocketDisconnect:
                logger.warning(f"WebSocket disconnected for {call_sid} (msg_counter: {msg_counter}) - caught by outer try.")
            except Exception as e:
                logger.error(f"Error in message loop for {call_sid} (msg_counter: {msg_counter}): {str(e)}", exc_info=True)
            finally:
                if handler_instance:
                    logger.info(f"Ensuring handler cleanup for {call_sid} in websocket_call_handler's finally block.")
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
        
    # For the new tool-based approach, the SYSTEM_MESSAGE from constants.py now contains tool descriptions.
    # We should not append the formatted menu directly to it anymore for the English agent.
    # The menu will be queried via tools.
    logger.info(f"Using tool-based SYSTEM_MESSAGE for restaurant {restaurant_id}. Full menu will not be appended to the prompt.")
    return base_system_message.strip()

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
        custom_params_from_start = start_data.get("start", {}).get("customParameters", {})
        
        # Get restaurant_id from custom parameters passed in the <Stream>
        # Fallback to environment variable, then to "LIMF" if not found
        restaurant_id_from_stream = custom_params_from_start.get("restaurant_id")
        restaurant_id = restaurant_id_from_stream or os.getenv("RESTAURANT_ID", "LIMF")
        logger.info(f"Using restaurant_id: {restaurant_id} for English agent stream.")

        # Determine language
        language_from_custom = custom_params_from_start.get("language") # language is also passed as a stream param
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
        # These are now imported from common_tool_defs.py
        all_english_function_definitions = [
            ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
            CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
            LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
            RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
            GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
            SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
            START_COMBO_ORDER_TOOL_SCHEMA,
            PROCESS_COMBO_SELECTION_TOOL_SCHEMA
        ]
        
        # Initialize based on language
        # This block is now simplified as /media-stream is English-only.
        # The 'if current_language == "chinese":' block is removed.

        # English - use Deepgram
        logger.info("Creating English audio handler with Deepgram")
        
        # Create Deepgram configuration for V1 API
        # Restoring top-level "type": "Settings" and provider, but removing api_key from provider.
        logger.warning("TEMP DEBUG: Restoring 'type: Settings', provider, but NO api_key in provider.")
        deepgram_config = {
            "type": "Settings",
            "audio": {
                "input": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                },
                "output": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                    "container": "none", # This field is valid for V1
                }
            },
            "agent": {
                "listen": {
                    "provider": {
                        "type": "deepgram"
                    }
                },
                "think": {
                    "provider": { 
                        "type": "open_ai",
                        "model": "gpt-4o-mini" 
                    },
                    "prompt": enhanced_system_message, # Changed from "instructions" to "prompt" per V1 guide
                    "functions": all_english_function_definitions
                },
                "speak": {
                    "provider": {
                        "type": "deepgram", 
                        "model": "aura-asteria-en" 
                    }
                }
            }
        }
        
        # Initialize DeepgramEnglishAudioHandler first to get its stop_event
        current_handler = DeepgramEnglishAudioHandler(
            websocket=websocket,
            client_id=restaurant_id, # Use the correctly determined restaurant_id
            deepgram_api_key=deepgram_api_key,
            system_message=enhanced_system_message, # This is already enhanced by get_system_message
            function_definitions=all_english_function_definitions,
            language="english"
        )

        # Create DeepgramService and pass the handler's stop_event to it
        deepgram_service = DeepgramService(
            api_key=deepgram_api_key,
            config=deepgram_config,
            stop_event=current_handler.stop_event
        )
        
        # Set the DeepgramService on the handler
        current_handler.deepgram_service = deepgram_service
        
        # Initialize with start event if available
        if start_data:
            await current_handler._handle_start_event(start_data)
        
        # Connect to Deepgram
        await deepgram_service.connect()
        
        # Start handler tasks
        twilio_task = asyncio.create_task(current_handler.process_twilio_messages())
        deepgram_task = asyncio.create_task(current_handler.process_deepgram_responses())
        handler_tasks.extend([twilio_task, deepgram_task])

        # Keep-alive task
        async def keep_alive(ws: WebSocket):
            while True:
                await asyncio.sleep(20)
                try:
                    await ws.send_text(json.dumps({"event": "keep-alive"}))
                except (WebSocketDisconnect, ConnectionClosed):
                    logger.info("Keep-alive failed: WebSocket is closed.")
                    break
                except Exception as e:
                    logger.error(f"Error in keep-alive task: {e}", exc_info=True)
                    break

        keep_alive_task = asyncio.create_task(keep_alive(websocket))
        handler_tasks.append(keep_alive_task)

        # Wait for any task to complete. The session is over when Twilio stops sending media.
        done, pending = await asyncio.wait(handler_tasks, return_when=asyncio.FIRST_COMPLETED)

        # Identify which task finished
        finished_task = done.pop()
        logger.info(f"Task finished, triggering shutdown: {finished_task}")

        # If the Twilio task is the one that finished, we know the media stream has ended.
        # We should give the Deepgram task a moment to process any final messages before cancelling.
        if finished_task is twilio_task:
            logger.info("Twilio processing finished. Giving Deepgram task a grace period...")
            # Find the deepgram_task in the pending set
            deepgram_task_in_pending = next((t for t in pending if t is deepgram_task), None)
            if deepgram_task_in_pending:
                try:
                    # Wait for a short period to allow final messages to be processed.
                    await asyncio.wait_for(deepgram_task_in_pending, timeout=2.0)
                except asyncio.TimeoutError:
                    logger.info("Deepgram task grace period timed out. Proceeding with cancellation.")
                except asyncio.CancelledError:
                    pass # Task was already cancelled, which is fine.

        # Cancel any remaining pending tasks to ensure graceful shutdown
        for task in pending:
            if not task.done():
                task.cancel()
        
        # Await the pending tasks to allow them to process the cancellation
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
        if current_handler:
            current_handler.connection_closed.set()
    except asyncio.CancelledError:
        logger.info("WebSocket connection cancelled")
    except Exception as e:
        logger.error(f"Error in handle_media_stream: {e}", exc_info=True)
    finally:
        # The main `try` block now handles task cancellation.
        # This `finally` block is for resource cleanup.
        if current_handler:
            current_handler.connection_closed.set()

        # Close the Deepgram service connection
        if current_handler and hasattr(current_handler, 'deepgram_service') and current_handler.deepgram_service:
            logger.info("Closing Deepgram service connection in finally block.")
            await current_handler.deepgram_service.close()
        
        # Close the websocket
