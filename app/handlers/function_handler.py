"""
Function Handler - Process function calls from Deepgram
"""
import json
import logging
import asyncio
import os
from typing import Dict, Any, Optional
from fastapi import WebSocket
import time
import traceback
from app.services.database_service import save_utterance, save_order_details
from app.utils.square import test_create_order_endpoint, test_payment_processing
from app.config import settings
from app.utils.twilio import end_call, send_sms

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define a constant for the mark name
FINAL_AUDIO_MARK_NAME = "final_message_played"

async def handle_function_call(
    function_request: Dict[str, Any],
    deepgram_service,
    websocket: WebSocket,
    stream_sid: str,
    caller_phone: Optional[str],
    call_sid: Optional[str]
):
    """
    Handle function calls from Deepgram
    
    Args:
        function_request: The function request from Deepgram
        deepgram_service: The Deepgram service instance
        websocket: WebSocket connection to Twilio
        stream_sid: The Twilio stream SID
        caller_phone: The caller's phone number
        call_sid: The Twilio call SID
    """
    try:
        # Extract function call details
        function_name = function_request.get("function_name", "")
        function_call_id = function_request.get("function_call_id", "")
        input_data = function_request.get("input", {})
        
        logger.info(f"Function call from Deepgram: {function_name}")
        logger.info(f"Function call ID: {function_call_id}")
        logger.info(f"Function input: {json.dumps(input_data)}")
        
        # Handle different function calls
        if function_name == "order_summary":
            await handle_order_summary(
                function_call_id,
                input_data,
                deepgram_service,
                websocket,
                stream_sid,
                caller_phone,
                call_sid
            )
        else:
            logger.warning(f"Unknown function call: {function_name}")
            # Send a generic response for unknown functions
            response = {
                "type": "FunctionCallResponse",
                "function_call_id": function_call_id,
                "output": f"The function {function_name} is not implemented."
            }
            logger.info(f"Sending unknown function response for {function_name}: {response}")
            await deepgram_service.send_json(response)
            
    except Exception as e:
        logger.error(f"Error handling function call: {e}")
        # If we have a function_call_id, try to send an error response
        if function_call_id := function_request.get("function_call_id"):
            try:
                error_response = {
                    "type": "FunctionCallResponse",
                    "function_call_id": function_call_id,
                    "output": "Sorry, there was an error processing your request."
                }
                await deepgram_service.send_json(error_response)
                logger.info(f"Sent error response for function call {function_call_id}")
            except Exception as e2:
                logger.error(f"Error sending error response: {e2}")

