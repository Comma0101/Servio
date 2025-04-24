"""
Deepgram Service - Handle interactions with Deepgram Voice API
"""
import json
import logging
import asyncio
import websockets
from typing import Dict, Any, Optional, Callable, Awaitable, List

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DeepgramService:
    """Service for handling communications with the Deepgram Voice Agent API"""
    
    def __init__(self, api_key: str, config: Dict[str, Any]):
        """
        Initialize the Deepgram service.
        
        Args:
            api_key: Deepgram API key
            config: Configuration for the Deepgram API 
        """
        self.api_key = api_key
        self.config = config
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.message_handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        
        logger.info("Initialized Deepgram service")
        
    async def connect(self) -> None:
        """Connect to the Deepgram Voice Agent API"""
        max_retries = 3
        retry_count = 0
        retry_delay = 1  # seconds
        
        while retry_count < max_retries:
            try:
                # Create extra headers with API key authorization
                extra_headers = {
                    "Authorization": f"Token {self.api_key}"
                }
                
                self.websocket = await websockets.connect(
                    'wss://agent.deepgram.com/agent',
                    extra_headers=extra_headers,
                    ping_interval=30,  # Send ping every 30 seconds to keep connection alive
                    ping_timeout=10    # Wait 10 seconds for pong before considering connection dead
                )
                logger.info("Connected to Deepgram Voice Agent API")
                self.connected = True
                
                # Send initial configuration
                await self.send_configuration(self.config)
                
                return self.websocket
            except Exception as e:
                logger.error(f"Error connecting to Deepgram (attempt {retry_count+1}/{max_retries}): {e}")
                self.connected = False
                retry_count += 1
                
                if retry_count < max_retries:
                    logger.info(f"Retrying connection in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error("Maximum retries reached, could not connect to Deepgram")
                    raise
    
    async def send_configuration(self, config: Dict[str, Any]) -> None:
        """Send configuration to Deepgram"""
        if not self.websocket:
            raise ValueError("Not connected to Deepgram")
        
        if not self.connected:
            logger.warning("Deepgram connection is closed, cannot send configuration")
            return
        
        try:
            config_json = json.dumps(config)
            await self.websocket.send(config_json)
            logger.info("Sent configuration to Deepgram")
        except Exception as e:
            logger.error(f"Error sending configuration to Deepgram: {e}")
            self.connected = False
            raise
    
    async def send_audio(self, audio_data: bytes) -> bool:
        """
        Send audio data to Deepgram
        
        Returns:
            bool: True if audio was sent successfully, False if connection is closed
        """
        if not self.websocket:
            logger.warning("Not connected to Deepgram, cannot send audio")
            self.connected = False
            return False
        
        if not self.connected:
            logger.warning("Deepgram connection is closed, cannot send audio")
            return False
        
        try:
            # Send raw binary data directly to websocket
            await self.websocket.send(audio_data)
            return True
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"Deepgram connection closed while sending audio: {e.code} - {e.reason}")
            self.connected = False
            # Don't raise here - allow reconnection logic to handle this
            return False
        except Exception as e:
            logger.error(f"Error sending audio to Deepgram: {e}")
            self.connected = False
            raise
    
    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data to Deepgram"""
        if not self.websocket:
            raise ValueError("Not connected to Deepgram")
        
        if not self.connected:
            logger.warning("Deepgram connection is closed, cannot send JSON")
            return
        
        try:
            json_data = json.dumps(data)
            await self.websocket.send(json_data)
            logger.info(f"Sent JSON data to Deepgram: {data.get('type', 'unknown type')}")
        except Exception as e:
            logger.error(f"Error sending JSON data to Deepgram: {e}")
            self.connected = False
            raise
    
    async def send_ping(self) -> bool:
        """
        Send a WebSocket protocol ping to keep the connection alive
        
        Returns:
            bool: True if ping was sent successfully, False otherwise
        """
        if not self.websocket or not self.connected:
            logger.warning("Deepgram connection is closed, cannot send ping")
            return False
            
        try:
            # Send a WebSocket protocol ping (not a JSON message)
            await self.websocket.ping()
            logger.debug("Sent WebSocket ping to Deepgram")
            return True
        except Exception as e:
            logger.error(f"Error sending ping to Deepgram: {e}")
            self.connected = False
            return False
    
    async def receive_messages(self) -> None:
        """Receive and process messages from Deepgram"""
        if not self.websocket:
            raise ValueError("Not connected to Deepgram")
        
        try:
            async for message in self.websocket:
                if isinstance(message, str):
                    # Process JSON messages
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "unknown")
                        logger.info(f"Received message from Deepgram, type: {msg_type}")
                        
                        # Enhanced logging for debugging function calls
                        if msg_type == "FunctionCallRequest":
                            logger.info(f"FUNCTION CALL REQUEST RECEIVED: {json.dumps(data)}")
                            function_name = data.get('function_name', 'unknown')
                            logger.info(f"Function name: {function_name}")
                        elif "function" in message.lower():
                            logger.info(f"Message contains 'function' but type is {msg_type}: {message[:200]}")
                        
                        # Log ALL message types for debugging
                        logger.info(f"DEEPGRAM MESSAGE CONTENT: {message[:200]}...")
                        
                        logger.debug(f"Deepgram message details: {message}")
                        
                        # Process message through all registered handlers
                        logger.info(f"Number of registered message handlers: {len(self.message_handlers)}")
                        for i, handler in enumerate(self.message_handlers):
                            logger.info(f"Calling handler #{i}, type: {type(handler).__name__}")
                            await handler(data)
                            logger.info(f"Handler #{i} completed processing")
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse Deepgram message: {message}")
                elif isinstance(message, bytes):
                    # Process binary messages (audio)
                    logger.info(f"Received binary message from Deepgram: {len(message)} bytes")
                    
                    # Pass binary messages to all registered handlers
                    for handler in self.message_handlers:
                        await handler(message)
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"Deepgram connection closed: {e}")
            self.connected = False
        except asyncio.CancelledError:
            logger.info("Receive messages task cancelled")
            await self.close()
            raise
        except Exception as e:
            logger.error(f"Error in receive_from_deepgram: {e}")
            self.connected = False
    
    async def check_connection(self) -> bool:
        """
        Check if the Deepgram connection is still alive
        
        Returns:
            bool: True if connected, False otherwise
        """
        if not self.websocket or not self.connected:
            return False
            
        try:
            # Check if the websocket is still open
            if self.websocket.closed:
                logger.warning("Deepgram WebSocket reported as closed")
                self.connected = False
                return False
                
            # Try sending a keepalive message
            await self.send_ping()
            return True
        except Exception as e:
            logger.error(f"Error checking Deepgram connection: {e}")
            self.connected = False
            return False
    
    def add_message_handler(self, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Add a message handler function"""
        self.message_handlers.append(handler)
    
    async def close(self) -> None:
        """Close the connection to Deepgram"""
        if self.websocket and self.connected:
            try:
                await self.websocket.close()
                logger.info("Closed connection to Deepgram")
            except Exception as e:
                logger.error(f"Error closing connection to Deepgram: {e}")
            finally:
                self.connected = False
