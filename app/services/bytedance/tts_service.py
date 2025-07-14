from __future__ import annotations
import asyncio
import base64
import json
import logging
import struct
import time
import uuid
from typing import AsyncGenerator, Optional

import audioop
import websockets
from websockets.client import WebSocketClientProtocol

from app.services.bytedance.utils import construct_bytedance_header

logger = logging.getLogger(__name__)

# Constants
BYTEDANCE_TTS_API_URL = "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"
DEFAULT_TTS_VOICE = "zh_female_zhixingnvsheng_mars_bigtts"

class BytedanceTTSService:
    """Manages the connection and interaction with the Bytedance TTS WebSocket service."""

    def __init__(
        self,
        app_id: str,
        auth_token: str,
        caller_phone: Optional[str] = None,
        call_sid: Optional[str] = None,
    ):
        self.app_id = app_id
        self.auth_token = auth_token
        self.caller_phone = caller_phone
        self.call_sid = call_sid
        
        self.websocket: Optional[WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._is_closed = asyncio.Event()
        self.tts_interrupt_event = asyncio.Event()

    async def connect(self) -> None:
        """Establishes the WebSocket connection to the TTS service."""
        if self.websocket and self.websocket.open:
            logger.info(f"TTS Service ({self.call_sid}): Already connected.")
            return

        headers = {"Authorization": f"Bearer; {self.auth_token}"}
        try:
            logger.info(f"TTS Service ({self.call_sid}): Connecting to {BYTEDANCE_TTS_API_URL}")
            self.websocket = await websockets.connect(
                BYTEDANCE_TTS_API_URL, extra_headers=headers, open_timeout=5
            )
            logger.info(f"TTS Service ({self.call_sid}): Successfully connected.")
            
            self._is_closed.clear()
            self.tts_interrupt_event.clear()
            self._receive_task = asyncio.create_task(self._message_handler(), name=f"tts_receiver_{self.call_sid}")
        except Exception as e:
            logger.error(f"TTS Service ({self.call_sid}): Failed to connect: {e}", exc_info=True)
            self.websocket = None
            self._is_closed.set()
            raise

    async def _message_handler(self) -> None:
        """Handles incoming messages from the Bytedance TTS WebSocket."""
        if not self.websocket:
            return
        
        logger.info(f"TTS Service ({self.call_sid}): Listening for messages.")
        try:
            async for raw_message in self.websocket:
                if self._is_closed.is_set():
                    logger.info(f"TTS Service ({self.call_sid}): Stop event detected, exiting listener.")
                    break

                # If interrupted, just discard messages but keep listening
                if self.tts_interrupt_event.is_set():
                    continue

                if not isinstance(raw_message, bytes) or len(raw_message) < 4:
                    continue

                header_bytes = raw_message[:4]
                msg_type = (header_bytes[1] >> 4) & 0x0F
                msg_flags = header_bytes[1] & 0x0F

                if msg_type == 0b1011:  # Audio data
                    if msg_flags == 0: # ACK
                        continue
                    
                    payload_size = struct.unpack('>I', raw_message[8:12])[0]
                    audio_data_pcm8k = raw_message[12:12 + payload_size]
                    
                    if audio_data_pcm8k:
                        audio_data_mulaw = audioop.lin2ulaw(audio_data_pcm8k, 2)
                        await self._audio_queue.put(audio_data_mulaw)

                    is_last_message = (msg_flags == 0b0010 or msg_flags == 0b0011)
                    if is_last_message:
                        logger.info(f"TTS Service ({self.call_sid}): Final audio flag received.")
                        await self._audio_queue.put(b"") # Sentinel value

                elif msg_type == 0b1111:  # Error message
                    error_code = struct.unpack('>I', raw_message[4:8])[0]
                    error_msg_size = struct.unpack('>I', raw_message[8:12])[0]
                    error_message = raw_message[12:12 + error_msg_size].decode('utf-8')
                    logger.error(f"TTS Service ({self.call_sid}): Received error - Code: {error_code}, Msg: '{error_message}'")
                    await self._audio_queue.put(b"") # Sentinel to unblock generator
                    break

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"TTS Service ({self.call_sid}): Connection closed - {e.code} {e.reason}.")
        except asyncio.CancelledError:
            logger.info(f"TTS Service ({self.call_sid}): Message handler cancelled.")
        except Exception as e:
            logger.error(f"TTS Service ({self.call_sid}): Exception in message handler: {e}", exc_info=True)
        finally:
            logger.info(f"TTS Service ({self.call_sid}): Exiting message handler.")
            self._is_closed.set()
            if self._audio_queue.empty():
                await self._audio_queue.put(b"") # Ensure generator unblocks

    async def speak(self, text: str) -> Tuple[AsyncGenerator[bytes, None], float]:
        """
        Sends text for synthesis and yields the resulting audio chunks (mu-law).
        Returns a tuple of the audio generator and the request timestamp.
        """
        if not self.websocket or not self.websocket.open:
            logger.error(f"TTS Service ({self.call_sid}): Cannot speak, WebSocket is not open.")
            # Return a dummy generator and current time to prevent crashes
            async def dummy_generator():
                yield b""
            return dummy_generator(), time.perf_counter()

        req_id = str(uuid.uuid4())
        logger.info(f"TTS Service ({self.call_sid}): Requesting TTS for reqid {req_id}: '{text[:50]}...'")
        self.tts_interrupt_event.clear()

        request_payload = {
            "app": {"appid": self.app_id, "token": self.auth_token, "cluster": "volcano_tts"},
            "user": {"uid": self.caller_phone or req_id},
            "audio": {"voice_type": DEFAULT_TTS_VOICE, "encoding": "pcm", "rate": 8000},
            "request": {"reqid": req_id, "text": text, "text_type": "plain", "operation": "submit"}
        }
        payload_bytes = json.dumps(request_payload).encode('utf-8')
        header = construct_bytedance_header(1, 1, 0b0001, 0, 0b0001, 0)
        message = header + struct.pack('>I', len(payload_bytes)) + payload_bytes

        request_time = time.perf_counter()
        
        async def audio_generator():
            try:
                await self.websocket.send(message)
                logger.info(f"TTS Service ({self.call_sid}): Successfully sent TTS request for reqid {req_id}.")

                while True:
                    if self.tts_interrupt_event.is_set():
                        logger.info(f"TTS Service ({self.call_sid}): Interrupt detected in generator for reqid {req_id}.")
                        break
                    
                    audio_chunk = await self._audio_queue.get()
                    
                    if audio_chunk == b"": # Sentinel value
                        break
                    yield audio_chunk
            
            except Exception as e:
                logger.error(f"TTS Service ({self.call_sid}): Error during speak operation for reqid {req_id}: {e}", exc_info=True)
            finally:
                logger.info(f"TTS Service ({self.call_sid}): Finished speaking for reqid {req_id}.")
                # Clear queue in case of errors or interruptions
                while not self._audio_queue.empty():
                    self._audio_queue.get_nowait()

        return audio_generator(), request_time

    async def interrupt(self):
        """Interrupts any ongoing TTS playback."""
        logger.info(f"TTS Service ({self.call_sid}): Interrupting current speech.")
        self.tts_interrupt_event.set()
        # Clear the queue of any pending audio
        while not self._audio_queue.empty():
            self._audio_queue.get_nowait()
        # The message handler will see the event and stop processing.
        # The speak() generator will also stop yielding.

    async def close(self) -> None:
        """Closes the WebSocket connection and cleans up resources."""
        logger.info(f"TTS Service ({self.call_sid}): Closing connection.")
        self._is_closed.set()
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            await asyncio.gather(self._receive_task, return_exceptions=True)
        
        if self.websocket and self.websocket.open:
            await self.websocket.close(code=1000)
        
        logger.info(f"TTS Service ({self.call_sid}): Connection closed.")
