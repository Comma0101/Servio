"""
Chinese Audio Handler - Process audio streams using OpenAI Whisper for speech recognition and OpenAI TTS for responses
"""
import asyncio
import base64
import json
import logging
import re
import tempfile
import io
from fastapi import WebSocket, WebSocketDisconnect # Import WebSocketDisconnect
import os
from typing import Dict, Any, Optional, List
import openai
import time
import traceback
from pydub import AudioSegment
import subprocess
import webrtcvad # Added VAD library
import audioop # For audio conversions
# Use the v1p1beta1 library for the async client
from google.cloud import speech_v1p1beta1 as speech
from google.cloud.texttospeech_v1 import TextToSpeechAsyncClient
from google.cloud.texttospeech_v1.types import SynthesisInput, VoiceSelectionParams, AudioConfig, AudioEncoding
# import queue # No longer needed
# import threading # No longer needed
import math
import functools # For SMS scheduling

# App-specific imports for tool execution
from app.services.database_service import save_order_details
from app.utils.twilio import send_sms # For SMS notification
# Removed: from app.api.websocket import get_caller_phone to break circular import
# Note: app.config.settings and app.utils.square imports are removed as Square logic is omitted.

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Default system message for the Chinese voice agent
DEFAULT_CHINESE_SYSTEM_MESSAGE = (
    "你是KK餐厅的助手。"
    "在对话过程中，收集菜品、数量和规格。"
    "询问缺失的规格。对于未完成的订单请使用 'IN PROGRESS' 状态，对于已完成的订单请使用 'DONE' 状态。"
    "当你认为订单完成后，请使用 'order_summary' 功能来提供订单的结构化总结，以便后端处理。\n\n"
    "重要指示：\n"
    "1. 保持友好、礼貌、乐于助人的态度。\n"
    "2. 只提供菜单上有的菜品。\n"
    "3. 为顾客点单时，务必与顾客逐一确认所点菜品。\n"
    "4. 顾客点单结束后，总结整个订单并与顾客确认总金额。\n"
    "5. 订单确认完毕后，告知顾客将会收到一条包含订单号的短信通知。然后结束对话。\n"
    "6. 如果顾客索要菜单，可以提议通过短信发送菜单，或者简要介绍当前供应的菜品。\n"
    "7. 回答应力求简洁自然，符合电话口语习惯。"
)

# Define the Chinese tool schema for order_summary
order_summary_tool_schema_cn = {
    "type": "function",
    "function": {
        "name": "order_summary",
        "description": "提供顾客订单的结构化总结，用于后端处理。对于处理中的订单，请使用 'IN PROGRESS' 状态；当顾客已确认订单并准备最终完成时，请使用 'DONE' 状态。(Provides a structured summary of the customer's order for backend processing. For orders still in progress, use 'IN PROGRESS' status; when the customer has confirmed the order and it's ready to be finalized, use 'DONE' status.)",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "订单中包含的所有商品列表。(A list of all items included in the order.)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "商品的标准名称。(The standard name of the item.)"
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "所选商品的数量。(The quantity of the selected item.)"
                            },
                            "variation": {
                                "type": ["string", "null"],
                                "description": "商品所选的规格或口味（如果适用）。例如：'大杯', '微辣'。(The selected variation or flavor of the item, if applicable. For example: 'Large', 'Mild Spicy'.)"
                            }
                        },
                        "required": ["name", "quantity"]
                    }
                },
                "total_price": {
                    "type": "number",
                    "description": "整个订单的计算总金额。(The calculated total amount for the entire order.)"
                },
                "summary": {
                    "type": "string",
                    "description": "订单总结的当前状态。使用 'IN PROGRESS' 表示订单内容可能还会更改，使用 'DONE' 表示订单内容已由顾客最终确认。(The current status of the order summary. Use 'IN PROGRESS' if the order contents might still change, use 'DONE' if the order contents have been finalized by the customer.)",
                    "enum": ["IN PROGRESS", "DONE"]
                }
            },
            "required": ["items", "total_price", "summary"]
        }
    }
}