async def handle_order_summary(
    function_call_id: str,
    input_data: Dict[str, Any],
    deepgram_service,
    websocket: WebSocket,
    stream_sid: str,
    caller_phone: Optional[str],
    call_sid: Optional[str]
):
    """
    Handle order summary function call and send response back to Deepgram
    
    Args:
        function_call_id: The unique ID for this function call
        input_data: The input data for the function
        deepgram_service: The Deepgram service instance
        websocket: WebSocket connection to Twilio
        stream_sid: The Twilio stream SID
        caller_phone: The caller's phone number
        call_sid: The Twilio call SID
    """
    logger.info(f"Handling order_summary function call (ID: {function_call_id})")
    logger.info(f"Input data: {json.dumps(input_data)}")

    # Extract order details
    items = input_data.get("items", [])
    total_price = input_data.get("total_price")
    status = input_data.get("status")

    if not items or total_price is None:
        logger.error("Missing items or total_price in order_summary input")
        # Optionally send an error response back to Deepgram
        error_response = {
            "type": "FunctionCallResponse",
            "function_call_id": function_call_id,
            "output": {"status": "error", "message": "Missing order details"}
        }
        await deepgram_service.send_json(error_response)
        return

    try:
        # Determine order status
        summary_status = input_data.get("summary", "IN PROGRESS")
        logger.info(f"DEBUG: Raw summary status from input: '{summary_status}' (Type: {type(summary_status)})")
        is_complete_order = summary_status == "DONE"
        logger.info(f"DEBUG: Calculated is_complete_order based on summary: {is_complete_order}")

        # Generate confirmation message text
        confirmation_text = f"Okay, I have {len(items)} items for a total of ${total_price:.2f}."
        if is_complete_order:
            confirmation_text += " Your order will be ready for pickup shortly."
        else:
            confirmation_text += " Is there anything else?"

        logger.info(f"Generated confirmation text: {confirmation_text}")

        # --- Save order and utterances ---
        logger.info(f"DEBUG: Saving order with is_complete_order = {is_complete_order}")
        order_id = await save_order_details(call_sid, items, total_price, is_complete_order)
        await save_utterance(call_sid, "assistant", confirmation_text)
        logger.info(f"Saved order {order_id} for call {call_sid}")
        
        # --- Handle Call Completion with Mark Event ---
        logger.info(f"DEBUG: Checking if is_complete_order is True: {is_complete_order}")
        if is_complete_order:
            logger.info(f"Order is complete for call {call_sid}. Processing Square order and payment.")

            payment_status = "PENDING" # Default status
            square_order_id = None
            square_payment_id = None

            try:
                # ---> USE ORIGINAL SQUARE LOGIC HERE <--- #
                logger.info(f"Creating order in Square with items: {items}")

                # Get test payment method ID for Square sandbox
                test_payment_method_id = settings.SQUARE_TEST_NONCE

                # Place order via Square - Remove idempotency key as it's not expected by the function
                result = await test_create_order_endpoint(items)
                logger.info(f"Square Order API response: {result}")

                # Add defensive checks for result structure
                if result and isinstance(result, dict) and "order" in result:
                    square_order_id = result["order"]["id"]
                    # Ensure amount is integer (cents)
                    current_order_total = result["order"].get("total_money", {}).get("amount")

                    logger.info(f"Square order created successfully! Order ID: {square_order_id}, Total: {current_order_total}")

                    if square_order_id and current_order_total is not None:
                        # Process payment via Square
                        logger.info(f"Processing Square payment for order {square_order_id}, amount: {current_order_total}")
                        payment_result = await test_payment_processing(
                            square_order_id,
                            current_order_total,
                            test_payment_method_id
                        )
                        logger.info(f"Square Payment result: {payment_result}")

                        if payment_result and isinstance(payment_result, dict):
                            # Check common Square payment statuses
                            if payment_result.get("status") == "COMPLETED":
                                square_payment_id = payment_result.get("id")
                                payment_status = "PAID"
                                logger.info(f"Square payment successful! Payment ID: {square_payment_id}")
                            elif payment_result.get("status") == "FAILED":
                                payment_status = "FAILED"
                                logger.error(f"Square payment failed! Result: {payment_result}")
                            else:
                                payment_status = payment_result.get("status", "UNKNOWN_STATUS") # Capture other statuses
                                logger.warning(f"Square payment status: {payment_status}. Result: {payment_result}")
                        else:
                            payment_status = "FAILED"
                            logger.error("Square payment processing failed or returned unexpected result.")
                    else:
                        payment_status = "FAILED" # Cannot proceed without order ID or total
                        logger.error(f"Cannot process payment. Missing Square order ID ({square_order_id}) or total amount ({current_order_total}).")
                else:
                    payment_status = "ORDER_FAILED"
                    logger.error(f"Failed to create order in Square or response structure invalid. Result: {result}")

            except Exception as sq_err:
                logger.error(f"Error during Square processing for call {call_sid}: {sq_err}", exc_info=True)
                payment_status = "ERROR"
                # Continue with confirmation even if Square fails

            # TODO: Optionally update the database record with square_order_id and payment_status
            # await update_order_with_square_details(order_id, square_order_id, payment_status)

            # --- Proceed with User Confirmation (TTS/SMS) ---

            # 1. Generate final confirmation text (including pickup message)
            # Use the original confirmation text and add the pickup part
            final_confirmation_text = f"{confirmation_text} Your order will be ready for pickup shortly."
            logger.info(f"Generated final confirmation text: {final_confirmation_text}")

            # Send the final confirmation message text back to Deepgram
            # Deepgram Agent will handle TTS generation and send audio back
            response = {
                "type": "FunctionCallResponse",
                "function_call_id": function_call_id,
                "output": final_confirmation_text
            }
            logger.info(f"Sending function call response to trigger TTS: {json.dumps(response)}")
            await deepgram_service.send_json(response)

            # --- SMS Sending (already handled asynchronously) ---
            if caller_phone:
                # Use a simple SMS format
                items_text = ", ".join([f"{i['quantity']}x {i['name']}" for i in items]) # Recreate items_text if needed
                
                # Determine which Order ID to display
                display_order_id = square_order_id if square_order_id else order_id
                
                # Use the display_order_id in the SMS body
                sms_body = f"Your Servio order ({display_order_id}) is confirmed! Items: {items_text}. Total: ${total_price:.2f}. It will be ready shortly. Status: {payment_status}"
                
                # Get the current event loop
                loop = asyncio.get_running_loop()
                
                # Schedule the synchronous send_sms function in the default executor
                # Note: We don't await the result here, just schedule it (fire-and-forget)
                # loop.run_in_executor(None, send_sms, caller_phone, sms_body)
                # Use functools.partial to pass arguments correctly to the executor
                import functools
                sms_task = functools.partial(send_sms, caller_phone, sms_body)
                loop.run_in_executor(None, sms_task)

                logger.info(f"Scheduled SMS confirmation via executor for {caller_phone} (Square Status: {payment_status})")
            else:
                logger.warning(f"Cannot send SMS confirmation, caller phone is missing for call {call_sid}")

        # --- Handle Non-Complete Order ---
        else:
            logger.info(f"DEBUG: Entered ELSE block for non-complete order (is_complete_order={is_complete_order})")
            # If order is not complete, TTS was already sent by handle_transcript
            logger.info(f"Order not complete for call {call_sid}. TTS already sent by handle_transcript.") # Updated log message
            # response_payload = {
            #     "type": "FunctionCallResponse",
            #     "function_call_id": function_call_id,
            #     "output": {"status": "processed_intermediately"}, # Send simple status, not full text
            # }
            # await deepgram_service.send_json(response_payload)
            # logger.info(f"Sent FunctionCallResponse status to Deepgram for call {call_sid}")

    except ValueError as ve:
        logger.error(f"Value error processing order summary: {ve}")
        await deepgram_service.send_json({
            "type": "FunctionCallResponse", 
            "function_call_id": function_call_id, 
            "output": {"status": "error", "message": str(ve)}
        })
    except Exception as e:
        logger.error(f"Error processing order summary: {e}")
        logger.error(traceback.format_exc())
        # Send generic error response to Deepgram
        await deepgram_service.send_json({
            "type": "FunctionCallResponse", 
            "function_call_id": function_call_id, 
            "output": {"status": "error", "message": "Internal server error"}
        })

