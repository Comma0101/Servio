import os
import json
import base64
import asyncio
import websockets
import logging
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Menu data - directly integrated without external services
MENU_DATA = {
  "menu": [
    {
      "category": "RICE NOODLE SOUP",
      "items": [
        {
          "id": "E1",
          "name": "Pork & Shrimp Rice Noodle Soup",
          "price": 15.00
        },
        {
          "id": "E2",
          "name": "Spicy Premium Beef Rice Noodle Soup",
          "price": 15.00
        },
        {
          "id": "E4",
          "name": "Spicy Beef Tendon Noodle Soup",
          "price": 16.00
        },
        {
          "id": "E7",
          "name": "Spicy Fish Rice Noodle Soup",
          "price": 15.00
        },
        {
            "id": "E9",
            "name": "Tomato Beef Rice Noodle Soup",
            "price": 15.00
        },
        {
          "id": "E10",
          "name": "Mate Spicy Beef Rice Noodle Soup",
          "price": 14.50
        },
        {
          "id": "E11",
          "name": "Mushroom Rice Noodle Soup",
          "price": 14.50
        },
        {
          "id": "E12",
          "name": "Mushroom Rice Noodle Soup w/ Pickled Vegetable",
          "price": 15.00
        },
        {
          "id": "E13",
          "name": "Devil's Spicy Beef Rice Noodle Soup",
          "price": 16.00
        },
        {
          "id": "E14",
          "name": "Braised Beef or Beef Tendon (+$1.00) Rice Noodle Soup",
          "price": 15.00
        },
        {
          "id": "E15",
          "name": "Tomato w/ Premium Beef Rice Noodle Soup",
          "price": 15.00
        },
        {
          "id": "E16",
          "name": "Seafood Rice Noodle Soup",
          "price": 15.00
        }
      ]
    },
    {
      "category": "JAPANESE RAMEN",
      "items": [
        {
          "id": "F1",
          "name": "Pork Ramen",
          "price": 15.50
        },
        {
          "id": "F2",
          "name": "Spicy Beef Ramen",
          "price": 16.00
        },
        {
          "id": "F3",
          "name": "Tomato with Premium Beef Ramen",
          "price": 16.00
        },
        {
          "id": "F4",
          "name": "Spicy Pickled Vegetable Fish or Premium Beef Ramen",
          "price": 16.00
        },
        {
          "id": "F5",
          "name": "Fish w/ Pickled Vegetable or Premium Beef Ramen",
          "price": 16.00
        },
        {
          "id": "F6",
          "name": "Tomato with Beef Ramen",
          "price": 16.00
        },
        {
          "id": "F7",
          "name": "Spicy Fish Ramen",
          "price": 16.00
        },
        {
          "id": "F8",
          "name": "Mushroom Ramen",
          "price": 15.00
        },
        {
          "id": "F9",
          "name": "House Special Ramen",
          "price": 16.00
        },
        {
          "id": "F10",
          "name": "Beef Ramen",
          "price": 16.00
        },
        {
          "id": "F11",
          "name": "Spicy Premium Beef Ramen",
          "price": 16.00
        },
        {
          "id": "F12",
          "name": "Seafood Ramen",
          "price": 16.00
        }
      ]
    },
    {
      "category": "HOUSE ENTREES",
      "items": [
        {
          "id": "G1",
          "name": "Fujian Style Fried Vermicelli",
          "price": 15.50
        },
        {
          "id": "G2",
          "name": "Taiwanese Style Fried Vermicelli",
          "price": 15.50
        },
        {
          "id": "G3",
          "name": "Black Pepper Shrimp Fried Udon",
          "price": 16.50
        },
        {
          "id": "G4",
          "name": "Black Pepper Beef Fried Udon",
          "price": 16.50
        },
        {
          "id": "G5",
          "name": "Spicy Cumin Fried Udon (Shrimp or Beef)",
          "price": 16.50
        },
        {
          "id": "G6",
          "name": "Duck & Female Ginseng Noodle Soup",
          "price": 16.50
        },
        {
          "id": "G7",
          "name": "Hunan Style Braised Noodle",
          "price": 16.50
        },
        {
          "id": "G8",
          "name": "Shrimp Fried Rice",
          "price": 15.50
        },
        {
          "id": "G9",
          "name": "Beef Fried Rice",
          "price": 15.50
        },
        {
          "id": "G10",
          "name": "House Special Fried Rice",
          "price": 15.50
        },
        {
          "id": "G11",
          "name": "Garlic Bok Choy",
          "price": 13.00
        }
      ]
    },
    {
      "category": "HOUSE BEVERAGES",
      "items": [
        {
          "id": "B1",
          "name": "Iced Milk Tea",
          "price": 4.50
        },
        {
          "id": "B2",
          "name": "Rainbow Jelly Milk Tea",
          "price": 4.99
        },
        {
          "id": "B3",
          "name": "Green Tea",
          "price": 4.50
        },
        {
          "id": "B4",
          "name": "Honey Green Tea",
          "price": 4.99
        },
        {
          "id": "B5",
          "name": "Lemon Green Tea",
          "price": 4.99
        },
        {
          "id": "B6",
          "name": "Honey Lemon Green Tea",
          "price": 4.99
        },
        {
          "id": "B7",
          "name": "Mango Green Tea",
          "price": 4.99
        },
        {
          "id": "B8",
          "name": "Strawberry Green Tea",
          "price": 4.99
        },
        {
          "id": "B9",
          "name": "Passion Green Tea",
          "price": 4.99
        },
        {
          "id": "B10",
          "name": "Green Apple Green Tea",
          "price": 4.99
        },
        {
          "id": "B11",
          "name": "Coke",
          "price": 2.50
        },
        {
          "id": "B12",
          "name": "Hot Tea",
          "price": 1.99
        }
      ]
    }
  ]
}

