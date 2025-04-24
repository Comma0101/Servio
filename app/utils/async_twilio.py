"""
Async Twilio Utilities - Asynchronous wrappers for Twilio functions
"""
import asyncio
import logging
from typing import Dict, Any, Optional
from app.utils.twilio import send_sms as sync_send_sms

# Configure logging
logger = logging.getLogger(__name__)

async def send_sms(to_number: str, message: str, client_id: str = "LIMF") -> Dict[str, Any]:
    """
    Asynchronous wrapper for the Twilio SMS sending function
    
    Args:
        to_number (str): The phone number to send the SMS to
        message (str): The message to send
        client_id (str, optional): Client identifier for tracking. Defaults to "LIMF".
        
    Returns:
        dict: A dictionary containing the success status and additional information
    """
    try:
        # Use the existing sync function in a thread pool to avoid blocking
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: sync_send_sms(to_number, message, client_id)
        )
        
        logger.info(f"Async SMS sent with result: {result.get('success', False)}")
        return result
    except Exception as e:
        logger.error(f"Error in async SMS sending: {e}")
        return {"success": False, "error": str(e)}

async def schedule_sms(to_number: str, message: str, delay_seconds: int = 0, client_id: str = "LIMF") -> Dict[str, Any]:
    """
    Schedule an SMS to be sent after a specified delay
    
    Args:
        to_number (str): The phone number to send the SMS to
        message (str): The message to send
        delay_seconds (int, optional): Delay in seconds before sending. Defaults to 0.
        client_id (str, optional): Client identifier for tracking. Defaults to "LIMF".
        
    Returns:
        dict: A dictionary containing scheduling status and information
    """
    try:
        if delay_seconds > 0:
            logger.info(f"Scheduling SMS to {to_number} with delay of {delay_seconds} seconds")
            await asyncio.sleep(delay_seconds)
            
        # Send the SMS after delay
        result = await send_sms(to_number, message, client_id)
        return result
    except Exception as e:
        logger.error(f"Error in scheduled SMS: {e}")
        return {"success": False, "error": str(e)}