class ChineseAudioHandler:
    """Handler for processing Chinese audio using OpenAI Whisper for recognition and OpenAI TTS for responses"""
    
    def __init__(self, 
            websocket, 
            client_id=None, 
            openai_api_key=None,
            system_message=None,
            verbose_logging=False,
            send_welcome_message=False,  # Default to not sending welcome message
            caller_phone: Optional[str] = None # Added caller_phone parameter
        ):
        """Initialize the Chinese audio handler"""
        self.websocket = websocket
        self.client_id = client_id
        self.openai_api_key = openai_api_key
        self.system_message = system_message or DEFAULT_CHINESE_SYSTEM_MESSAGE # Use the new default
        self.verbose_logging = verbose_logging  # Flag to control verbose logging
        self.send_welcome_message = send_welcome_message  # Flag to control welcome message
        self.caller_phone = caller_phone # Store caller_phone
        
        # Initialize OpenAI client for chat, speech recognition, and TTS
        self.openai_client = openai.AsyncOpenAI(api_key=openai_api_key)

        # Google Cloud Speech-to-Text setup (using AsyncClient)
        self.speech_client: Optional[speech.SpeechAsyncClient] = None
        self.streaming_config: Optional[speech.StreamingRecognitionConfig] = None
        self.google_stt_stream = None
        self._google_audio_queue = asyncio.Queue()
        self._stt_stream_started = False
        self._google_stt_task = None
        self._current_utterance_has_speech = False
        
        try:
            self.speech_client = speech.SpeechAsyncClient()
            self._init_google_stt_config() # Depends on self.speech_client
            logger.info("Google SpeechAsyncClient (STT) initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google SpeechAsyncClient (STT): {e}", exc_info=True)
            # self.speech_client will remain None

        # Google Cloud Text-to-Speech setup
        self.tts_client: Optional[TextToSpeechAsyncClient] = None
        try:
            self.tts_client = TextToSpeechAsyncClient()
            logger.info("Google TextToSpeechAsyncClient (TTS) initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google TextToSpeechAsyncClient (TTS): {e}", exc_info=True)
            # self.tts_client will remain None

        # Create conversation history for context
        self.conversation_history = [{"role": "system", "content": self.system_message}]
        
        # Track call state
        self.call_connected = False
        self.stop_event_handled = False
        self.is_ready = False
        self.welcome_sent = False
        
        # Echo cancellation system
        self.is_speaking = False
        self.last_speech_end_time = 0
        self.echo_cancellation_timeout = 2.0  # Reduced to 2.0 seconds after speaking before allowing new audio processing
        self.last_spoken_text = ""  # The last text we spoke, used for echo detection
        self.similarity_threshold = 0.2  # Reduced to 0.2 for much more sensitive echo detection
        
        # VAD Initialization & Buffering
        self.vad = webrtcvad.Vad()
        self.vad.set_mode(1)  # Aggressiveness mode (0-3), 1 is less aggressive
        self.vad_frame_duration_ms = 30  # VAD frame duration in ms (10, 20, or 30)
        self.vad_sample_rate = 8000 # Sample rate for VAD (Twilio uses 8kHz)
        logger.info(f"VAD initialized: mode=1, frame_duration_ms={self.vad_frame_duration_ms}, sample_rate={self.vad_sample_rate}") # ADDED
        # Bytes per frame for 8kHz, 16-bit PCM (VAD input)
        self.vad_pcm_bytes_per_frame = (self.vad_sample_rate * self.vad_frame_duration_ms // 1000) * 2 
        # Bytes per frame for 8kHz, 8-bit mulaw (Twilio input)
        self.vad_mulaw_bytes_per_frame = self.vad_pcm_bytes_per_frame // 2
        self.vad_silence_timeout_ms = 700 # How long silence before considering end of speech (tune this)
        self.vad_min_speech_duration_ms = 250 # Minimum duration of speech to process
        logger.info(f"VAD params: pcm_bytes_per_frame={self.vad_pcm_bytes_per_frame}, mulaw_bytes_per_frame={self.vad_mulaw_bytes_per_frame}, silence_timeout_ms={self.vad_silence_timeout_ms}, min_speech_duration_ms={self.vad_min_speech_duration_ms}") # ADDED
        
        # VAD state tracking
        self.vad_internal_buffer = bytearray()  # Buffer for incoming audio frames
        # self.vad_speech_buffer = bytearray()    # REMOVED - Not buffering full utterance anymore
        self.vad_triggered = False              # Whether we're currently collecting speech
        self.vad_silence_frames = 0             # Count of consecutive silence frames
        self.vad_frames_in_silence_timeout = max(1, self.vad_silence_timeout_ms // self.vad_frame_duration_ms)
        
        # Recent responses for echo detection
        self.recent_responses = []
        self.min_content_length = 4  # Minimum meaningful speech length (characters)

        # Call information placeholders
        self.stream_sid = None
        self.call_sid = None
        self.caller_phone = None

        # Add a version identifier log message
        logger.info(">>> ChineseAudioHandler vAsyncClient_20250508_1143 initialized <<<") 
        logger.info(f"Chinese audio handler initialized (using OpenAI TTS, Google Cloud Speech-to-Text)") # Update log message

    async def send_initial_greeting(self):
        """Synthesizes and sends a predefined initial greeting after WebSocket connection."""
        if not self.tts_client:
            logger.error("Google TTS client not initialized. Cannot send initial greeting.")
            return

        greeting_text = "现在您已连接，我来帮您点餐。"
        logger.info(f"Sending initial agent greeting: '{greeting_text}'")

        try:
            self.is_speaking = True # Mark that we are about to speak

            synthesis_input = SynthesisInput(text=greeting_text)
            voice_params = VoiceSelectionParams(
                language_code="cmn-CN",
                name="cmn-CN-Wavenet-A" # Using a standard WaveNet voice
            )
            audio_config = AudioConfig(
                audio_encoding=AudioEncoding.MULAW,
                sample_rate_hertz=8000
            )

            tts_response = await self.tts_client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config
            )
            audio_bytes = tts_response.audio_content

            if not audio_bytes:
                logger.error("TTS returned empty audio for initial greeting.")
                self.is_speaking = False
                return

            media_payload = base64.b64encode(audio_bytes).decode('utf-8')
            
            if not self.stream_sid:
                logger.error("stream_sid is not set. Cannot send initial greeting audio.")
                self.is_speaking = False
                return

            media_message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": media_payload,
                    "track": "outbound"
                }
            }
            await self.websocket.send_text(json.dumps(media_message))
            logger.info(f"Successfully sent initial greeting audio for stream {self.stream_sid}")

            mark_message = {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {
                    "name": "end_of_initial_greeting" # Specific mark name
                }
            }
            await self.websocket.send_text(json.dumps(mark_message))
            logger.info(f"Sent 'end_of_initial_greeting' mark for stream {self.stream_sid}")

        except Exception as e:
            logger.error(f"Error sending initial greeting: {e}", exc_info=True)
        finally:
            self.is_speaking = False # Reset speaking flag
            self.last_speech_end_time = time.time() # Update last speech time to manage echo

    def _init_google_stt_config(self):
        """Initializes the Google STT streaming configuration."""
        if not self.speech_client:
            logger.error("Cannot initialize Google STT config: SpeechClient is not available.")
            return

        # Create speech context with Chinese restaurant-related phrases
        # This helps improve recognition accuracy for domain-specific terms
        speech_contexts = [speech.SpeechContext(
            phrases=[
                "餐厅", "菜单", "点餐", "预订", "订位", "服务员", 
                "结账", "外卖", "套餐", "饮料", "主食", "甜点",
                "辣的", "不辣", "素食", "推荐", "特色菜", "食物过敏"
            ]
        )]

        self.streaming_config = speech.StreamingRecognitionConfig(
            config=speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
                sample_rate_hertz=8000,
                language_code="cmn-Hans-CN",  # Mandarin Chinese (Simplified)
                enable_automatic_punctuation=True,
                model="telephony",  # Use telephony model which is optimized for phone calls
                speech_contexts=speech_contexts,
                use_enhanced=True
            ),
            interim_results=False,  # We only want final results
        )
        logger.info(f"Google STT StreamingRecognitionConfig initialized for language: cmn-Hans-CN with telephony model and enhanced performance")

    async def _start_google_stt_stream(self):
        """Starts the Google STT stream and the task to process its responses."""
        if not self.speech_client:
            logger.error("Google Speech client not initialized. Cannot start STT stream.")
            return
        
        if not self.streaming_config:
            logger.error("Google STT streaming_config is not initialized. Cannot start STT stream.")
            return

        if self.google_stt_stream is not None or self._google_stt_task is not None:
            logger.warning("Google STT stream or task already exists. Not starting a new one.")
            return

        try:
            logger.info("Starting Google STT async stream...")

            # Define an async generator for requests
            async def request_generator():
                logger.info("Async request generator started.")
                # Send configuration first.
                logger.info("Yielding config from async generator.")
                yield speech.StreamingRecognizeRequest(streaming_config=self.streaming_config)
                
                # Now yield audio chunks from the async queue
                while True:
                    chunk = await self._google_audio_queue.get()
                    if chunk == b'' or chunk is None: # Use None or b'' as sentinel
                        logger.info("Async generator received sentinel, stopping.")
                        self._google_audio_queue.task_done()
                        break
                    
                    # Send the audio chunk.
                    # logger.debug(f"Async generator yielding audio chunk: {len(chunk)} bytes") # DEBUG
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)
                    self._google_audio_queue.task_done()
                logger.info("Async request generator finished.")

            # Call the async streaming_recognize method
            self.google_stt_stream = await self.speech_client.streaming_recognize(
                requests=request_generator()
            )

            # No need for separate transfer task, stop flag, sync queue, or executor
            
            # Create the task to process responses from the async stream
            self._google_stt_task = asyncio.create_task(self._process_google_stt_responses())
            logger.info("Google STT async stream started and response processing task created.")
            
        except Exception as e:
            logger.error(f"Failed to start Google STT async stream: {e}", exc_info=True)
            if self.google_stt_stream:
                try:
                    await self.google_stt_stream.aclose()
                except Exception as close_err:
                    logger.error(f"Error closing STT stream during start failure: {close_err}")
                self.google_stt_stream = None
            self._google_stt_task = None

    async def _queue_speech_for_google_stt(self, audio_bytes, is_final=False):
        """Queue speech audio for Google STT processing.
        
        Args:
            audio_bytes: The audio bytes (a single frame or finalization signal b'') to queue.
            is_final: Whether this is the finalization signal for an utterance.
        """
        # If it's not the final signal, ensure we have audio bytes
        if not is_final and not audio_bytes:
            logger.warning("Attempted to queue empty audio data (and not final signal)")
            return

        # Ensure Google client is available before proceeding
        if not self.speech_client:
            logger.error("Google Speech client not initialized. Cannot queue audio for STT.")
            return
        
        try:
            # If the stream is active, queue the audio/signal
            if self._stt_stream_started:
                await self._google_audio_queue.put(audio_bytes)
                if is_final:
                    logger.info("Queued finalization signal (empty chunk) for current utterance.")
                elif self.verbose_logging:
                    logger.debug(f"Queued {len(audio_bytes)} audio bytes for STT. Queue size: {self._google_audio_queue.qsize()}")
            else:
                # Should not happen with new logic where stream starts on first speech frame,
                # but log a warning if we try to queue data before starting.
                logger.warning(f"Attempted to queue audio/signal when STT stream not started. is_final={is_final}")

        except Exception as e:
            logger.error(f"Error queuing audio for Google STT: {e}", exc_info=True)

    async def _process_google_stt_responses(self):
        """Process responses from the Google Cloud Speech-to-Text stream."""
        if not self.google_stt_stream:
            logger.error("Google STT stream is not available for processing responses.")
            return

        logger.info("Starting to process Google STT responses using async iterator...")
        try:
            # Asynchronously iterate over the responses from the stream
            async for response in self.google_stt_stream:
                logger.info("Received response from Google STT async stream")
                
                # Check if we should stop processing (e.g., call ended)
                if hasattr(self, 'stop_event_handled') and self.stop_event_handled:
                    logger.info("Stop event handled, breaking from STT response processing.")
                    break

                # Process the response
                if not response.results:
                    logger.info("Received Google STT response with no results.")
                    continue
                
                had_final_results = False
                # Log all results for debugging
                for i, result in enumerate(response.results):
                    if not result.alternatives:
                        continue
                    
                    if result.is_final:
                        had_final_results = True
                        transcript = result.alternatives[0].transcript.strip()
                        confidence = result.alternatives[0].confidence
                        logger.info(f"Google STT Final Transcript #{i}: '{transcript}' (Confidence: {confidence:.2f})")
                        
                        if transcript and not (hasattr(self, 'stop_event_handled') and self.stop_event_handled):
                            # Process this transcript using the existing method for handling NLU and response generation
                            # Ensure process_transcribed_text is awaited if it's async
                            if asyncio.iscoroutinefunction(self.process_transcribed_text):
                                await self.process_transcribed_text(transcript)
                            else:
                                self.process_transcribed_text(transcript) # Assuming it might be sync
                                
                    elif self.verbose_logging:
                        # Log interim results if they exist and verbose logging is enabled
                        transcript = result.alternatives[0].transcript.strip()
                        logger.debug(f"Google STT Interim Transcript #{i}: '{transcript}'")
                
                # If we received final results, mark that this utterance's speech is processed
                if had_final_results:
                    logger.info("Received final transcripts for the utterance.")
                    self._current_utterance_has_speech = False # Ready for next VAD trigger
                    # Keep stream alive for next utterance

            # If the loop finishes without error, it means the stream ended gracefully.
            logger.info("Google STT async stream iteration finished gracefully.")
            self._stt_stream_started = False # Mark stream as stopped
            self.google_stt_stream = None
            self._google_stt_task = None
            self._current_utterance_has_speech = False

        except asyncio.CancelledError:
            logger.info("Google STT response processing task was cancelled.")
        except Exception as e:
            # This could catch gRPC errors if the stream breaks unexpectedly.
            logger.error(f"Error processing Google STT async responses: {e}", exc_info=True)
            # Reset stream state on error
            self._stt_stream_started = False 
            self.google_stt_stream = None
            self._google_stt_task = None
            self._current_utterance_has_speech = False
        finally:
            logger.info("Google STT async response processing finished.")

    async def _cleanup_google_stt_resources(self):
        """Cleans up Google STT related resources like the audio queue and the response processing task."""
        logger.info("Attempting to clean up Google STT async resources...")

        # 1. Signal the async audio generator to stop by putting None in the queue
        #    This allows the generator loop in _start_google_stt_stream to exit gracefully.
        if self._google_audio_queue is not None:
            try:
                if self.verbose_logging:
                    logger.debug("Putting None sentinel into Google STT audio queue for cleanup.")
                # Use wait_for to prevent blocking indefinitely if queue is full or put fails
                await asyncio.wait_for(self._google_audio_queue.put(None), timeout=0.5)
            except asyncio.TimeoutError:
                logger.warning("Timeout putting None sentinel into audio queue during cleanup.")
            except asyncio.QueueFull:
                 logger.warning("Audio queue full during cleanup, could not put None sentinel.")
            except Exception as e: 
                logger.error(f"Error putting None into Google STT audio queue during cleanup: {e}", exc_info=True)
        
        # 2. Cancel and await the STT response processing task
        #    This task reads from the google_stt_stream iterator.
        if self._google_stt_task is not None and not self._google_stt_task.done():
            logger.info("Cancelling Google STT response processing task...")
            self._google_stt_task.cancel()
            try:
                await self._google_stt_task
            except asyncio.CancelledError:
                logger.info("Google STT response processing task successfully cancelled.")
            except Exception as e:
                # Log error but continue cleanup
                logger.error(f"Error awaiting cancelled Google STT response task: {e}", exc_info=True)
        self._google_stt_task = None # Ensure task reference is cleared

        # 3. Reset stream state variables
        # The underlying gRPC stream associated with the async iterator 
        # self.google_stt_stream should be managed by the library/garbage collection
        # once the iterator is no longer referenced or awaited.
        self.google_stt_stream = None 
        self._stt_stream_started = False
        self._current_utterance_has_speech = False # Reset VAD flag too

        logger.info("Google STT async resources cleanup process finished.")

    async def _execute_order_summary_tool(self, args: dict) -> str:
        """
        Executes the order_summary tool: saves order details and sends an SMS confirmation if the order is 'DONE'.
        Does NOT interact with Square.
        Returns a JSON string with the outcome.
        """
        logger.info(f"Executing simplified 'order_summary' tool with arguments: {args}")
        tool_result_data = {"status": "ERROR", "message_cn": "处理订单总结时发生未知错误。(Unknown error processing order summary.)"}

        items = args.get("items", [])
        total_price = args.get("total_price")
        summary_status = args.get("summary")

        if not items or total_price is None or summary_status is None:
            logger.error("Missing items, total_price, or summary in order_summary tool arguments.")
            tool_result_data["message_cn"] = "订单信息不完整（缺少商品、总价或状态）。(Incomplete order information: missing items, total price, or status.)"
            return json.dumps(tool_result_data, ensure_ascii=False)

        try:
            is_complete_order = summary_status == "DONE"
            
            if not self.call_sid:
                logger.error("call_sid is not set. Cannot save order details.")
                tool_result_data["message_cn"] = "通话ID丢失，无法保存订单。(Call ID missing, cannot save order.)"
                return json.dumps(tool_result_data, ensure_ascii=False)

            # Save order details to database
            internal_order_id = await save_order_details(self.call_sid, items, total_price, is_complete_order)
            logger.info(f"Order details saved with internal_order_id: {internal_order_id} for call {self.call_sid}")
            
            tool_result_data = {
                "status": "SUCCESS", # Default to success after saving
                "internal_order_id": internal_order_id,
                "summary_status_received": summary_status,
                "items_processed_count": len(items),
                "total_price_processed": total_price,
                "message_cn": f"订单（ID: {internal_order_id}）已记录，状态：{summary_status}。(Order (ID: {internal_order_id}) recorded, status: {summary_status}.)"
            }

            if is_complete_order:
                logger.info(f"Order {internal_order_id} is complete. Attempting to send SMS.")
                
                # Use self.caller_phone which is now passed during __init__
                if self.caller_phone:
                    items_text_cn = ", ".join([f"{i['quantity']}份 {i['name']}" for i in items])
                    sms_body = f"您的食为天订单 ({internal_order_id}) 已确认！菜品: {items_text_cn}。总价: ¥{total_price:.2f}。请稍后取餐。"
                    try:
                        # Ensure functools is imported at the top of the file
                        loop = asyncio.get_running_loop()
                        sms_task = functools.partial(send_sms, self.caller_phone, sms_body) # send_sms from app.utils.twilio
                        loop.run_in_executor(None, sms_task)
                        logger.info(f"Scheduled SMS confirmation for {self.caller_phone} for order {internal_order_id}")
                        tool_result_data["sms_notification_sent_to"] = self.caller_phone
                        tool_result_data["message_cn"] += " 短信确认已发送。(SMS confirmation sent.)"
                    except Exception as sms_err:
                        logger.error(f"Failed to schedule SMS for order {internal_order_id}: {sms_err}", exc_info=True)
                        tool_result_data["sms_notification_error"] = str(sms_err)
                        tool_result_data["message_cn"] += " 短信确认发送失败。(SMS confirmation failed to send.)"
                else:
                    logger.warning(f"Cannot send SMS for order {internal_order_id}, caller phone (self.caller_phone) is not set for call {self.call_sid}")
                    tool_result_data["sms_notification_sent_to"] = None
                    tool_result_data["message_cn"] += " 未发送短信确认（缺少电话号码）。(SMS confirmation not sent (missing phone number).)"
            
        except Exception as e:
            logger.error(f"Error in _execute_order_summary_tool: {e}", exc_info=True)
            tool_result_data["status"] = "TOOL_EXECUTION_ERROR"
            tool_result_data["message_cn"] = f"执行订单总结工具时发生内部错误。(Internal error executing order summary tool.)"
            tool_result_data["error_details"] = str(e)

        return json.dumps(tool_result_data, ensure_ascii=False)

    async def process_transcribed_text(self, transcript: str):
        """Processes the transcribed text, gets a response from OpenAI (potentially using tools), and sends it back."""
        try:
            logger.info(f"Processing transcribed text: '{transcript}'")
            if not transcript.strip():
                logger.info("Empty transcript, skipping NLU.")
                return

            self.conversation_history.append({"role": "user", "content": transcript})

            logger.info("Sending transcript to OpenAI for response (with tools)...")
            chat_response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=self.conversation_history,
                tools=[order_summary_tool_schema_cn], # Pass the defined tool
                tool_choice="auto", # Let OpenAI decide
                temperature=0.7,
            )
            
            response_message = chat_response.choices[0].message
            assistant_response_text = None

            if response_message.tool_calls:
                logger.info(f"OpenAI requested tool calls: {response_message.tool_calls}")
                # Important: Add the assistant message with tool_calls to history
                self.conversation_history.append(response_message)

                tool_messages_for_openai = []
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    function_args_str = tool_call.function.arguments
                    logger.info(f"Tool call: {function_name}, Args: {function_args_str}")
                    
                    function_response_content = ""
                    try:
                        function_args = json.loads(function_args_str)
                        if function_name == "order_summary":
                            function_response_content = await self._execute_order_summary_tool(function_args)
                        else:
                            logger.warning(f"Unknown function requested by OpenAI: {function_name}")
                            function_response_content = json.dumps({"error": f"Unknown function: {function_name}"})
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse function arguments for {function_name}: {function_args_str}")
                        function_response_content = json.dumps({"error": "Invalid arguments format"})
                    except Exception as e:
                        logger.error(f"Error executing tool {function_name}: {e}", exc_info=True)
                        function_response_content = json.dumps({"error": f"Error executing function {function_name}"})

                    tool_messages_for_openai.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": function_response_content,
                    })
                
                # Add all tool responses to history
                for tm in tool_messages_for_openai:
                    self.conversation_history.append(tm)

                logger.info("Sending tool call results back to OpenAI...")
                second_chat_response = await self.openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=self.conversation_history,
                    temperature=0.7,
                )
                assistant_response_text = second_chat_response.choices[0].message.content
                # Add this final assistant response to history as well
                self.conversation_history.append(second_chat_response.choices[0].message)

            else:
                # No tool calls, it's a direct text response
                assistant_response_text = response_message.content
                # Add the direct assistant response to history
                self.conversation_history.append(response_message)
            
            if not assistant_response_text:
                logger.warning("OpenAI returned an empty final response.")
                return
                
            logger.info(f"Final OpenAI response to synthesize: '{assistant_response_text}'")
            
            self.last_spoken_text = assistant_response_text 
            self.is_speaking = True

            if not self.tts_client:
                logger.error("Google TTS client not initialized. Cannot synthesize speech.")
                self.is_speaking = False # Reset speaking flag
                return
                
            logger.info("Synthesizing response to audio using Google Cloud TTS...")
            synthesis_input = SynthesisInput(text=assistant_response_text)
            voice_params = VoiceSelectionParams(
                language_code="cmn-CN",  # Mandarin Chinese
                name="cmn-CN-Wavenet-A"   # Example Wavenet voice (male), can be changed
            )
            audio_config = AudioConfig(
                audio_encoding=AudioEncoding.MULAW,
                sample_rate_hertz=8000
            )

            audio_bytes = None
            try:
                logger.info("Attempting to call Google TTS synthesize_speech...")
                tts_response = await self.tts_client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice_params,
                    audio_config=audio_config
                )
                logger.info("Google TTS synthesize_speech call completed.")
                audio_bytes = tts_response.audio_content
                logger.info(f"Received {len(audio_bytes) if audio_bytes else 'no'} audio bytes from TTS.")
            except Exception as e:
                logger.error(f"Google Cloud TTS synthesis failed: {e}", exc_info=True)
                self.is_speaking = False # Reset speaking flag
                return

            if not audio_bytes:
                logger.error("Google Cloud TTS returned empty or no audio bytes.")
                self.is_speaking = False # Reset speaking flag
                return

            # Send the audio back over the WebSocket
            media_payload = base64.b64encode(audio_bytes).decode('utf-8')
            
            if not self.stream_sid:
                logger.error("stream_sid is not set. Cannot send TTS audio.")
                self.is_speaking = False # Reset speaking flag
                return

            media_message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": media_payload,
                    "track": "outbound" 
                }
            }
            
            try:
                logger.info(f"Attempting to send TTS audio media_message for stream {self.stream_sid}...")
                await self.websocket.send_text(json.dumps(media_message))
                logger.info(f"Successfully sent TTS audio media_message for stream {self.stream_sid}")
            except Exception as e:
                logger.error(f"Failed to send TTS audio media_message: {e}", exc_info=True)
                self.is_speaking = False # Reset speaking flag
                return

            # Send a mark message to indicate the end of bot's speech.
            mark_message = {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {
                    "name": "end_of_bot_speech" 
                }
            }
            await self.websocket.send_text(json.dumps(mark_message))
            logger.info(f"Sent 'end_of_bot_speech' mark for stream {self.stream_sid}")

        except openai.APIError as e:
            logger.error(f"OpenAI API error in process_transcribed_text: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error in process_transcribed_text: {e}", exc_info=True)
        finally:
            self.is_speaking = False # Ensure this is reset
            self.last_speech_end_time = time.time()

    async def _handle_start_event(self, data):
        """Handle the start event from Twilio."""
        try:
            # Extract stream and call information
            self.stream_sid = data.get("streamSid")
            start_data = data.get("start", {})
            self.call_sid = start_data.get("callSid")
            
            logger.info(f"Media stream started: StreamSid={self.stream_sid}, CallSid={self.call_sid}")
            self.call_connected = True
            self.is_ready = True
            self.stop_event_handled = False
            
            # Check if Google Speech client is initialized
            if not self.speech_client:
                logger.error("Google Speech client not initialized. Cannot process audio.")
                return
            
            # Reset stream status when a new call starts
            self._stt_stream_started = False
            self._current_utterance_has_speech = False
            
            logger.info("Handler ready for media.")

            # Send initial greeting if configured and not already sent
            if self.send_welcome_message and not self.welcome_sent:
                logger.info("Attempting to send initial agent greeting after WebSocket connection.")
                await self.send_initial_greeting()
                self.welcome_sent = True
            
        except Exception as e:
            logger.error(f"Error handling start event: {e}", exc_info=True)

    async def cleanup(self):
        """Public cleanup method called by websocket handler on disconnect.
        Ensures all resources are properly released."""
        logger.info("Cleanup method called for ChineseAudioHandler")
        
        # Clean up Google STT resources
        await self._cleanup_google_stt_resources()
        
        # Clean up Google TTS client - Google gRPC clients often don't need explicit async close
        # if self.tts_client:
        #     try:
        #         logger.info("Closing Google TextToSpeechAsyncClient (TTS)...")
        #         # await self.tts_client.aclose() # This method doesn't exist
        #         logger.info("Google TextToSpeechAsyncClient (TTS) closed (or left for garbage collection).")
        #     except Exception as e:
        #         logger.error(f"Error closing Google TTS client: {e}", exc_info=True)
        
        # If there are any additional resources to clean up that aren't handled
        # by _cleanup_google_stt_resources, do it here
        
        # Call parent cleanup if it exists
        if hasattr(super(), 'cleanup'):
            await super().cleanup()

    async def process_twilio_message(self, message):
        """Process a message from Twilio WebSocket."""
        try:
            # Extract event type
            message_data = message.get("data", {}) if isinstance(message, dict) else {}
            
            if not isinstance(message, dict):
                try:
                    message_data = json.loads(message) # Assuming message might be a JSON string
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode message as JSON: {message[:100]}...")
                    return
            
            event = message_data.get("event")
            
            # Always log the event type for monitoring audio flow
            logger.debug(f"Twilio event: {event}") # Changed to DEBUG
            
            if not event:
                logger.warning(f"No event in message data: {message_data}")
                return
                
            if event == "start":
                await self._handle_start_event(message_data)
            
            elif event == "media":
                # Extract media data - always log count and sample size
                media = message_data.get("media", {})
                payload = media.get("payload")
                track = media.get("track")
                
                if track != "inbound":
                    return  # Only process inbound audio
                
                if not payload:
                    logger.warning("Received empty media payload")
                    return
                    
                # Always log audio payload statistics
                static_count = getattr(self, '_media_packet_count', 0) + 1
                setattr(self, '_media_packet_count', static_count)
                
                # Log every 20 packets or first 5 packets
                if static_count <= 5 or static_count % 20 == 0:
                    logger.debug(f"Media packet #{static_count}: {len(payload)} chars payload") # Changed to DEBUG
                
                try:
                    # Decode base64 payload
                    audio_chunk = base64.b64decode(payload)
                    if static_count <= 5 or static_count % 20 == 0:
                        logger.debug(f"Decoded audio chunk: {len(audio_chunk)} bytes") # Changed to DEBUG
                    
                    # Check if we're avoiding echo of our own speech
                    current_time = time.time()
                    time_since_last_speech = current_time - self.last_speech_end_time
                    if self.is_speaking or time_since_last_speech < self.echo_cancellation_timeout:
                        return  # Skip processing audio during/shortly after we're speaking
                    
                    # Process the audio frame
                    await self.process_audio_frame(audio_chunk)
                
                except Exception as e:
                    logger.error(f"Error processing media event: {e}")
                    logger.error(traceback.format_exc())
            
            elif event == "mark":
                # Handle mark event (e.g., end of bot speech)
                await self._handle_mark_event(message_data) # Call the new method
            
            elif event == "stop":
                await self._handle_stop_event(message_data)

            else:
                logger.warning(f"Received unknown event type: {event} or malformed message: {message_data}")

        except WebSocketDisconnect:
            logger.warning(f"WebSocket disconnected unexpectedly for call {self.call_sid}")
            await self.cleanup() # Ensure cleanup is called
        except Exception as e:
            logger.error(f"Error processing Twilio message: {e}")
            logger.error(traceback.format_exc())

    async def process_audio_frame(self, audio_frame):
        """Process an incoming audio frame with VAD and queue for Google STT if speech is detected."""
        logger.debug("process_audio_frame called.") # New Log
        if not self.is_ready or self.stop_event_handled:
            logger.debug(f"process_audio_frame returning early: is_ready={self.is_ready}, stop_event_handled={self.stop_event_handled}") # New Log
            return
        
        # Convert mulaw to PCM for VAD
        try:
            pcm_audio = audioop.ulaw2lin(audio_frame, 2)  # 2 bytes per sample (16-bit)
        except Exception as e:
            logger.error(f"Error converting mulaw to PCM: {e}")
            return
        
        # Check with VAD if this frame contains speech
        is_speech = False # Default to false
        try:
            is_speech = self.vad.is_speech(pcm_audio, self.vad_sample_rate)
            logger.debug(f"VAD is_speech result: {is_speech}") # New Log
        except Exception as e:
            logger.error(f"VAD error: {e}")
            return
        
        # Process audio frame based on VAD result
        if is_speech:
            logger.debug("VAD: Frame IS speech.") # New Log
            self._current_utterance_has_speech = True # Mark that we've had speech in this segment
            self.vad_silence_frames = 0 # Reset silence counter
            if not self.vad_triggered:
                logger.info("VAD: Speech started (vad_triggered is now True)") # Modified Log
                self.vad_triggered = True
                # Start the STT stream ONLY if it's not already started
                if not self._stt_stream_started:
                    logger.info("First speech frame detected, attempting to start STT stream...") # Modified Log
                    await self._start_google_stt_stream()
                    # Check if stream started successfully before marking it and queueing
                    if self.google_stt_stream: 
                        self._stt_stream_started = True
                        logger.info("STT stream successfully started. Queuing first frame.") # Modified Log
                        await self._queue_speech_for_google_stt(audio_frame)
                    else:
                        logger.error("STT stream failed to start on first speech frame. Resetting VAD trigger.") # Modified Log
                        self.vad_triggered = False 
                        return # Stop processing this frame if stream failed
                else:
                    # Stream already started, just queue this frame
                    logger.debug("STT stream already started. Queuing speech frame.") # New Log
                    await self._queue_speech_for_google_stt(audio_frame)
            else:
                # VAD already triggered, stream should be started, just queue the frame
                # Ensure stream is actually started before queueing subsequent frames
                if self._stt_stream_started:
                    logger.debug("VAD triggered and STT stream started. Queuing speech frame.") # New Log
                    await self._queue_speech_for_google_stt(audio_frame)
                else:
                    # This case should ideally not be hit if the first frame logic is correct
                    logger.warning("VAD triggered, but STT stream is not marked as started. Ignoring frame.")
        else: # Not speech
            logger.debug("VAD: Frame is NOT speech.") # New Log
            if self.vad_triggered:
                logger.debug("VAD was triggered, now processing silence frame.") # New Log
                # We were previously hearing speech, now it's silent
                self.vad_silence_frames += 1
                logger.debug(f"VAD silence_frames count: {self.vad_silence_frames}/{self.vad_frames_in_silence_timeout}") # New Log
                if self.vad_silence_frames >= self.vad_frames_in_silence_timeout:
                    logger.info(f"VAD: End of utterance detected after {self.vad_silence_frames} silence frames.")
                    # Only send finalization if we actually sent speech for this utterance
                    # and the stream is currently running
                    if self._stt_stream_started and self._current_utterance_has_speech: 
                        logger.info("Sending finalization signal (empty chunk) to STT stream.") # Modified Log
                        await self._queue_speech_for_google_stt(b'', is_final=True)
                    else:
                         logger.info("VAD end detected, but no speech was sent for this utterance or STT stream not started. No finalization signal sent.") # Modified Log
                         
                    # Reset for next utterance
                    logger.debug("Resetting VAD state for next utterance.") # New Log
                    self.vad_triggered = False
                    self.vad_silence_frames = 0
                    self._current_utterance_has_speech = False # Reset speech flag
            # else: # Silence frame, but VAD wasn't triggered (initial silence or between utterances)
                # logger.debug("VAD: Silence frame ignored (pre-speech or between utterances)") # Can be noisy

    async def _handle_mark_event(self, data):
        """Handle the mark event from Twilio."""
        mark_name = data.get("mark", {}).get("name")
        logger.info(f"Received mark event: Name='{mark_name}'")
        # Add any specific logic needed when a mark is received, e.g., confirming end of speech.
        if mark_name == "end_of_bot_speech":
            logger.info("Confirmed end of bot speech mark.")
            # Potentially update state if needed

    async def _handle_stop_event(self, data):
        """Handle the stop event from Twilio."""
        try:
            logger.info(f"Received stop event: {data}")
            self.stop_event_handled = True
            
            # Clean up Google STT resources
            await self._cleanup_google_stt_resources()
            
            logger.info("Call terminated. All resources cleaned up.")
        except Exception as e:
            logger.error(f"Error handling stop event: {e}", exc_info=True)
