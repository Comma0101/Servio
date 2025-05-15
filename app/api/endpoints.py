"""
API endpoints for handling Twilio voice calls and webhooks
"""
from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream, Gather, Start, Transcription
from twilio.rest import Client
from openai import AsyncOpenAI
import os
import asyncio
import logging
from app.utils.constants import get_restaurant_config
from app.utils.twilio import get_call_details
from app.api.websocket import get_handler_instance

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api", tags=["voice"])

# Create additional router for database operations
db_router = APIRouter(prefix="/api/db", tags=["database"])

# Cache for OpenAI clients to avoid connection overhead
_openai_client_cache = {}


def get_openai_client(api_key, timeout=4.0, max_retries=0):
    """Get or create an OpenAI client with optimized settings"""
    from openai import AsyncOpenAI
    
    # Use existing client if available to avoid connection overhead
    cache_key = f"{api_key}_{timeout}_{max_retries}"
    if cache_key in _openai_client_cache:
        return _openai_client_cache[cache_key]
    
    # Create a new client with optimized settings
    client = AsyncOpenAI(
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries
    )
    
    # Cache the client for future use
    _openai_client_cache[cache_key] = client
    return client


@router.post("/incoming-call")
async def handle_incoming_call(request: Request):
    """Handle incoming calls from Twilio"""
    try:
        # Get restaurant configuration
        restaurant_id = os.getenv("RESTAURANT_ID", "LIMF")
        restaurant_config = get_restaurant_config(restaurant_id)
        twilio_voice = restaurant_config.get("TWILIO_VOICE", "Polly.Joanna-Neural")
        
        # Parse form data from the request
        form_data = await request.form()
        
        # Extract and log key information
        caller_phone = form_data.get("From")
        call_sid = form_data.get("CallSid")
        account_sid = form_data.get("AccountSid")
        
        # Caller info (phone, language) will be stored after language selection
            
        # Log the incoming call data
        logger.info(f"Received incoming call: CallSid={call_sid}, From={caller_phone}, AccountSid={account_sid}")
        
        # Get host information for building callback URLs
        host = request.url.hostname
        port = request.url.port
        scheme = "https" if request.url.scheme == "https" else "http"
        
        # Build the callback URL for language selection
        if port and port not in (80, 443):
            callback_url = f"{scheme}://{host}:{port}/api/language-selection"
        else:
            callback_url = f"{scheme}://{host}/api/language-selection"
        
        # Create TwiML response with language selection
        response = VoiceResponse()
        
        # Add a brief welcome greeting
        response.say(
            "Welcome to our restaurant.",
            voice=twilio_voice
        )
        
        # Add Gather for language selection - must use response.gather() 
        # so it's properly nested in the TwiML flow
        gather = response.gather(
            num_digits=1,
            action=callback_url,
            method="POST",
            timeout=10
        )
        
        # Prompt for language selection
        gather.say(
            "For English, press 1. For Chinese, press 2.",
            voice=twilio_voice,
            language="en-US"
        )
        
        # This code only executes AFTER the gather timeout expires
        # If no input is received, default to English with an explanation
        response.say("We didn't receive your selection. Continuing in English.", voice=twilio_voice)
        response.redirect(f"{callback_url}?Digits=1", method="POST")
        
        # Return the TwiML response
        return Response(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"Error handling incoming call: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/language-selection")
