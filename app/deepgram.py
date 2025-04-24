import os
import json
import base64
import asyncio
import websockets
import time
import logging
import asyncpg
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from app.constants import CONSTANTS
from app.utils.square import test_create_order_endpoint, test_payment_processing
from app.utils.twilio import send_sms, get_call_details
from app.utils.database import init_db, save_call_start, save_call_end, save_utterance, upload_audio_to_s3
from app.routers import square
import inspect
from app.config import settings
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# PostgreSQL connection settings
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "servio")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")

# Configuration from constants.py
RESTAURANT_CONFIG = CONSTANTS.get("LIMF", {})
DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = RESTAURANT_CONFIG.get("SYSTEM_MESSAGE")

# Voice mapping
TWILIO_VOICE = RESTAURANT_CONFIG.get("TWILIO_VOICE", "Polly.Joanna-Neural")
MENU = RESTAURANT_CONFIG.get("MENU", "[]")
ORDER_SUMMARY_TOOL = RESTAURANT_CONFIG.get("OPENAI_CHAT_TOOLS", [])[0] if RESTAURANT_CONFIG.get("OPENAI_CHAT_TOOLS") else None

app = FastAPI()

# Include square router for API endpoints
app.include_router(square.router, prefix="/api/v1", tags=["Square"])

# Initialize the database at startup
@app.on_event("startup")
async def startup_db_client():
    """Initialize the database connection on app startup."""
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

if not DEEPGRAM_API_KEY:
    raise ValueError('Missing the Deepgram API key. Please set DEEPGRAM_API_KEY in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    """Root endpoint that returns a simple status message."""
    return {"message": "Twilio Media Stream Server with Deepgram is running!"}

@app.get("/api/debug/calls")
async def get_calls():
    """Debug endpoint to list recent calls"""
    try:
        pool = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            host=DB_HOST,
            port=DB_PORT
        )
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM calls ORDER BY start_time DESC LIMIT 10")
            calls = [dict(row) for row in rows]
            return {"calls": calls}
    except Exception as e:
        logger.error(f"Error fetching calls: {e}")
        return {"error": str(e)}

