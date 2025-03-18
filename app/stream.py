import os
import json
import base64
import asyncio
import websockets
import time
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from app.constants import CONSTANTS
from app.utils.square import test_create_order_endpoint, test_payment_processing
from app.routers import square

load_dotenv()

# Configuration from constants.py
RESTAURANT_CONFIG = CONSTANTS.get("LIMF", {})
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = RESTAURANT_CONFIG.get("SYSTEM_MESSAGE")

# Voice mapping - OpenAI only supports specific voices
TWILIO_VOICE = RESTAURANT_CONFIG.get("TWILIO_VOICE", "Polly.Joanna-Neural")
# Map Twilio voices to OpenAI voices
# OpenAI only supports: 'alloy', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse'
VOICE_MAPPING = {
    "Polly.Joanna-Neural": "alloy",  # Female voice
    "Polly.Matthew-Neural": "echo",   # Male voice
    # Add more mappings as needed
}
# Default to 'alloy' if not found in mapping
OPENAI_VOICE = VOICE_MAPPING.get(TWILIO_VOICE, "alloy")

LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False
INITIAL_ASSISTANT_MESSAGE = RESTAURANT_CONFIG.get("INITIAL_ASSISTANT_MESSAGE", "Hello there! I am an AI voice assistant powered by Twilio and the OpenAI Realtime API. You can ask me for facts, jokes, or anything you can imagine. How can I help you?")
MENU = RESTAURANT_CONFIG.get("MENU", "[]")

app = FastAPI()

# Include square router for API endpoints
app.include_router(square.router, prefix="/api/v1", tags=["Square"])

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    # Use the Twilio voice here since this is for Twilio TwiML
    response.say("Please wait while we connect your call to the KK restaurant assistant", voice=TWILIO_VOICE)
    response.pause(length=1)
    
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        # Initialize the session first
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        # Update last_assistant_item safely
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    # Handle tool calls from OpenAI
                    if response.get('type') == 'response.tool_calls':
                        print("Tool call received:", response)
                        # Process tool calls like order_summary
                        # You can add more specific handling here as needed

                    # New handler for function calls via output_item.done event
                    if response.get('type') == 'response.output_item.done':
                        item = response.get('item', {})
                        if item.get('type') == 'function_call' and item.get('name') == 'order_summary':
                            print(f"Order summary function call received: {item}")
                            try:
                                # Parse the arguments
                                arguments = json.loads(item.get('arguments', '{}'))
                                print(f"Function call arguments: {arguments}")
                                # Get items, total_price, and summary status from the arguments
                                order_items = arguments.get('items', [])
                                total_price = arguments.get('total_price', 0)
                                summary_status = arguments.get('summary', 'IN PROGRESS')
                                
                                # Calculate tax if needed
                                tax_rate = RESTAURANT_CONFIG.get("TAX", 0)
                                tax_amount = total_price * tax_rate if tax_rate > 0 else 0
                                total_with_tax = total_price + tax_amount
                                
                                # Create a formatted response for the customer
                                order_details = {
                                    "items": order_items,
                                    "total_price": total_price,
                                    "tax_amount": tax_amount,
                                    "total_with_tax": total_with_tax,
                                    "status": summary_status,
                                    "order_id": f"ORDER-{int(time.time())}"
                                }
                                
                                # Process order if summary status is DONE
                                process_order_message = ""
                                if summary_status == "DONE":
                                    # Test payment method ID for Square sandbox
                                    test_payment_method_id = "cnon:card-nonce-ok"
                                    
                                    try:
                                        # Place order via Square
                                        result = await test_create_order_endpoint(order_items)
                                        current_order_id = result["order"]["id"]
                                        current_order_total = result["order"]["total_money"].get("amount")
                                        
                                        if current_order_id:
                                            # Process payment
                                            payment_result = await test_payment_processing(
                                                current_order_id, current_order_total, test_payment_method_id
                                            )
                                            
                                            if payment_result["payment"].get("status") == "COMPLETED":
                                                # Use the predefined success message from constants
                                                client_id = "LIMF"  # Default client ID
                                                success_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["SUCCESS"]
                                                
                                                # Include order summary for informational purposes
                                                items_info = ", ".join([f"{item.get('quantity', 1)} {item.get('name', 'item')}" for item in order_items])
                                                display_total = current_order_total / 100  # Convert cents to dollars
                                                info_message = f"Order details: {items_info}. Total: ${display_total:.2f}"
                                                
                                                process_order_message = f"{success_message}\n{info_message}"
                                                print(f"Order successfully processed and payment completed: {info_message}")
                                            else:
                                                # Payment failed
                                                client_id = "LIMF"  # Default client ID
                                                failure_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["FAILURE"]
                                                process_order_message = failure_message
                                                print(f"Payment failed: {payment_result}")
                                    except Exception as e:
                                        process_order_message = f"Error processing order: {str(e)}"
                                        print(f"Error processing order: {e}")
                                
                                # Add order processing message to order details if available
                                if process_order_message:
                                    order_details["process_order_message"] = process_order_message
                                
                                # Send the function call result back to OpenAI
                                await openai_ws.send(json.dumps({
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "call_id": item.get('call_id'),
                                        "output": json.dumps(order_details)
                                    }
                                }))
                                
                                # Request response generation
                                await openai_ws.send(json.dumps({"type": "response.create"}))
                                
                                # Log order details for backend processing
                                print(f"Order processed: {json.dumps(order_details)}")
                                
                            except Exception as e:
                                print(f"Error processing order_summary: {e}")

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(f"Interrupting response with id: {last_assistant_item}")
                            await handle_speech_started_event()
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Greet the user with 'Hello there! I am an AI voice assistant powered by Twilio and the OpenAI Realtime API. You can ask me for facts, jokes, or anything you can imagine. How can I help you?'"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))