async def handle_language_selection(request: Request):
    """Handle language selection and connect to WebSocket or start transcription"""
    try:
        # Parse form data from the request
        form_data = await request.form()

        # Get the selected language digit
        selected_digit = form_data.get("Digits", "1")  # Default to English (1)
        call_sid = form_data.get("CallSid")
        caller_phone = form_data.get("From")

        # Determine language from digit
        language = "chinese" if selected_digit == "2" else "english"

        logger.info(f"Language selected: {language} (digit {selected_digit}) for call {call_sid}")

        # Get restaurant configuration
        restaurant_id = os.getenv("RESTAURANT_ID", "default-restaurant")
        restaurant_config = get_restaurant_config(restaurant_id)
        twilio_voice_en = restaurant_config.get("TWILIO_VOICE_EN", "Polly.Joanna-Neural")
        twilio_voice_zh = restaurant_config.get("TWILIO_VOICE_ZH", "Polly.Zhiyu-Neural")

        # Update caller info with language preference using the global store
        from app.api.websocket import store_caller_info # Import the correct function
        if caller_phone and call_sid:
            store_caller_info(call_sid, caller_phone, language) # Use the correct function

        # Create TwiML response
        response = VoiceResponse()

        # Get host information for building URLs
        host = request.url.hostname
        port = request.url.port
        http_scheme = "https" if request.url.scheme == "https" else "http"
        ws_scheme = "wss" if request.url.scheme == "https" else "ws"

        # Build base URLs
        if port and port not in (80, 443):
            base_http_url = f"{http_scheme}://{host}:{port}"
            base_ws_url = f"{ws_scheme}://{host}:{port}"
        else:
            base_http_url = f"{http_scheme}://{host}"
            base_ws_url = f"{ws_scheme}://{host}"

        # Determine WebSocket URL based on language
        if language == "english":
            ws_url = f"{base_ws_url}/api/media-stream" # Correct endpoint for English/Deepgram
        else: # chinese
            ws_url = f"{base_ws_url}/api/ws/{call_sid}" # Endpoint for Chinese/Google Speech

        transcription_callback_url = f"{base_http_url}/api/transcription-callback" # This might be deprecated or unused

        if language == "english":
            response.say(
                "You selected English. Connecting you to our restaurant assistant.",
                voice=twilio_voice_en
            )
            response.pause(length=1)
            logger.info(f"Connecting to WebSocket for English (Deepgram): {ws_url}")
            connect = Connect()
            stream = Stream(url=ws_url)
            # For /media-stream, parameters are often sent in the 'start' message or not needed in the URL itself
            # Twilio's <Stream> parameters are available in the 'start' event's customParameters
            # Let's ensure we still pass what might be expected by the /media-stream handler if it checks customParameters
            stream.parameter(name="language", value="english") # This will appear in customParameters
            stream.parameter(name="restaurant_id", value=restaurant_id) # This will appear in customParameters
            connect.append(stream)
            response.append(connect)

        else: # Chinese
            logger.info(f"Language selected: Chinese for CallSid: {call_sid}. Will connect to WebSocket for STT via Google Cloud Speech.")
            # Caller info with language was already stored above using store_caller_info
            
            # Create a Twilio response object
            response = VoiceResponse()
            
            # Save the call start to database - use try/except around each step for better error isolation
            try:
                from app.services.database_service import get_db_pool
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    await conn.execute('''
                        INSERT INTO calls (call_sid, caller_phone)
                        VALUES ($1, $2)
                        ON CONFLICT (call_sid) DO UPDATE
                        SET caller_phone = $2
                    ''', call_sid, caller_phone)
                logger.info(f"Directly inserted call record for {call_sid} to database.")
            except Exception as db_err:
                logger.error(f"Error inserting call record: {db_err}")
            
            # Get restaurant configuration
            restaurant_id = os.getenv("RESTAURANT_ID", "default-restaurant")
            restaurant_config = get_restaurant_config(restaurant_id)
            chinese_voice = restaurant_config.get("TWILIO_VOICE_ZH", "Polly.Zhiyu-Neural")

            # Initial greeting in Chinese
            response.say(
                "您好！欢迎致电我们的餐厅,正在帮您连接", 
                voice=chinese_voice,
                language="cmn-Hans-CN" # Mandarin Chinese language code for Polly voice
            )
            response.pause(length=1) # Pause to ensure greeting is fully played before streaming starts
            
            # Connect to WebSocket for Chinese audio streaming
            logger.info(f"Connecting to WebSocket for Chinese: {ws_url}")
            connect = Connect()
            stream = Stream(url=ws_url)
            # Pass language and restaurant_id as parameters to the WebSocket stream
            # These will be read by the websocket_call_handler in websocket.py
            stream.parameter(name="language", value="chinese")
            stream.parameter(name="restaurant_id", value=restaurant_id)
            connect.append(stream)
            response.append(connect)

        # Return the TwiML response
        return Response(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"Error handling language selection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/v1/create-order")