async def play_audio_with_mark(twilio_websocket: WebSocket, stream_sid: str, audio_bytes: bytes, sample_width: int, mark_name: Optional[str] = None):
    """Send audio bytes (as µ-law) and an optional mark event to Twilio."""
    if not twilio_websocket or not audio_bytes:
        logger.error("Cannot play audio: missing websocket or audio data.")
        return

    try:
        # 1. Convert PCM to µ-law
        ulaw_bytes = pcm_to_ulaw(audio_bytes, sample_width)
        
        # 2. Encode µ-law to base64
        ulaw_b64 = bytes_to_base64(ulaw_bytes)
        
        # 3. Send media event
        media_message = {
            "event": "media",
            "streamSid": stream_sid,
            "media": {
                "payload": ulaw_b64,
                "format": { # Explicitly state format
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000, # Twilio requires 8kHz for µ-law
                    "channels": 1
                }
            }
        }
        await twilio_websocket.send_json(media_message)
        logger.info(f"Sent audio media event to Twilio stream {stream_sid} ({len(ulaw_bytes)} µ-law bytes)")

        # 4. Send mark event if requested
        if mark_name:
            mark_message = {
                "event": "mark",
                "streamSid": stream_sid,
                "mark": { "name": mark_name }
            }
            await twilio_websocket.send_json(mark_message)
            logger.info(f"Sent mark event '{mark_name}' to Twilio stream {stream_sid}")
            
    except Exception as e:
        logger.error(f"Error in play_audio_with_mark: {e}")
        logger.error(traceback.format_exc())
