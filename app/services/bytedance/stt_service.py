from __future__ import annotations
import asyncio
import json
import logging
import struct
import time
import uuid
from typing import AsyncGenerator, Optional, Tuple

import websockets
from websockets.client import WebSocketClientProtocol

from app.services.bytedance.utils import construct_bytedance_header

logger = logging.getLogger(__name__)

# Constants
BYTEDANCE_STT_API_URL_BIGMODEL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
STT_FULL_CLIENT_REQUEST = 0b0001
STT_AUDIO_ONLY_REQUEST = 0b0010
STT_NO_SEQUENCE = 0b0000
STT_JSON_SERIALIZATION = 0b0001
STT_NO_SERIALIZATION = 0b0000
STT_NO_COMPRESSION = 0b0000

class BytedanceSTTService:
    """Manages the connection and interaction with the Bytedance STT WebSocket service."""

    def __init__(
        self,
        app_key: str,
        access_key: str,
        resource_id: str,
        caller_phone: Optional[str] = None,
        call_sid: Optional[str] = None,
    ):
        self.app_key = app_key
        self.access_key = access_key
        self.resource_id = resource_id
        self.caller_phone = caller_phone
        self.call_sid = call_sid
        self.connect_id: str = str(uuid.uuid4())
        
        self.websocket: Optional[WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._transcript_queue: asyncio.Queue[Tuple[str, bool, Optional[float]]] = asyncio.Queue()
        self._is_closed = asyncio.Event()
        self._is_paused = asyncio.Event() # New: Event to control audio sending
        self._turn_start_time: Optional[float] = None

    def pause(self) -> None:
        """Pauses sending audio to the STT service."""
        if not self._is_paused.is_set():
            logger.info(f"STT Service ({self.call_sid}): Pausing audio stream.")
            self._is_paused.set()

    def resume(self) -> None:
        """Resumes sending audio to the STT service."""
        if self._is_paused.is_set():
            logger.info(f"STT Service ({self.call_sid}): Resuming audio stream.")
            self._is_paused.clear()

    async def connect(self) -> None:
        """Establishes the WebSocket connection and initializes the STT stream."""
        if self.websocket and self.websocket.open:
            logger.info(f"STT Service ({self.call_sid}): Already connected.")
            return

        headers = {
            "X-Api-App-Key": self.app_key,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": self.connect_id,
        }
        try:
            logger.info(f"STT Service ({self.call_sid}): Connecting to {BYTEDANCE_STT_API_URL_BIGMODEL}")
            self.websocket = await websockets.connect(
                BYTEDANCE_STT_API_URL_BIGMODEL, extra_headers=headers, open_timeout=5
            )
            logger.info(f"STT Service ({self.call_sid}): Successfully connected.")
            
            req_payload = {
                "user": {"uid": self.caller_phone or self.connect_id},
                "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
                "request": {
                    "model_name": "bigmodel", "enable_itn": True, "enable_punc": True,
                    "show_utterances": True, "enable_vad": True, "end_window_size": 400,
                    "force_to_speech_time": 1500
                }
            }
            payload_bytes = json.dumps(req_payload).encode('utf-8')
            header = construct_bytedance_header(1, 1, STT_FULL_CLIENT_REQUEST, STT_NO_SEQUENCE, STT_JSON_SERIALIZATION, STT_NO_COMPRESSION)
            message = header + struct.pack('>I', len(payload_bytes)) + payload_bytes
            await self.websocket.send(message)
            logger.info(f"STT Service ({self.call_sid}): Sent initial full client request.")

            self._is_closed.clear()
            self._receive_task = asyncio.create_task(self._message_handler(), name=f"stt_receiver_{self.call_sid}")
        except Exception as e:
            logger.error(f"STT Service ({self.call_sid}): Failed to connect or initialize: {e}", exc_info=True)
            self.websocket = None
            self._is_closed.set()
            raise

    async def send_audio(self, audio_chunk: bytes, is_last_chunk: bool = False) -> None:
        """Sends an audio chunk to the STT service."""
        if self._is_paused.is_set():
            # logger.debug(f"STT Service ({self.call_sid}): Audio sending is paused. Discarding chunk.")
            return
            
        if not self.websocket or not self.websocket.open:
            logger.warning(f"STT Service ({self.call_sid}): Cannot send audio, WebSocket is not open.")
            return
        
        if audio_chunk and self._turn_start_time is None:
            self._turn_start_time = time.perf_counter()

        flags = 0b0010 if is_last_chunk else 0b0000
        header = construct_bytedance_header(1, 1, STT_AUDIO_ONLY_REQUEST, flags, STT_NO_SERIALIZATION, STT_NO_COMPRESSION)
        message = header + struct.pack('>I', len(audio_chunk)) + audio_chunk
        try:
            await self.websocket.send(message)
        except Exception as e:
            logger.error(f"STT Service ({self.call_sid}): Error sending audio: {e}", exc_info=True)

    async def _message_handler(self) -> None:
        """Handles incoming messages from the Bytedance STT WebSocket."""
        if not self.websocket:
            return
        
        processed_sequence_numbers = set()
        logger.info(f"STT Service ({self.call_sid}): Listening for messages.")
        try:
            async for raw_message in self.websocket:
                if not isinstance(raw_message, bytes) or len(raw_message) < 4:
                    logger.warning(f"STT ({self.call_sid}): Received invalid message (non-bytes or too short).")
                    continue

                header_bytes = raw_message[:4]
                msg_type = (header_bytes[1] >> 4) & 0x0F

                if msg_type == 0b1001:  # Server-to-Client Full Response
                    sequence_number = struct.unpack('>I', raw_message[4:8])[0]
                    if sequence_number in processed_sequence_numbers:
                        continue
                    processed_sequence_numbers.add(sequence_number)

                    payload_size = struct.unpack('>I', raw_message[8:12])[0]
                    payload_data = json.loads(raw_message[12:12 + payload_size].decode('utf-8'))
                    
                    result = payload_data.get("result")
                    if not result:
                        continue

                    transcript = result.get("text", "").strip()
                    if not transcript:
                        continue
                    
                    server_msg_flags = header_bytes[1] & 0x0F
                    is_final = (server_msg_flags & 0x02) != 0
                    
                    latency = None
                    if self._turn_start_time:
                        latency = time.perf_counter() - self._turn_start_time

                    await self._transcript_queue.put((transcript, is_final, latency))

                    if is_final:
                        self._turn_start_time = None # Reset for the next turn

                elif msg_type == 0b1111:  # Error message
                    error_code = struct.unpack('>I', raw_message[4:8])[0]
                    error_msg_size = struct.unpack('>I', raw_message[8:12])[0]
                    error_message = raw_message[12:12 + error_msg_size].decode('utf-8')
                    logger.error(f"STT Service ({self.call_sid}): Received error - Code: {error_code}, Msg: '{error_message}'")
                    break # Stop processing on error

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"STT Service ({self.call_sid}): Connection closed - {e.code} {e.reason}.")
        except asyncio.CancelledError:
            logger.info(f"STT Service ({self.call_sid}): Message handler cancelled.")
        except Exception as e:
            logger.error(f"STT Service ({self.call_sid}): Exception in message handler: {e}", exc_info=True)
        finally:
            logger.info(f"STT Service ({self.call_sid}): Exiting message handler.")
            self._is_closed.set()
            # Put a sentinel value to unblock the generator
            await self._transcript_queue.put(("", True, None))

    async def transcripts(self) -> AsyncGenerator[Tuple[str, bool, Optional[float]], None]:
        """Yields transcripts and latency as they become available."""
        while not self._is_closed.is_set():
            try:
                transcript, is_final, latency = await asyncio.wait_for(self._transcript_queue.get(), timeout=1.0)
                
                if transcript:
                    yield transcript, is_final, latency

                if is_final and not transcript: # Sentinel value
                    break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
        logger.info(f"STT Service ({self.call_sid}): Transcript generator finished.")


    async def close(self) -> None:
        """Closes the WebSocket connection and cleans up resources."""
        logger.info(f"STT Service ({self.call_sid}): Closing connection.")
        self._is_closed.set()
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            await asyncio.gather(self._receive_task, return_exceptions=True)
        
        if self.websocket and self.websocket.open:
            await self.websocket.close(code=1000)
        
        logger.info(f"STT Service ({self.call_sid}): Connection closed.")