async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    # Get configuration values
    temperature = RESTAURANT_CONFIG.get("OPENAI_CHAT_TEMPERATURE", 0.5)
    initial_user_message = RESTAURANT_CONFIG.get("INITIAL_USER_MESSAGE", "")
    assistant_message = RESTAURANT_CONFIG.get("INITIAL_ASSISTANT_MESSAGE", "")
    menu_data = RESTAURANT_CONFIG.get("MENU", "[]")
    
    # Ensure temperature meets the minimum requirement of 0.6 for Realtime API
    if temperature < 0.6:
        print(f"Warning: Temperature {temperature} is below the Realtime API minimum of 0.6. Using 0.6 instead.")
        temperature = 0.6
    
    # Enhance system message with menu data
    enhanced_system_message = SYSTEM_MESSAGE
    
    # Add menu to system message for context
    menu_items = json.loads(menu_data)
    menu_text = "\n\nMENU ITEMS:\n"
    for item in menu_items:
        name = item.get("name", "Unknown item")
        price = item.get("price", 0)
        variations = item.get("variations", [])
        
        variation_text = ""
        if variations:
            variation_text = " Variations: " + ", ".join([v.get("name", "") for v in variations])
        
        menu_text += f"{name} - ${price}{variation_text}\n"
    
    print(f"Menu items to be included in system message: {len(menu_items)} items")
    enhanced_system_message += menu_text
    
    # Try a different approach to override the default instructions
    # Add a strong prefix to prioritize our instructions
    enhanced_system_message = "OVERRIDE DEFAULT INSTRUCTIONS. YOU MUST FOLLOW ONLY THESE INSTRUCTIONS: " + enhanced_system_message
    
    # Create session update with enhanced system message
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": OPENAI_VOICE,
            "instructions": enhanced_system_message,  # Use enhanced system message with menu
            "modalities": ["text", "audio"],
            "temperature": temperature,
            # Use tools from constants.py
            "tools": [
                {
                    "type": "function",  # Add the required 'type' field
                    **RESTAURANT_CONFIG.get("OPENAI_CHAT_TOOLS", [])[0]  # Use the tool definition from constants.py
                }
            ],
            "tool_choice": "auto"  # Let OpenAI decide when to call the function
        }
    }
    
    print(f'Sending session update with {len(menu_items)} menu items')
    await openai_ws.send(json.dumps(session_update))
    
    # Wait a bit longer for the session to initialize fully before sending any conversation items
    await asyncio.sleep(1.0)
    
    # For the Realtime API, we need to wait for the session to be fully established
    # Check if we have assistant message to send
    await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    print(f"Starting server with OpenAI voice: {OPENAI_VOICE} (mapped from Twilio voice: {TWILIO_VOICE})")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