@app.get("/api/debug/utterances")
async def get_utterances():
    """Debug endpoint to list recent utterances"""
    try:
        pool = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            host=DB_HOST,
            port=DB_PORT
        )
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.*, c.caller_number 
                FROM utterances u
                JOIN calls c ON u.call_sid = c.call_sid
                ORDER BY u.timestamp DESC LIMIT 20
            """)
            utterances = [dict(row) for row in rows]
            return {"utterances": utterances}
    except Exception as e:
        logger.error(f"Error fetching utterances: {e}")
        return {"error": str(e)}

        
@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """
    Handle incoming call and return TwiML response to connect to Media Stream.
    
    Args:
        request (Request): The FastAPI request object
        
    Returns:
        HTMLResponse: TwiML response for Twilio
    """
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    response.say("Please wait while we connect your call to the KK restaurant assistant", voice=TWILIO_VOICE)
    response.pause(length=1)
    
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

def create_settings_configuration():
    """
    Create the settings configuration for Deepgram Voice Agent API with function calling.
    
    Returns:
        dict: Configuration for Deepgram API
    """
    # Enhance system message with menu data
    enhanced_system_message = SYSTEM_MESSAGE
    
    # Add menu to system message for context
    try:
        menu_items = json.loads(MENU)
        logger.info(f"Total menu items: {len(menu_items)}")
        
        # Add detailed logging for each menu item
        for i, item in enumerate(menu_items):
            logger.info(f"MENU ITEM {i+1}: Name={item.get('name')}, Variations={len(item.get('variations', []))}")
            for j, variation in enumerate(item.get('variations', [])):
                logger.info(f"  - Variation {j+1}: {variation.get('name')}, Price=${variation.get('price')}")
        
        # Build a more detailed menu text with correct prices
        menu_text = "\n\nMENU ITEMS:\n"
        for item in menu_items:
            name = item.get("name", "Unknown item")
            variations = item.get("variations", [])
            
            # Handle menu items with variations
            if variations:
                for variation in variations:
                    var_name = variation.get("name", "")
                    var_price = variation.get("price", 0)
                    menu_text += f"{name} ({var_name}): ${var_price}\n"
            else:
                # For items without variations (though it seems all items have variations)
                price = item.get("price", 0)
                menu_text += f"{name}: ${price}\n"
        
        logger.info("Menu text added to system message")
        enhanced_system_message += menu_text
    except Exception as e:
        logger.error(f"Error processing menu items: {e}")
    
    # Add strong prefix to prioritize our instructions
    enhanced_system_message = "OVERRIDE DEFAULT INSTRUCTIONS. YOU MUST FOLLOW ONLY THESE INSTRUCTIONS: " + enhanced_system_message
    
    # Use the order_summary function definition from constants.py if available
    function_def = None
    if ORDER_SUMMARY_TOOL:
        logger.info("Using order_summary function definition from constants.py")
        function_def = {
            "name": ORDER_SUMMARY_TOOL["name"],
            "description": ORDER_SUMMARY_TOOL["description"],
            "parameters": ORDER_SUMMARY_TOOL["parameters"]
        }
    else:
        logger.warning("Order summary function not found in constants.py, using default definition")
        function_def = {
            "name": "order_summary",
            "description": "Create a summary of the customer's food order including items, quantities, and variations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "The name of the menu item"},
                                "quantity": {"type": "integer", "description": "The quantity of the item ordered"},
                                "variation": {"type": "string", "description": "Any variations or customizations of the item"}
                            },
                            "required": ["name", "quantity"]
                        },
                        "description": "List of items in the order"
                    },
                    "total_price": {"type": "number", "description": "The total price of the order before tax"},
                    "summary": {"type": "string", "enum": ["IN PROGRESS", "DONE"], "description": "The status of the order"}
                },
                "required": ["items", "total_price", "summary"]
            }
        }
    
    return {
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
            },
        },
        "agent": {
            "listen": {"model": "nova-3"},
            "think": {
                "provider": {
                    "type": "anthropic",  # You can also use OpenAI or other supported models
                },
                "model": "claude-3-haiku-20240307",
                "instructions": enhanced_system_message,  # Use enhanced system message with menu
                "functions": [function_def]
            },
            "speak": {"model": "aura-asteria-en"},
        },
    }

def format_menu_for_sms():
    """
    Format the menu items for sending via SMS.
    
    Returns:
        str: Formatted menu text for SMS
    """
    try:
        # Get menu from constants
        menu_items = json.loads(CONSTANTS["LIMF"]["MENU"])
        logger.info(f"Formatting menu with {len(menu_items)} items for SMS")
        
        # Add detailed logging for menu items being formatted for SMS
        for i, item in enumerate(menu_items):
            variations = item.get("variations", [])
            logger.info(f"SMS MENU ITEM {i+1}: {item.get('name')} with {len(variations)} variations")
            
        # Format exactly as required by the API
        menu_text = ""
        
        for item in menu_items:
            name = item.get("name", "Unknown item")
            variations = item.get("variations", [])
            
            menu_text += f"{name} options:\n"
            for variation in variations:
                var_name = variation.get("name", "")
                var_price = variation.get("price", 0)
                menu_text += f"  - {name} - Variation: {var_name} - Price: ${var_price}\n"
        
        return menu_text
    except Exception as e:
        logger.error(f"Error formatting menu for SMS: {e}")
        return "Menu currently unavailable. Please ask our voice assistant for menu options."

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """
    Handle WebSocket connections between Twilio and Deepgram.
    
    Args:
        websocket (WebSocket): The FastAPI WebSocket connection
    """
    logger.info("Client connected")
    await websocket.accept()
    
    # Connection specific state
    stream_sid = None
    caller_phone = None
    call_sid = None  # Add call_sid to the outer scope
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()
    menu_sms_sent = False  # Flag to track if menu SMS was sent
    inbuffer = bytearray(b"")  # Initialize inbuffer in the outer scope
    
    try:
        # Connect to Deepgram Voice Agent API
        async with websockets.connect(
            'wss://agent.deepgram.com/agent', 
            subprotocols=["token", DEEPGRAM_API_KEY]
        ) as deepgram_ws:
            # Send initial configuration
            config_message = create_settings_configuration()
            await deepgram_ws.send(json.dumps(config_message))
            
            # Wait a moment for the connection to be fully established
            await asyncio.sleep(1.0)
            
            # Make the agent speak first with a greeting
            initial_greeting = {
                "type": "InjectAgentMessage",
                "message": "Hello there! Welcome to KK restaurant. I'm your AI voice assistant. How can I help you today?"
            }
            await deepgram_ws.send(json.dumps(initial_greeting))
            logger.info("Sent initial greeting to agent")
            
            async def receive_from_twilio():
                """Receive audio data from Twilio and send it to Deepgram."""
                nonlocal stream_sid, caller_phone, menu_sms_sent, call_sid, inbuffer
                BUFFER_SIZE = settings.AUDIO_BUFFER_BYTES  # Use buffer size from settings
                
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        if data["event"] == "start":
                            logger.info("Got stream SID from Twilio")
                            stream_sid = data["start"]["streamSid"]
                            await streamsid_queue.put(stream_sid)
                            
                            # Extract caller phone number - try all possible locations
                            caller_phone = None
                            # Remove call_sid declaration here since it's now in outer scope
                            
                            # Log all potential caller ID locations for debugging
                            if "start" in data:
                                # Log the raw message for complete analysis
                                logger.info(f"TWILIO RAW DATA: {json.dumps(data, indent=2)}")
                                
                                # Get the call SID if available
                                if "callSid" in data["start"]:
                                    call_sid = data["start"]["callSid"]
                                    logger.info(f"TWILIO CALL SID: {call_sid}")
                                
                                # Method 1: Check 'from' field in start object
                                if "from" in data["start"]:
                                    caller_phone = data["start"]["from"]
                                    logger.info(f"CALLER ID from 'from' field: {caller_phone}")
                                
                                # Method 2: Check parameters.From
                                if "parameters" in data["start"] and "From" in data["start"]["parameters"]:
                                    caller_phone_param = data["start"]["parameters"]["From"]
                                    logger.info(f"CALLER ID from parameters.From: {caller_phone_param}")
                                    if not caller_phone:  # Only set if not already found
                                        caller_phone = caller_phone_param
                                
                                # Method 3: Check customParameters.From
                                if "customParameters" in data["start"] and "From" in data["start"]["customParameters"]:
                                    caller_phone_custom = data["start"]["customParameters"]["From"]
                                    logger.info(f"CALLER ID from customParameters.From: {caller_phone_custom}")
                                    if not caller_phone:  # Only set if not already found
                                        caller_phone = caller_phone_custom
                                
                                # Method 4: Check for callerId field
                                if "callerId" in data["start"]:
                                    caller_id = data["start"]["callerId"]
                                    logger.info(f"CALLER ID from callerId field: {caller_id}")
                                    if not caller_phone:  # Only set if not already found
                                        caller_phone = caller_id
                                
                                # Method 5: Check for caller field
                                if "caller" in data["start"]:
                                    caller = data["start"]["caller"]
                                    logger.info(f"CALLER ID from caller field: {caller}")
                                    if not caller_phone:  # Only set if not already found
                                        caller_phone = caller
                                
                                # Method 6: If we have a call SID and no caller ID yet, fetch call details from API
                                if not caller_phone and call_sid:
                                    logger.info(f"Attempting to fetch caller ID from Twilio API using call SID: {call_sid}")
                                    call_details = await asyncio.to_thread(get_call_details, call_sid)
                                    
                                    if call_details.get("success", False):
                                        caller_phone = call_details.get("from_number")
                                        logger.info(f"CALLER ID from Twilio API: {caller_phone}")
                                        
                                        # Log additional call information
                                        logger.info(f"CALL DIRECTION: {call_details.get('direction')}")
                                        logger.info(f"CALLER NAME: {call_details.get('caller_name')}")
                                    else:
                                        logger.error(f"Failed to fetch call details: {call_details.get('error')}")
                                
                                # Final summary log
                                if caller_phone:
                                    logger.info(f"FINAL CALLER ID: {caller_phone}")
                                    
                                    # Save call start information to database
                                    if call_sid:
                                        logger.info(f"Saving call start to database for SID: {call_sid}")
                                        await save_call_start(call_sid, caller_phone)
                                else:
                                    logger.warning("NO CALLER ID FOUND IN ANY LOCATION")
                                    caller_phone = settings.FALLBACK_CALLER_ID  # Fallback from settings
                                    logger.info(f"Using fallback test number: {caller_phone}")
                                    
                                    # Save call start with fallback number
                                    if call_sid:
                                        logger.info(f"Saving call start to database with fallback number for SID: {call_sid}")
                                        await save_call_start(call_sid, caller_phone)
                            
                            # Send menu via SMS if we have a valid phone number
                            if caller_phone and not menu_sms_sent:
                                menu_text = format_menu_for_sms()
                                
                                # Log the attempt to send SMS
                                full_sms_text = f"Thank you for calling KK Restaurant! Here's our menu:\n\n{menu_text}"
                                logger.info(f"SMS SENDING: Attempting to send menu SMS to {caller_phone}")
                                logger.info(f"SMS CONTENT: {full_sms_text[:100]}... (truncated, total length: {len(full_sms_text)} chars)")
                                
                                sms_response = await asyncio.to_thread(
                                    send_sms, 
                                    caller_phone, 
                                    full_sms_text
                                )
                                
                                # Log the SMS sending result
                                if sms_response.get("success", False):
                                    menu_sms_sent = True
                                    logger.info(f"SMS SENT: Menu SMS successfully sent to {caller_phone}, message SID: {sms_response.get('message_sid')}")
                                else:
                                    logger.error(f"SMS ERROR: Failed to send menu SMS: {sms_response.get('error')}")
                                
                        elif data["event"] == "media" and data["media"]["track"] == "inbound":
                            media = data["media"]
                            chunk = base64.b64decode(media["payload"])
                            inbuffer.extend(chunk)
                            
                        elif data["event"] == "stop":
                            logger.info(f"Received 'stop' event for call SID: {call_sid}")
                            
                            # Upload the audio to S3
                            if len(inbuffer) > 0:
                                # First mark the call as ended in the database immediately (without audio URL)
                                call_end_result = await save_call_end(call_sid)
                                if call_end_result:
                                    logger.info(f"Call end recorded for SID: {call_sid}")
                                else:
                                    logger.error(f"Failed to record call end for SID: {call_sid}")
                                
                                # Start the S3 upload in the background as a non-blocking task
                                logger.info(f"Starting S3 upload of {len(inbuffer)} bytes of audio data in background")
                                upload_task = asyncio.create_task(upload_audio_to_s3(call_sid, bytes(inbuffer)))
                                
                                # Define an async function to handle the upload completion
                                async def process_upload_result():
                                    try:
                                        # Wait for the upload to complete (non-blocking since this is its own task)
                                        audio_url = await upload_task
                                        if audio_url:
                                            # Update the database with the audio URL
                                            update_result = await save_call_end(call_sid, audio_url=audio_url)
                                            if update_result:
                                                logger.info(f"Call record updated with audio URL: {audio_url}")
                                            else:
                                                logger.error(f"Failed to update call record with audio URL")
                                        else:
                                            logger.error("Failed to upload audio to S3, no URL returned")
                                    except Exception as e:
                                        logger.error(f"Error processing upload result: {str(e)}")
                                        import traceback
                                        logger.error(f"Traceback: {traceback.format_exc()}")
                                
                                # Schedule the async task to process the upload result - this won't block
                                asyncio.create_task(process_upload_result())
                                logger.info("Audio processing scheduled, continuing with call flow")
                            else:
                                logger.warning("No audio data available to upload to S3")
                                call_end_result = await save_call_end(call_sid)
                                if call_end_result:
                                    logger.info(f"Call end recorded for SID: {call_sid}")
                                else:
                                    logger.error(f"Failed to record call end for SID: {call_sid}")
                            
                            break
                            
                        # Check if our buffer is ready to send to Deepgram
                        while len(inbuffer) >= BUFFER_SIZE:
                            chunk = inbuffer[:BUFFER_SIZE]
                            await audio_queue.put(chunk)
                            inbuffer = inbuffer[BUFFER_SIZE:]
                            
                except WebSocketDisconnect:
                    logger.info("Twilio client disconnected.")
                except Exception as e:
                    logger.error(f"Error in receive_from_twilio: {e}")
            
            async def send_to_deepgram():
                """Send buffered audio data to Deepgram."""
                logger.info("Deepgram sender started")
                try:
                    while True:
                        chunk = await audio_queue.get()
                        await deepgram_ws.send(chunk)
                except Exception as e:
                    logger.error(f"Error sending to Deepgram: {e}")
            
            async def receive_from_deepgram():
                """Receive responses from Deepgram and handle function calls."""
                nonlocal call_sid, inbuffer
                logger.info("Deepgram receiver started")
                
                # Wait for stream_sid from Twilio
                stream_sid = await streamsid_queue.get()
                
                try:
                    async for message in deepgram_ws:
                        if isinstance(message, str):
                            try:
                                decoded = json.loads(message)
                                message_type = decoded.get('type', 'unknown')
                                logger.info(f"Received message from Deepgram, type: {message_type}")
                                
                                # Log detailed message contents for debugging
                                if message_type not in ['KeepAlive', 'Heartbeat']:  # Skip logging for frequent messages
                                    # Log only a subset of the message to avoid log bloat
                                    log_message = {k: v for k, v in decoded.items() if k != 'binary'}
                                    if 'transcript' in log_message:
                                        log_message['transcript'] = log_message['transcript'][:50] + '...' if len(log_message['transcript']) > 50 else log_message['transcript']
                                    logger.info(f"Deepgram message details: {json.dumps(log_message)}")
                                
                                # Record transcriptions for storage
                                if message_type == 'ConversationText' and call_sid:
                                    role = decoded.get('role', '')
                                    content = decoded.get('content', '')
                                    
                                    if content and content.strip():
                                        if role == 'user':
                                            logger.info(f"CUSTOMER: {content}")
                                            # Save customer utterance to database
                                            save_result = await save_utterance(call_sid, "customer", content, 1.0)
                                            logger.info(f"Save utterance result for customer: {save_result}")
                                        elif role == 'assistant':
                                            logger.info(f"AI ASSISTANT: {content}")
                                            # Save system utterance to database
                                            save_result = await save_utterance(call_sid, "system", content, 1.0)
                                            logger.info(f"Save utterance result for system: {save_result}")
                                
                                # Handle barge-in (user interrupting)
                                if decoded.get('type') == 'UserStartedSpeaking':
                                    clear_message = {
                                        "event": "clear",
                                        "streamSid": stream_sid
                                    }
                                    await websocket.send_json(clear_message)
                                
                                # Handle function calling
                                if decoded.get('type') == 'FunctionCallRequest':
                                    await handle_function_call(decoded, deepgram_ws, websocket, stream_sid, caller_phone, call_sid, inbuffer)
                            except json.JSONDecodeError:
                                logger.warning(f"Received non-JSON message: {message[:100]}...")
                            continue
                        
                        # Handle binary audio response from Deepgram
                        raw_mulaw = message
                        
                        # Construct a Twilio media message with the raw mulaw
                        media_message = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
                        }
                        
                        # Send the TTS audio to the attached phone call
                        await websocket.send_json(media_message)
                except Exception as e:
                    logger.error(f"Error in receive_from_deepgram: {e}")
            
            # Start all tasks concurrently
            await asyncio.gather(
                receive_from_twilio(),
                send_to_deepgram(),
                receive_from_deepgram()
            )
    except Exception as e:
        logger.error(f"Error in handle_media_stream: {e}")
        await websocket.close(code=1011, reason=f"Error: {str(e)}")

async def handle_function_call(decoded, deepgram_ws, websocket, stream_sid, caller_phone, call_sid=None, inbuffer=None):
    """
    Handle function calls from the Deepgram API.
    
    Args:
        decoded (dict): The decoded function call request
        deepgram_ws (WebSocketClientProtocol): The Deepgram WebSocket connection
        websocket (WebSocket): The Twilio WebSocket connection
        stream_sid (str): The Twilio stream SID
        caller_phone (str): The caller's phone number
        call_sid (str, optional): The Twilio call SID
        inbuffer (bytearray, optional): The audio buffer containing recorded audio
    """
    function_name = decoded.get('function_name')
    function_call_id = decoded.get('function_call_id')
    input_data = decoded.get('input', {})
    
    logger.info(f"Function call request: {function_name}")
    
    # Process order_summary function
    if function_name == 'order_summary':
        order_items = input_data.get('items', [])
        total_price = input_data.get('total_price', 0)
        summary_status = input_data.get('summary', 'IN PROGRESS')
        
        # Calculate tax if needed
        tax_rate = settings.RESTAURANT_TAX_RATE
        tax_amount = total_price * tax_rate if tax_rate > 0 else 0
        total_with_tax = total_price + tax_amount
        
        # Create order details
        order_details = {
            "items": order_items,
            "total_price": total_price,
            "tax_amount": tax_amount,
            "total_with_tax": total_with_tax,
            "status": summary_status,
            "order_id": f"ORDER-{int(time.time())}"
        }
        
        # Process order with Square if summary status is DONE
        if summary_status == "DONE":
            try:
                # Test payment method ID for Square sandbox from settings
                test_payment_method_id = settings.SQUARE_TEST_NONCE
                
                logger.info(f"Creating order in Square with items: {order_items}")
                # Place order via Square
                result = await test_create_order_endpoint(order_items)
                current_order_id = result["order"]["id"]
                current_order_total = result["order"]["total_money"].get("amount")
                
                logger.info(f"Order created successfully! Order ID: {current_order_id}, Total: {current_order_total}")
                
                if current_order_id:
                    # Process payment via Square
                    logger.info(f"Processing payment for order {current_order_id}, amount: {current_order_total}")
                    payment_result = await test_payment_processing(
                        current_order_id, 
                        current_order_total,
                        test_payment_method_id
                    )
                    
                    if payment_result:
                        logger.info(f"Payment successful! Payment ID: {payment_result.get('id')}")
                        order_details["payment_status"] = "PAID"
                        order_details["payment_id"] = payment_result.get("id")
                        
                        # Send order confirmation via SMS
                        if caller_phone:
                            order_summary = "Your order has been confirmed:\n"
                            for item in order_items:
                                name = item.get("name", "Unknown")
                                quantity = item.get("quantity", 1)
                                variation = item.get("variation", "")
                                order_summary += f"- {quantity}x {name} ({variation})\n"
                            
                            order_summary += f"\nTotal: ${total_price:.2f}\n"
                            order_summary += f"Tax: ${tax_amount:.2f}\n"
                            order_summary += f"Total with tax: ${total_with_tax:.2f}\n"
                            order_summary += f"\nOrder ID: {order_details['order_id']}\n"
                            order_summary += "\nThank you for ordering from KK Restaurant!"
                            
                            # Log the attempt to send order confirmation SMS
                            logger.info(f"SMS SENDING: Attempting to send order confirmation to {caller_phone}")
                            logger.info(f"SMS CONTENT: {order_summary[:100]}... (truncated, total length: {len(order_summary)} chars)")
                            
                            # Send order confirmation SMS asynchronously
                            async def send_order_confirmation():
                                try:
                                    sms_result = await asyncio.to_thread(
                                        send_sms, 
                                        caller_phone, 
                                        order_summary
                                    )
                                    if sms_result.get("success", False):
                                        logger.info(f"SMS SENT: Order confirmation SMS successfully sent to {caller_phone}, message SID: {sms_result.get('message_sid')}")
                                    else:
                                        logger.error(f"SMS ERROR: Failed to send order confirmation SMS: {sms_result.get('error')}")
                                except Exception as e:
                                    logger.error(f"SMS EXCEPTION: Error sending order confirmation SMS: {e}")
                            
                            asyncio.create_task(send_order_confirmation())
                    else:
                        logger.error("Payment failed!")
                        order_details["payment_status"] = "FAILED"
                        order_details["payment_id"] = None
            except Exception as e:
                logger.error(f"Error processing order: {e}")
                order_details["payment_status"] = "ERROR"
                order_details["error"] = str(e)
        
        # Send function call response back to Deepgram
        function_response = {
            "type": "FunctionCallResponse",
            "function_call_id": function_call_id,
            "output": json.dumps(order_details)
        }
        await deepgram_ws.send(json.dumps(function_response))
        
        # If order summary status is DONE, schedule a hangup after final response
        if summary_status == "DONE":
            # Create a task to hang up after a delay
            asyncio.create_task(schedule_hangup(deepgram_ws, websocket, stream_sid, call_sid, inbuffer))

async def schedule_hangup(deepgram_ws, websocket, stream_sid, call_sid, inbuffer):
    """
    Schedule call hangup with a farewell message.
    
    Args:
        deepgram_ws (WebSocketClientProtocol): The Deepgram WebSocket connection
        websocket (WebSocket): The Twilio WebSocket connection
        stream_sid (str): The Twilio stream SID
        call_sid (str): The Twilio call SID
        inbuffer (bytearray): The audio buffer containing recorded audio
    """
    try:
        # Wait for the agent to speak the final confirmation
        await asyncio.sleep(5)
        
        # Send a thank you message before hanging up
        hangup_greeting = {
            "type": "InjectAgentMessage",
            "message": "Thank you for your order. We will send you the order summary via SMS shortly. Goodbye!"
        }
        await deepgram_ws.send(json.dumps(hangup_greeting))
        
        # Wait for the thank you message to finish - give it more time (10 seconds)
        logger.info("Waiting for farewell message to complete...")
        await asyncio.sleep(10)
        
        # Send a more forceful disconnect - first clean up
        logger.info("Hanging up the call")
        
        # Now we can handle the S3 upload here
        if call_sid and inbuffer is not None and len(inbuffer) > 0:
            logger.info(f"Initiating audio upload during hangup for call SID: {call_sid}, {len(inbuffer)} bytes")
            
            # First mark the call as ended
            call_end_result = await save_call_end(call_sid)
            if call_end_result:
                logger.info(f"Call end recorded during hangup for SID: {call_sid}")
            else:
                logger.error(f"Failed to record call end during hangup for SID: {call_sid}")
            
            # Start S3 upload and handle it properly
            try:
                logger.info(f"Starting S3 upload during hangup")
                audio_url = await upload_audio_to_s3(call_sid, bytes(inbuffer))
                
                if audio_url:
                    # Update the database with the audio URL
                    update_result = await save_call_end(call_sid, audio_url=audio_url)
                    if update_result:
                        logger.info(f"Call record updated with audio URL during hangup: {audio_url}")
                    else:
                        logger.error(f"Failed to update call record with audio URL during hangup")
                else:
                    logger.error("Failed to upload audio to S3 during hangup")
            except Exception as e:
                logger.error(f"Error during hangup S3 upload: {str(e)}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
        
        # First send stop event to shut down the media stream
        stop_message = {
            "event": "stop",
            "streamSid": stream_sid
        }
        await websocket.send_json(stop_message)
        
        # Then close both websocket connections
        await asyncio.sleep(1)
        await websocket.close(code=1000, reason="Call completed")
        
        # Log that we've initiated closure
        logger.info("WebSocket connections closed, call should now terminate")
    except Exception as e:
        logger.error(f"Error during call hangup: {e}")
        import traceback
        logger.error(f"Hangup traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting Deepgram Voice Agent API server on {settings.HOST}:{settings.PORT}")
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)