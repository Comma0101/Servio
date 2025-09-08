import asyncio
import json
import websockets
import logging
import base64

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def receiver(websocket, welcome_event):
    """Receives messages from the server and sets an event on welcome."""
    while True:
        try:
            message_str = await websocket.recv()
            message = json.loads(message_str)
            if message.get("event") == "media" and not welcome_event.is_set():
                logger.info("Welcome message received.")
                welcome_event.set()
            else:
                logger.info(f"Received: {message}")
        except websockets.exceptions.ConnectionClosed:
            logger.info("Receiver: Connection closed.")
            break
        except Exception as e:
            logger.error(f"Receiver error: {e}")
            break

async def sender(websocket, message_queue):
    """Sends messages from a queue, or silence if the queue is empty."""
    silence_payload = base64.b64encode(b'\xff' * 160).decode('ascii') # 20ms of mulaw silence
    while True:
        try:
            # Prioritize sending real messages
            try:
                message_text = message_queue.get_nowait()
                logger.info(f"Sending from queue: {message_text}")
                payload = base64.b64encode(message_text.encode('utf-8')).decode('ascii')
                message_queue.task_done()
            except asyncio.QueueEmpty:
                # Send silence if no message is in the queue
                payload = silence_payload

            media_message = {
                "event": "media",
                "streamSid": "test_stream_sid_123",
                "media": {
                    "track": "inbound",
                    "payload": payload
                }
            }
            await websocket.send(json.dumps(media_message))
            await asyncio.sleep(0.02) # Send packets every 20ms
        except websockets.exceptions.ConnectionClosed:
            logger.info("Sender: Connection closed.")
            break
        except Exception as e:
            logger.error(f"Sender error: {e}")
            break

async def run_combo_test():
    """
    Tests the customized combo order flow through a WebSocket connection.
    """
    uri = "ws://localhost:8000/api/media-stream"
    try:
        async with websockets.connect(uri) as websocket:
            logger.info("WebSocket connection established.")

            # 1. Send 'connected' and 'start' events
            await websocket.send(json.dumps({"event": "connected"}))
            start_message = {
                "event": "start",
                "start": {
                    "callSid": "test_call_sid_123",
                    "customParameters": {"restaurant_id": "LIMF", "language": "english"}
                }
            }
            await websocket.send(json.dumps(start_message))
            logger.info("Sent connected and start events.")

            # 2. Start sender and receiver tasks
            welcome_event = asyncio.Event()
            message_queue = asyncio.Queue()
            
            receiver_task = asyncio.create_task(receiver(websocket, welcome_event))
            sender_task = asyncio.create_task(sender(websocket, message_queue))

            # 3. Wait for the welcome message
            try:
                await asyncio.wait_for(welcome_event.wait(), timeout=15)
            except asyncio.TimeoutError:
                logger.error("Timeout: Did not receive welcome message.")
                sender_task.cancel()
                receiver_task.cancel()
                await asyncio.gather(sender_task, receiver_task, return_exceptions=True)
                return

            # 4. Simulate the conversation
            conversation = [
                "I want to order a customized combo",
                "shrimp",
                "one pound",
                "no",
                "spicy",
                "mild",
                "corn",
                "yes"
            ]

            for text in conversation:
                await message_queue.put(text)
                await asyncio.sleep(5) # Wait for the agent to respond

            # Wait for the queue to be fully processed
            await message_queue.join()
            await asyncio.sleep(5) # Final wait for last response

            logger.info("Conversation simulation finished.")
            
            # 5. Cleanup
            sender_task.cancel()
            receiver_task.cancel()
            await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

    except Exception as e:
        logger.error(f"An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(run_combo_test())
