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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    # Log that we received this function call
    logger.info(f"Processing order_summary function call")
    logger.info(f"Order function input: {input_data}")
    
    try:
        # Process the order data
        items = input_data.get("items", [])
        total_price = input_data.get("total_price", 0)
        summary_status = input_data.get("summary", "IN PROGRESS")
        
        # Format items for readability
        item_descriptions = []
        for item in items:
            name = item.get("name", "Unknown item")
            quantity = item.get("quantity", 1)
            variation = item.get("variation", "Regular")
            item_descriptions.append(f"{quantity}x {name} ({variation})")
        
        items_text = ", ".join(item_descriptions)
        
        # Create order confirmation message
        confirmation_message = f"Your order of {items_text} for a total of ${total_price:.2f} has been received."
        
        # If the order is marked as done, process with Square and send confirmation
        if summary_status == "DONE":
            # Add the pickup confirmation message
            confirmation_message = f"Your order of {items_text} for a total of ${total_price:.2f} has been received and will be ready for pickup shortly. Thank you for ordering with us."
            
            # Calculate tax if needed
            from app.config import settings
            tax_rate = settings.RESTAURANT_TAX_RATE
            tax_amount = total_price * tax_rate if tax_rate > 0 else 0
            total_with_tax = total_price + tax_amount
            
            # Create order details
            order_details = {
                "items": items,
                "total_price": total_price,
                "tax_amount": tax_amount,
                "total_with_tax": total_with_tax,
                "status": summary_status,
                "order_id": f"ORDER-{int(time.time())}"
            }
            
            # Process with Square if summary status is DONE
            try:
                # Process order via Square API
                logger.info(f"Creating order in Square with items: {items}")
                
                # Import Square utilities
                from app.utils.square import test_create_order_endpoint, test_payment_processing
                from app.config import settings
                
                # Get test payment method ID for Square sandbox
                test_payment_method_id = settings.SQUARE_TEST_NONCE
                
                # Place order via Square
                result = await test_create_order_endpoint(items)
                logger.info(f"Square API response: {result}")
                
                # Add defensive checks for result structure
                if result and isinstance(result, dict) and "order" in result:
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
                        
                        logger.info(f"Payment result: {payment_result}")
                        if payment_result and isinstance(payment_result, dict) and payment_result.get("status") == "COMPLETED":
                            logger.info(f"Payment successful! Payment ID: {payment_result.get('id')}")
                            order_details["payment_status"] = "PAID"
                            order_details["payment_id"] = payment_result.get("id")
                        else:
                            logger.error("Payment failed!")
                            order_details["payment_status"] = "FAILED"
                            order_details["payment_id"] = None
                else:
                    logger.error("Failed to create order in Square")
            except Exception as e:
                logger.error(f"Error processing Square order: {e}")
                logger.error(f"Exception details: {traceback.format_exc()}")
                order_details["payment_status"] = "ERROR"
                order_details["error"] = str(e)
                # Continue with sending confirmation even if Square fails
            
            # Optional: Send SMS confirmation if caller phone is available
            if caller_phone:
                try:
                    # Prepare a detailed order summary for SMS
                    order_summary = "Your order has been confirmed:\n"
                    for item in items:
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
                    
                    from app.utils.twilio import send_sms
                    await send_sms(caller_phone, order_summary)
                    logger.info(f"Sent order confirmation SMS to {caller_phone}")
                except Exception as e:
                    logger.error(f"Error sending order confirmation SMS: {e}")
        
        # Send function call response back to Deepgram
        response = {
            "type": "FunctionCallResponse",
            "function_call_id": function_call_id,
            "output": confirmation_message
        }
        
        logger.info(f"Sending function call response: {json.dumps(response)}")
        await deepgram_service.send_json(response)
        
        # Save the confirmation message to the database
        if call_sid:
            try:
                from app.services.database_service import save_utterance
                await save_utterance(
                    call_sid,
                    "system",
                    confirmation_message
                )
            except Exception as e:
                logger.error(f"Error saving confirmation message to database: {e}")
                
        # For order completion, simply log that we're finished - don't schedule hangup
        if summary_status == "DONE":
            logger.info("Order is complete, allowing agent to speak confirmation")
            logger.info("Letting natural call flow complete without forced hangup")
            
    except Exception as e:
        logger.error(f"Error processing order summary: {e}")
        # Send error response
        error_response = {
            "type": "FunctionCallResponse",
            "function_call_id": function_call_id,
            "output": "Sorry, there was an error processing your order."
        }
        await deepgram_service.send_json(error_response)

async def schedule_hangup(
    deepgram_websocket: WebSocket, 
    twilio_websocket: WebSocket, 
    stream_sid: str, 
    call_sid: Optional[str], 
    delay_seconds: Optional[int] = 5
):
    """
    Schedule a call hangup with a farewell message
    
    Args:
        deepgram_websocket: The Deepgram WebSocket connection
        twilio_websocket: The Twilio WebSocket connection
        stream_sid: The Twilio stream SID
        call_sid: The Twilio call SID
        delay_seconds: Delay in seconds before sending the hangup signal
    """
    # Log that we're scheduling a hangup
    logger.info(f"Scheduling call hangup with {delay_seconds} second delay")
    
    # Wait for the specified delay
    await asyncio.sleep(delay_seconds)
    
    logger.info("Proceeding with call hangup sequence")
    
    # Send hangup signal
    logger.info("Hanging up the call")
    
    try:
        # Skip Deepgram EndSession since the connection is likely already closed by this point
        # and attempting to send messages results in unnecessary errors
        
        # Send the stop signal to Twilio to properly end the media stream
        # This will trigger the AudioHandler._handle_stop_event which does the S3 upload
        if twilio_websocket and stream_sid:
            try:
                # Create a stop event message for Twilio
                stop_message = {
                    "event": "stop",
                    "streamSid": stream_sid
                }
                logger.info(f"Sending stop event to Twilio for stream {stream_sid}")
                await twilio_websocket.send_json(stop_message)
                logger.info("Successfully sent stop event to Twilio")
                
                # Add another small delay after stopping the Twilio stream
                # This ensures the stop event is fully processed before ending the call
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error sending stop event to Twilio: {e}")
        else:
            logger.error(f"Cannot send stop to Twilio, missing websocket or stream_sid: {stream_sid}")
        
        # Third, use the Twilio REST API to properly end the call
        if call_sid:
            try:
                from app.utils.twilio import end_call
                result = end_call(call_sid)
                logger.info(f"Twilio REST API call ending result: {result}")
            except Exception as e:
                logger.error(f"Error using Twilio REST API to end call: {e}")
        else:
            logger.warning("Cannot end call using Twilio REST API: no call_sid provided")
        
    except Exception as e:
        logger.error(f"Error during hangup process: {e}")
        import traceback
        logger.error(f"Hangup error details: {traceback.format_exc()}")
