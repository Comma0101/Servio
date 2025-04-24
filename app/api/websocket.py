"""
WebSocket endpoints for handling real-time audio streams between Twilio and Deepgram
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
import logging
import json
import os
from dotenv import load_dotenv

# Import services and handlers
from app.services.deepgram_service import DeepgramService
from app.handlers.audio_handler import AudioHandler
from app.utils.constants import get_restaurant_config, get_restaurant_menu

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(tags=["websocket"])

# Store for caller information, keyed by call_sid
caller_info = {}

def set_caller_info(call_sid: str, caller_phone: str):
    """
    Store caller phone number for a specific call SID
    
    Args:
        call_sid: The Twilio Call SID
        caller_phone: The caller's phone number
    """
    caller_info[call_sid] = {"phone": caller_phone}
    logger.info(f"Stored caller phone for {call_sid}: {caller_phone}")

def get_caller_phone(call_sid: str) -> str:
    """
    Get the caller's phone number for a specific call SID
    
    Args:
        call_sid: The Twilio Call SID
        
    Returns:
        The caller's phone number or None if not found
    """
    if call_sid in caller_info and "phone" in caller_info[call_sid]:
        return caller_info[call_sid]["phone"]
    return None

@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """WebSocket endpoint for handling media streams from Twilio"""
    # Accept the WebSocket connection
    await websocket.accept()
    logger.info("WebSocket client connected")
    
    # Initialize Deepgram service
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        logger.error("Deepgram API key not found, closing WebSocket")
        await websocket.close(1008, "Configuration error: Deepgram API key missing")
        return
    
    # Get restaurant configuration
    restaurant_id = os.getenv("RESTAURANT_ID", "LIMF")
    restaurant_config = get_restaurant_config(restaurant_id)
    
    # Get the menu data and enhance the system message
    # Get the base system message
    system_message = restaurant_config.get("SYSTEM_MESSAGE", "")
    
    # Get and format the menu
    menu_items = get_restaurant_menu(restaurant_id)
    if menu_items:
        # Build a detailed menu text with correct prices
        menu_text = "\n\nMENU ITEMS:\n"
        for item in menu_items:
            name = item.get("name", "Unknown item")
            variations = item.get("variations", [])
            
            # Handle menu items with variations
            if variations:
                for variation in variations:
                    var_name = variation.get("name", "")
                    var_price = variation.get("price", 0)
                    menu_text += f"{name} ({var_name}): ${var_price}\n"
            else:
                # For items without variations
                price = item.get("price", 0)
                menu_text += f"{name}: ${price}\n"
        
        # Enhance the system message with menu data
        enhanced_system_message = system_message + menu_text
        logger.info(f"Enhanced system message with {len(menu_items)} menu items")
    else:
        enhanced_system_message = system_message
        logger.warning("No menu items found to enhance system message")
    

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
    # Prepare Deepgram configuration
    deepgram_config = {
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
                    "type": "open_ai",  # You can also use OpenAI or other supported models
                },
                "model": "gpt-4o",
                "instructions": enhanced_system_message,
                "functions": [function_def]
            },
            "speak": {"model": "aura-asteria-en"},
        },
    }
    
    # Initialize services
    deepgram_service = DeepgramService(api_key, deepgram_config)
    
    # Initialize the audio handler
    audio_handler = AudioHandler(deepgram_service, websocket)
    
    # Create tasks list to track all created tasks for proper cleanup
    tasks_to_cleanup = []
    
    try:
        # Connect to Deepgram
        await deepgram_service.connect()
        logger.info("Connected to Deepgram")
        
        # Start processing in parallel
        process_twilio_task = asyncio.create_task(
            audio_handler.process_twilio_messages()
        )
        tasks_to_cleanup.append(process_twilio_task)
        
        process_deepgram_task = asyncio.create_task(
            audio_handler.process_deepgram_responses()
        )
        tasks_to_cleanup.append(process_deepgram_task)
        
        # Wait for both tasks to complete
        try:
            done, pending = await asyncio.wait(
                [process_twilio_task, process_deepgram_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # If there was an exception in one task, cancel the others but continue cleanup
            for task in done:
                try:
                    # This will raise any exception that occurred during task execution
                    task.result()
                except asyncio.CancelledError:
                    # This is expected during shutdown, ignore and continue processing
                    logger.debug("Task cancelled normally - continuing call processing")
                    pass
                except Exception as e:
                    logger.error(f"Error in WebSocket task: {e}")
                    # Cancel other tasks but don't raise - this ensures cleanup continues
                    for p in pending:
                        p.cancel()
            
            # Cancel any pending tasks (one finished without error)
            for p in pending:
                p.cancel()
                
        except asyncio.CancelledError:
            # Explicitly catch and ignore at this level to prevent propagation
            logger.info("WebSocket wait cancelled - isolating from shutdown sequence")
            # Do NOT re-raise the exception - this is key to isolating the handler
        
    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket")
    except asyncio.CancelledError:
        # This is expected during shutdown, handle gracefully
        # Explicitly IGNORE the cancellation to prevent it from affecting other connections
        logger.info("WebSocket tasks cancelled - isolated from shutdown sequence")
    except Exception as e:
        logger.error(f"Error in WebSocket handler: {e}")
    finally:
        # Clean up connections
        try:
            # First properly close the deepgram connection with a timeout
            try:
                # Use a shield to prevent the close from being cancelled
                close_task = asyncio.shield(deepgram_service.close())
                # Wait for the close task with a timeout
                await asyncio.wait_for(close_task, timeout=2.0)
                logger.info("Closed connection to Deepgram")
            except asyncio.TimeoutError:
                logger.warning("Timeout while closing Deepgram connection")
            except asyncio.CancelledError:
                # Shield didn't work, but we still want to continue cleanup
                logger.info("Deepgram close operation cancelled - continuing cleanup")
            except Exception as e:
                logger.error(f"Error closing Deepgram connection: {e}")
            
            # Cancel all other tasks gracefully but ensure cleanup completes
            tasks = [t for t in tasks_to_cleanup if not t.done()]
            
            # If no specific tasks to clean up, get related tasks only (not ALL tasks)
            if not tasks:
                # Only include tasks that are related to this handler
                current = asyncio.current_task()
                tasks = [t for t in asyncio.all_tasks() 
                        if t is not current and not t.done() and 
                        getattr(t, 'name', '').startswith(f'call_')] 
            
            # Give tasks a chance to complete
            if tasks:
                for task in tasks:
                    task.cancel()
                
                # Wait for tasks to be cancelled with a timeout - use shield to prevent this from being cancelled
                try:
                    shielded_wait = asyncio.shield(asyncio.wait(tasks, timeout=1.0))
                    await shielded_wait
                    logger.info(f"Gracefully cancelled {len(tasks)} pending tasks")
                except asyncio.TimeoutError:
                    logger.warning("Timeout while waiting for tasks to cancel")
                except asyncio.CancelledError:
                    # Continue cleanup even if this is cancelled
                    logger.info("Task cancellation was cancelled - continuing cleanup")
                except Exception as e:
                    logger.error(f"Error during task cancellation: {e}")
        except asyncio.CancelledError:
            # Explicitly catch and ignore cancellation during cleanup to ensure we finish
            logger.info("Cleanup process was cancelled - continuing through completion")
        except Exception as e:
            logger.error(f"Error during WebSocket cleanup: {e}")
        finally:
            # Ensure we always log the completion
            logger.info("WebSocket session ended")