async def create_order(request: Request):
    """Create a new order in Square"""
    try:
        # Parse JSON data from the request
        json_data = await request.json()
        items = json_data.get("items", [])
        
        if not items:
            return {"error": "No items provided"}
        
        # Get Square location ID
        from app.utils.square import get_square_location_id, create_square_order
        location_id = await get_square_location_id()
        
        if not location_id:
            return {"error": "Failed to get Square location ID"}
        
        # Create the order in Square
        result = await create_square_order(items, location_id)
        
        logger.info(f"Square order created: {result}")
        return result
    except Exception as e:
        logger.error(f"Error creating Square order: {e}")
        return {"error": str(e)}


@db_router.get("/calls")
async def list_calls(limit: int = 50, offset: int = 0):
    """
    List all calls with pagination
    
    Parameters:
    - limit: Maximum number of calls to return (default: 50)
    - offset: Number of calls to skip (for pagination)
    """
    try:
        from app.services.database_service import get_db_pool
        
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Get total count for pagination info
            total_count = await conn.fetchval('SELECT COUNT(*) FROM calls')
            
            # Get calls with pagination
            rows = await conn.fetch('''
                SELECT id, call_sid, caller_phone, start_time, end_time, audio_url
                FROM calls
                ORDER BY start_time DESC
                LIMIT $1 OFFSET $2
            ''', limit, offset)
            
            # Convert rows to dictionaries for JSON serialization
            calls = []
            for row in rows:
                calls.append({
                    "id": row["id"],
                    "call_sid": row["call_sid"],
                    "caller_phone": row["caller_phone"],
                    "start_time": row["start_time"].isoformat() if row["start_time"] else None,
                    "end_time": row["end_time"].isoformat() if row["end_time"] else None,
                    "audio_url": row["audio_url"],
                    "duration_seconds": (row["end_time"] - row["start_time"]).total_seconds() 
                        if row["end_time"] and row["start_time"] else None
                })
            
            return {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "data": calls
            }
    except Exception as e:
        logger.error(f"Error listing calls: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@db_router.get("/calls/{call_sid}")
async def get_call_by_sid(call_sid: str):
    """Get details for a specific call including utterances"""
    try:
        from app.services.database_service import get_call_details, get_call_utterances
        
        # Get call details
        call_details = await get_call_details(call_sid)
        if not call_details:
            raise HTTPException(status_code=404, detail=f"Call not found: {call_sid}")
        
        # Get utterances for this call
        utterances = await get_call_utterances(call_sid)
        
        # Include utterances in the response
        call_details["utterances"] = utterances
        
        return call_details
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving call details: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@db_router.get("/utterances")
async def list_recent_utterances(limit: int = 100):
    """List recent utterances with pagination"""
    try:
        from app.services.database_service import get_recent_utterances
        
        utterances = await get_recent_utterances(limit)
        return {
            "count": len(utterances),
            "data": utterances
        }
    except Exception as e:
        logger.error(f"Error listing utterances: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@db_router.get("/calls/{call_sid}/utterances")
async def get_utterances_by_call(call_sid: str):
    """Get all utterances for a specific call"""
    try:
        from app.services.database_service import get_call_utterances
        
        utterances = await get_call_utterances(call_sid)
        if not utterances:
            # Call might exist but have no utterances, or call might not exist
            # Check if call exists
            from app.services.database_service import get_call_details
            call_details = await get_call_details(call_sid)
            if not call_details:
                raise HTTPException(status_code=404, detail=f"Call not found: {call_sid}")
        
        return {
            "call_sid": call_sid,
            "count": len(utterances),
            "data": utterances
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving call utterances: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