# Define system message for the Deepgram agent
SYSTEM_MESSAGE = """
You are a restaurant voice assistant for a restaurant called KK Restaurant. Your job is to help customers with their orders.
Be friendly, helpful, and concise. If customers have questions about the menu, assist them in finding what they want.
If a customer wants to place an order, use the order_summary function to record their order details.

Here's how the conversation should flow:
1. Greet the customer and ask how you can help them
2. If they ask about the menu, describe the categories available
3. If they ask about items in a specific category, describe those items
4. If they want to order, help them through the process and confirm their selections
5. Use the order_summary function to create an order summary when they're done ordering

Always provide prices when discussing menu items. Be helpful but professional.
"""

# Deepgram API key
DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
PORT = int(os.getenv('PORT', 5050))

app = FastAPI()

@app.get("/", response_class=JSONResponse)
async def index_page():
    """Root endpoint that returns a simple status message."""
    return {"message": "Simple Deepgram Voice Agent is running!"}

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
    response.say("Please wait while we connect your call to the One Dragon restaurant assistant", voice="Polly.Joanna-Neural")
    response.pause(length=1)
    
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

def create_settings_configuration():
    """Create settings for Deepgram Voice Agent API with function calling."""
    # Build menu text for the agent
    menu_text = "\n\nMENU ITEMS:\n"
    for category in MENU_DATA["menu"]:
        menu_text += f"\n{category['category']}:\n"
        for item in category["items"]:
            menu_text += f"{item['id']} - {item['name']}: ${item['price']:.2f}\n"
    
    enhanced_system_message = SYSTEM_MESSAGE + menu_text
    
    # Define the function for the agent
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
    
    logger.info("Using order_summary function definition")
    
    # Return the configuration in the same format as the working deepgram.py
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
                    "type": "anthropic",  # Using the same provider as the working version
                },
                "model": "claude-3-haiku-20240307",
                "instructions": enhanced_system_message,
                "functions": [function_def]
            },
            "speak": {"model": "aura-asteria-en"},
        },
    }

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
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()
    inbuffer = bytearray(b"")
    
    try:
        # Connect to Deepgram Voice Agent API
        logger.info("Attempting to connect to Deepgram Voice Agent API...")
        try:
            async with websockets.connect(
                'wss://agent.deepgram.com/agent', 
                subprotocols=["token", DEEPGRAM_API_KEY],
                ping_interval=20,  # Send ping every 20 seconds
                ping_timeout=10,   # Wait 10 seconds for pong response
                close_timeout=5,   # Wait 5 seconds for close handshake
            ) as deepgram_ws:
                logger.info("WebSocket connection to Deepgram established successfully")
                
                # Send initial configuration
                config_message = create_settings_configuration()
                logger.info(f"Sending configuration to Deepgram: {json.dumps(config_message)[:200]}...")
                try:
                    await deepgram_ws.send(json.dumps(config_message))
                    logger.info("Configuration sent to Deepgram successfully")
                except Exception as e:
                    logger.error(f"Failed to send configuration to Deepgram: {str(e)}")
                    raise
                
                # Send an initial empty audio chunk to prevent timeout
                try:
                    silence_packet_size = 160
                    logger.info(f"Preparing silence packet of size {silence_packet_size} bytes")
                    initial_silence = bytes([0] * silence_packet_size)  # One packet of silence in mulaw
                    logger.info(f"Sending silence packet of size {len(initial_silence)} bytes to Deepgram")
                    await deepgram_ws.send(initial_silence)
                    logger.info("Silence packet sent successfully")
                except Exception as e:
                    logger.error(f"Failed to send silence packet: {str(e)}")
                    raise

                # Wait a moment for the connection to be fully established
                logger.info("Waiting for 1 second to allow connection to establish fully...")
                await asyncio.sleep(1.0)
                logger.info("Wait completed")
                
                # Define initial greeting - we'll send it after receiving confirmation from Deepgram
                initial_greeting = {
                    "type": "InjectAgentMessage",
                    "message": "Hello there! Welcome to One Dragon restaurant. I'm your AI voice assistant. How can I help you today?"
                }
                logger.info("Initial greeting prepared, will send after Deepgram confirmation")
                
                # Setup flags to track the Deepgram connection state
                received_welcome = False
                received_settings_applied = False
                greeting_sent = False
                
                async def receive_from_twilio():
                    """Receive audio data from Twilio and send it to Deepgram."""
                    nonlocal stream_sid, inbuffer
                    BUFFER_SIZE = 5 * 160  # Buffer 5 Twilio messages (0.1 seconds of audio)
                    logger.info(f"Starting Twilio receiver with buffer size {BUFFER_SIZE} bytes")
                    
                    try:
                        message_count = 0
                        async for message in websocket.iter_text():
                            message_count += 1
                            if message_count % 100 == 0:
                                logger.debug(f"Processed {message_count} messages from Twilio")
                                
                            data = json.loads(message)
                            if data["event"] == "start":
                                logger.info("Got stream SID from Twilio")
                                stream_sid = data["start"]["streamSid"]
                                await streamsid_queue.put(stream_sid)
                                
                                # Basic logging
                                if "start" in data:
                                    logger.info(f"TWILIO RAW DATA: {json.dumps(data, indent=2)}")
                                    if "callSid" in data["start"]:
                                        call_sid = data["start"]["callSid"]
                                        logger.info(f"TWILIO CALL SID: {call_sid}")
                                
                            elif data["event"] == "media" and data["media"]["track"] == "inbound":
                                media = data["media"]
                                chunk = base64.b64decode(media["payload"])
                                inbuffer.extend(chunk)
                                
                                # Send small chunks immediately if queue is empty
                                if len(inbuffer) > 0 and audio_queue.empty():
                                    logger.debug(f"Sending immediate audio chunk of size {len(inbuffer)} bytes")
                                    await audio_queue.put(bytes(inbuffer))
                                    inbuffer = bytearray(b"")

                            elif data["event"] == "stop":
                                logger.info("Received stop event from Twilio")
                                break
                                
                            # Check if our buffer is ready to send to Deepgram
                            while len(inbuffer) >= BUFFER_SIZE:
                                chunk = inbuffer[:BUFFER_SIZE]
                                logger.debug(f"Queuing audio chunk of size {len(chunk)} bytes")
                                await audio_queue.put(chunk)
                                inbuffer = inbuffer[BUFFER_SIZE:]
                                
                    except WebSocketDisconnect:
                        logger.warning("Twilio client disconnected.")
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error from Twilio: {str(e)}")
                    except Exception as e:
                        logger.error(f"Error in receive_from_twilio: {str(e)}", exc_info=True)
                
                async def send_to_deepgram():
                    """Send buffered audio data to Deepgram."""
                    logger.info("Deepgram sender started")
                    try:
                        send_count = 0
                        while True:
                            chunk = await audio_queue.get()
                            send_count += 1
                            if send_count % 100 == 0:
                                logger.debug(f"Sent {send_count} audio chunks to Deepgram")
                            
                            try:
                                await deepgram_ws.send(chunk)
                                audio_queue.task_done()
                            except websockets.exceptions.ConnectionClosed as e:
                                logger.error(f"Deepgram WebSocket connection closed while sending: code={e.code}, reason={e.reason}")
                                raise
                            except Exception as e:
                                logger.error(f"Error sending chunk to Deepgram: {str(e)}")
                                audio_queue.task_done()
                                raise
                    except asyncio.CancelledError:
                        logger.info("Deepgram sender task cancelled")
                    except Exception as e:
                        logger.error(f"Error in send_to_deepgram: {str(e)}", exc_info=True)
                
                async def receive_from_deepgram():
                    """Receive responses from Deepgram and handle function calls."""
                    logger.info("Deepgram receiver started")
                    nonlocal received_welcome, received_settings_applied, greeting_sent, initial_greeting
                    
                    # Wait for stream_sid from Twilio
                    logger.info("Waiting for stream_sid from Twilio...")
                    stream_sid = await streamsid_queue.get()
                    logger.info(f"Got stream_sid: {stream_sid}")
                    
                    try:
                        async for message in deepgram_ws:
                            if isinstance(message, str):
                                try:
                                    decoded = json.loads(message)
                                    message_type = decoded.get('type', 'unknown')
                                    logger.info(f"Received message from Deepgram, type: {message_type}")
                                    
                                    # Check for confirmation messages from Deepgram
                                    if message_type == "Welcome":
                                        received_welcome = True
                                        logger.info("Received Welcome message from Deepgram")
                                    elif message_type == "SettingsApplied":
                                        received_settings_applied = True
                                        logger.info("Received SettingsApplied message from Deepgram")
                                    
                                    # Send the greeting after both confirmations are received
                                    if received_welcome and received_settings_applied and not greeting_sent:
                                        greeting_sent = True
                                        logger.info(f"Deepgram connection confirmed, sending initial greeting: {json.dumps(initial_greeting)}")
                                        try:
                                            await deepgram_ws.send(json.dumps(initial_greeting))
                                            logger.info("Initial greeting sent successfully")
                                        except Exception as e:
                                            logger.error(f"Failed to send initial greeting: {str(e)}")
                                    
                                    # Log detailed message contents for debugging
                                    if message_type not in ['KeepAlive', 'Heartbeat']:  # Skip logging for frequent messages
                                        # Log only a subset of the message to avoid log bloat
                                        log_message = {k: v for k, v in decoded.items() if k != 'binary'}
                                        if 'transcript' in log_message:
                                            log_message['transcript'] = log_message['transcript'][:50] + '...' if len(log_message['transcript']) > 50 else log_message['transcript']
                                        logger.info(f"Deepgram message details: {json.dumps(log_message)}")
                                    
                                    # Record transcriptions for display
                                    if message_type == 'ConversationText':
                                        role = decoded.get('role', '')
                                        content = decoded.get('content', '')
                                        
                                        if content and content.strip():
                                            if role == 'user':
                                                logger.info(f"CUSTOMER: {content}")
                                            elif role == 'assistant':
                                                logger.info(f"AI ASSISTANT: {content}")
                                    
                                    # Handle barge-in (user interrupting)
                                    if decoded.get('type') == 'UserStartedSpeaking':
                                        clear_message = {
                                            "event": "clear",
                                            "streamSid": stream_sid
                                        }
                                        await websocket.send_json(clear_message)
                                    
                                    # Handle function calling
                                    if decoded.get('type') == 'FunctionCallRequest':
                                        # Log the order summary
                                        logger.info(f"ORDER SUMMARY REQUEST: {decoded}")
                                        
                                        # Extract the function call data 
                                        function_name = decoded.get('function_name')
                                        function_call_id = decoded.get('function_call_id')
                                        input_data = decoded.get('input', {})
                                        
                                        if function_name == 'order_summary':
                                            # Log the order details
                                            logger.info(f"ORDER RECEIVED: {json.dumps(input_data, indent=2)}")
                                            
                                            # Create a human-readable summary for the agent to speak
                                            items = input_data.get('items', [])
                                            summary_text = "Here's your order summary: "
                                            
                                            for i, item in enumerate(items):
                                                name = item.get('name', '')
                                                quantity = item.get('quantity', 1)
                                                variation = item.get('variation', '')
                                                
                                                summary_text += f"{quantity} {name}"
                                                if variation:
                                                    summary_text += f" with {variation}"
                                                if i < len(items) - 2:
                                                    summary_text += ", "
                                                elif i == len(items) - 2:
                                                    summary_text += " and "
                                            
                                            total_price = input_data.get('total_price', 0)
                                            summary_text += f". Your total comes to ${total_price:.2f}. The order will be ready in 15-20 minutes."
                                            
                                            # Create response to confirm order with embedded summary
                                            response_data = {
                                                "success": True,
                                                "message": summary_text,
                                                "data": {
                                                    "order_id": "TEST123",
                                                    "estimated_ready_time": "15-20 minutes",
                                                    "items": items,
                                                    "total_price": total_price
                                                }
                                            }
                                            
                                            order_response = {
                                                "type": "FunctionCallResponse",
                                                "function_call_id": function_call_id,
                                                "output": json.dumps(response_data)
                                            }
                                            
                                            # Send the response back to Deepgram
                                            await deepgram_ws.send(json.dumps(order_response))
                                            logger.info(f"Sent order summary to Deepgram: {summary_text}")
                                            
                                            # Schedule a delayed hangup to end the call after order is confirmed
                                            asyncio.create_task(schedule_hangup(deepgram_ws, websocket, stream_sid))
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
                        logger.error(f"Error in receive_from_deepgram: {str(e)}", exc_info=True)
                
                # Start all tasks concurrently
                logger.info("Starting all async tasks for WebSocket communication")
                await asyncio.gather(
                    receive_from_twilio(),
                    send_to_deepgram(),
                    receive_from_deepgram()
                )
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"Failed to connect to Deepgram: Invalid status code {e.status_code}")
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"Deepgram WebSocket connection closed unexpectedly: code={e.code}, reason={e.reason}")
        except Exception as e:
            logger.error(f"Failed to connect to Deepgram: {str(e)}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in handle_media_stream: {str(e)}", exc_info=True)
        await websocket.close(code=1011, reason=f"Error: {str(e)}")

async def schedule_hangup(deepgram_ws, websocket, stream_sid):
    """
    Schedule call hangup with a farewell message.
    
    Args:
        deepgram_ws: The Deepgram WebSocket connection
        websocket: The Twilio WebSocket connection
        stream_sid: The Twilio stream SID
    """
    try:
        # Wait a few seconds after order confirmation
        await asyncio.sleep(5)
        
        # Send a final message from the agent
        final_message = {
            "type": "InjectAgentMessage",
            "message": "Thank you for your order! Your food will be ready in about 15-20 minutes. We appreciate your business and hope to serve you again soon. Goodbye!"
        }
        await deepgram_ws.send(json.dumps(final_message))
        logger.info("Sent farewell message to agent")
        
        # Wait for the agent to finish speaking (rough estimate)
        await asyncio.sleep(8)
        
        # Send hangup command to Twilio
        hangup_message = {
            "event": "hangup",
            "streamSid": stream_sid
        }
        await websocket.send_json(hangup_message)
        logger.info("Sent hangup command to Twilio")
    except Exception as e:
        logger.error(f"Error in schedule_hangup: {str(e)}", exc_info=True)

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting Simple Deepgram Voice Agent server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)