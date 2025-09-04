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
    """Service for handling communications with the Deepgram API (Agent and STT)"""

    def __init__(self, api_key: str, config: Dict[str, Any], use_stt_endpoint: bool = False, stop_event: Optional[asyncio.Event] = None):
        """
        Initialize the Deepgram service.

        Args:
            api_key: Deepgram API key
            config: Configuration for the Deepgram API
            use_stt_endpoint: If True, connect to /v1/listen for STT. Otherwise, agent endpoint.
            stop_event: An asyncio Event to signal when to stop processing.
        """
        self.api_key = api_key
        self.config = config
        self.use_stt_endpoint = use_stt_endpoint
        self.stop_event = stop_event
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.is_final_confirmation_sent = False
        self.message_handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []

        logger.info(f"Initialized Deepgram service (STT mode: {self.use_stt_endpoint})")

    async def connect(self) -> None:
        """Connect to the Deepgram API (Agent or STT endpoint)"""
        logger.info(f"Connect method called. Stop event status: {self.stop_event.is_set() if self.stop_event else 'Not set'}")
        max_retries = 3
        retry_count = 0
        retry_delay = 1  # seconds

        while retry_count < max_retries:
            if self.stop_event and self.stop_event.is_set():
                logger.info("Stop event is set, aborting Deepgram connection attempt.")
                return

            try:
                extra_headers = {"Authorization": f"Token {self.api_key}"}
                
                if self.use_stt_endpoint:
                    # Connect to dedicated STT endpoint: wss://api.deepgram.com/v1/listen
                    # STT parameters are passed as query parameters
                    stt_params = self.config.get("listen", {})
                    query_string_parts = []
                    
                    provider_config = stt_params.get("provider", stt_params)
                    for key, value in provider_config.items():
                        if value is not None:
                            query_string_parts.append(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
                        
                        # Default to interim_results=False, smart_format=True if not specified for STT
                        if "interim_results" not in stt_params: query_string_parts.append("interim_results=false")
                        if "smart_format" not in stt_params: query_string_parts.append("smart_format=true")
                        if "punctuate" not in stt_params: query_string_parts.append("punctuate=true")


                        # Ensure essential parameters are present for STT
                        if "model" not in stt_params:
                            logger.warning("STT config missing 'model', defaulting to 'nova-3'")
                            query_string_parts.append("model=nova-3") # Or handle as error
                        if "encoding" not in stt_params:
                            logger.warning("STT config missing 'encoding', defaulting to 'linear16'")
                            query_string_parts.append("encoding=linear16") # Or handle as error
                        if "sample_rate" not in stt_params:
                            logger.warning("STT config missing 'sample_rate', defaulting to 16000")
                            query_string_parts.append("sample_rate=16000")


                    query_string = "&".join(query_string_parts)
                    connect_url = f"wss://api.deepgram.com/v1/listen?{query_string}"
                    
                    logger.info(f"Connecting to Deepgram STT endpoint: {connect_url}")
                    self.websocket = await websockets.connect(
                        connect_url,
                        extra_headers=extra_headers,
                        ping_interval=30,
                        ping_timeout=10
                    )
                    logger.info("Connected to Deepgram STT API (/v1/listen)")
                    self.connected = True
                    # For STT endpoint, no initial JSON config message is sent.
                    # Readiness can be assumed after connection, or handler can wait for first Metadata message.
                    # We will notify handlers that settings are "applied" conceptually.
                    for handler in self.message_handlers:
                        await handler({"type": "STTConnected", "description": "Successfully connected to /v1/listen"})

                else: # Original Agent Endpoint Logic
                    # Add instructions to control conversation ending
                    if "agent" not in self.config: self.config["agent"] = {}
                    if "think" not in self.config["agent"]: self.config["agent"]["think"] = {}
                    # The 'instructions' field is now expected to be fully formed in self.config
                    # No suffix will be appended here.

                    # Default to nova-3 for agent's STT model if not specified
                    if "listen" not in self.config["agent"]:
                        self.config["agent"]["listen"] = {}
                    if "provider" not in self.config["agent"]["listen"]:
                        self.config["agent"]["listen"]["provider"] = {}
                    if "model" not in self.config["agent"]["listen"]["provider"]:
                        logger.warning("Agent config missing STT model, defaulting to 'nova-3'")
                        self.config["agent"]["listen"]["provider"]["model"] = "nova-3"
                                        
                    sanitized_config = json.dumps(self.config) # Consider more robust sanitization
                    logger.info(f"Connecting to Deepgram Agent with configuration: {sanitized_config}")
                    
                    self.websocket = await websockets.connect(
                        'wss://agent.deepgram.com/v1/agent/converse',
                        extra_headers=extra_headers,
                        ping_interval=30,
                        ping_timeout=10
                    )
                    logger.info("Connected to Deepgram Voice Agent API")
                    self.connected = True
                    await self.send_configuration(self.config) # Send initial config for agent
                    logger.info("Sent configuration (without suffix modification) to Deepgram Agent")

                return self.websocket # type: ignore
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
        if not await self.ensure_alive(): # Added ensure_alive call
            logger.warning("Deepgram connection not alive (checked by ensure_alive), cannot send audio.")
            return False

        # self.connected should be True here if ensure_alive succeeded
        if not self.connected: # Double check
            logger.warning("Deepgram connection is closed after ensure_alive check, cannot send audio")
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
            # raise # Don't re-raise, allow calling function to decide based on return value
            return False # Explicitly return False on exception
    
    async def ensure_alive(self) -> bool:
        """Checks the WebSocket connection and attempts to reconnect if necessary."""
        if self.websocket and not self.websocket.closed:
            # Optionally, could add a quick ping test here if just checking websocket.closed isn't enough
            # For now, assume if it's not None and not closed, it's alive for sending.
            # self.connected should accurately reflect the state from send/receive operations.
            return self.connected # Rely on self.connected which is updated by send/receive

        logger.info("Deepgram WebSocket closed or not initialized. Attempting to (re)connect...")
        try:
            await self.connect() # connect() has its own retry logic and sets self.connected
            if self.connected:
                logger.info("Successfully (re)connected to Deepgram via ensure_alive.")
                # Any re-registration of handlers or re-sending of initial config
                # would typically be managed by the class using this service (e.g., AudioHandler)
                # or by making connect() idempotent regarding initial setup if called multiple times.
                # For now, connect() re-sends config if it's an agent connection.
            else:
                logger.error("Failed to (re)connect to Deepgram via ensure_alive after connect() attempt.")
            return self.connected
        except Exception as e:
            logger.error(f"Exception during ensure_alive -> connect(): {e}")
            self.connected = False # Ensure state is accurate
            return False

    async def send_json(self, data: Dict[str, Any]) -> bool:
        """Send JSON data to Deepgram. Returns True on success, False on failure."""
        if not await self.ensure_alive():
            logger.warning("Deepgram connection not alive (checked by ensure_alive), cannot send JSON.")
            return False
        
        # self.connected should be True here if ensure_alive succeeded and didn't reconnect,
        # or if ensure_alive reconnected successfully.
        if not self.connected: # Double check, ensure_alive might have failed to connect
             logger.warning("Deepgram connection is closed after ensure_alive check, cannot send JSON")
             return False
        
        try:
            # Check if this is a message to the Chinese voice model
            is_chinese_message = False
            if self.config and 'agent' in self.config and 'speak' in self.config['agent']:
                speak_model = self.config['agent']['speak'].get('model', '')
                if 'zh' in speak_model and data.get('type') == 'InjectAgentMessage':
                    is_chinese_message = True
                    logger.info("Detected message for Chinese voice model")
                    
                    # For Chinese voice model, we need to ensure the message is properly formatted
                    message = data.get('message', '')
                    
                    # Check if message contains any non-Chinese characters
                    if any(not ('\u4e00' <= c <= '\u9fff' or c in '。，！？；：（）《》""''、') for c in message):
                        logger.warning(f"Message contains non-Chinese characters, which may cause errors: {message}")
                    
                    # Log the exact message being sent for debugging
                    logger.info(f"Chinese message content: {message}")
            
            json_data = json.dumps(data)
            logger.info(f"RAW JSON PAYLOAD SENT TO DEEPGRAM: {json_data}")
            await self.websocket.send(json_data)
            logger.info(f"Sent JSON data to Deepgram: {data.get('type', 'unknown type')}")
            return True
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"Deepgram connection closed while sending JSON: {e.code} - {e.reason}")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"Error sending JSON data to Deepgram: {e}")
            self.connected = False
            # raise # Avoid re-raising to allow caller to handle based on boolean
            return False

    async def send_raw_json_string(self, json_string: str) -> bool:
        """Sends a pre-formatted JSON string to Deepgram. Returns True on success, False on failure."""
        if not await self.ensure_alive():
            logger.warning("Deepgram connection not alive (checked by ensure_alive), cannot send raw JSON string.")
            return False
        
        if not self.connected:
             logger.warning("Deepgram connection is closed after ensure_alive check, cannot send raw JSON string")
             return False
        try:
            await self.websocket.send(json_string)
            logger.info(f"Sent raw JSON string to Deepgram: {json_string[:100]}")
            return True
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"Deepgram connection closed while sending raw JSON string: {e.code} - {e.reason}")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"Error sending raw JSON string to Deepgram: {e}")
            self.connected = False
            return False
    
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
        """Receive and process messages from Deepgram.
        This loop will exit if the connection is closed or a critical error occurs.
        The caller (AudioHandler) is responsible for attempting to reconnect and recall this method.
        """
        if not self.websocket or not self.connected: # Check self.connected as well
            logger.error("Cannot receive messages: Not connected to Deepgram or websocket is None.")
            # Set self.connected to False to signal the caller loop to attempt reconnection.
            self.connected = False 
            return # Exit, so AudioHandler's loop can try ensure_alive

        logger.info("Starting to receive messages from Deepgram...")
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
            self.connected = False # Connection is definitely closed if loop exits normally or due to this error
            logger.error(f"Deepgram connection closed: {e}") # Log the specific close reason
        except asyncio.CancelledError:
            logger.info("Deepgram receive_messages task cancelled.")
            # self.connected should be managed by close() if called
            # If not explicitly closed, it might still be considered connected until ensure_alive checks
            await self.close() # Ensure cleanup on cancellation
            raise # Re-raise CancelledError to stop the calling loop in AudioHandler
        except Exception as e:
            logger.error(f"Error in receive_messages from Deepgram: {e}")
            self.connected = False # Critical error, mark as not connected
            # Do not raise here, let AudioHandler's loop decide to retry or stop.
        finally:
            logger.info("Exiting Deepgram receive_messages inner loop.")
            # self.connected state should reflect the outcome of the loop.
            # If loop exited due to ConnectionClosed, self.connected is already False.
            # If loop exited due to other unhandled exception, self.connected is False.
            # If loop was cancelled and close() was called, self.connected is False.

    
    async def check_connection(self) -> bool: # This method might be less used if ensure_alive is robust
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
