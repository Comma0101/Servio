"""
API endpoints for handling Twilio voice calls and webhooks
"""
from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream, Gather, Start
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

# Create router for English Menu operations
menu_router = APIRouter(prefix="/api/menu", tags=["Menu"])

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


@router.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming calls from Twilio"""
    try:
        # Get restaurant_id from query parameters, default to LIMF if not provided
        restaurant_id_from_query = request.query_params.get("restaurant_id")
        restaurant_id = restaurant_id_from_query or os.getenv("RESTAURANT_ID", "LIMF")
        logger.info(f"Handling incoming call for restaurant_id: {restaurant_id}")

        restaurant_config = get_restaurant_config(restaurant_id)
        # Use a generic welcome voice or a restaurant-specific one if defined
        welcome_voice = restaurant_config.get("TWILIO_WELCOME_VOICE", restaurant_config.get("TWILIO_VOICE", "Polly.Joanna-Neural"))
        
        # Get the selected language digit from either form data (POST) or query params (GET)
        if request.method == "POST":
            form_data = await request.form()
            caller_phone = form_data.get("From")
            call_sid = form_data.get("CallSid")
            account_sid = form_data.get("AccountSid")
        else:  # GET
            caller_phone = request.query_params.get("From")
            call_sid = request.query_params.get("CallSid")
            account_sid = request.query_params.get("AccountSid")
        
        # Caller info (phone, language) will be stored after language selection
            
        # Log the incoming call data
        logger.info(f"Received incoming call for {restaurant_id}: CallSid={call_sid}, From={caller_phone}, AccountSid={account_sid}")
        
        # Get host information for building callback URLs
        host = request.url.hostname
        port = request.url.port
        scheme = "https" if request.url.scheme == "https" else "http"
        
        # Build the callback URL for language selection, including restaurant_id
        base_action_url = f"{scheme}://{host}"
        if port and port not in (80, 443):
            base_action_url += f":{port}"
        
        # Ensure restaurant_id is included in the action URL for language selection
        action_url_with_restaurant = f"{base_action_url}/api/language-selection?restaurant_id={restaurant_id}"
        
        # Create TwiML response with language selection
        response = VoiceResponse()
        
        # Add a brief welcome greeting
        # Use restaurant_name from config if available for the welcome message
        restaurant_name = restaurant_config.get("RESTAURANT_NAME", "our restaurant")
        response.say(
            f"Welcome to {restaurant_name}.",
            voice=welcome_voice # Use the determined welcome voice
        )
        
        # Add Gather for language selection - must use response.gather() 
        # so it's properly nested in the TwiML flow
        gather = response.gather(
            num_digits=1,
            action=action_url_with_restaurant, # Use URL with restaurant_id
            method="POST",
            timeout=10
        )
        
        # Prompt for language selection
        # Use the English voice from config for the English part
        twilio_voice_en = restaurant_config.get("TWILIO_VOICE_EN", "Polly.Joanna-Neural") # Fallback to a generic English voice
        gather.say(
            "For English, press 1.",
            voice=twilio_voice_en,
            language="en-US"
        )
        # Use the Chinese voice from config for the Chinese part
        twilio_voice_zh = restaurant_config.get("TWILIO_VOICE_ZH", "Polly.Zhiyu-Neural") # Fallback to a generic Chinese voice
        gather.say(
            "中文请按2。", # "For Chinese, press 2."
            voice=twilio_voice_zh,
            language="cmn-CN" # Use cmn-CN for Mandarin
        )
        
        # This code only executes AFTER the gather timeout expires
        # If no input is received, default to English with an explanation
        # Ensure the redirect also includes the restaurant_id
        timeout_redirect_url = f"{action_url_with_restaurant}&Digits=1" # Append Digits=1 for timeout default
        response.say(f"We didn't receive your selection. Continuing in English for {restaurant_name}.", voice=welcome_voice)
        response.redirect(timeout_redirect_url, method="POST")
        
        # Return the TwiML response
        return Response(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"Error handling incoming call: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.api_route("/language-selection", methods=["GET", "POST"])
async def handle_language_selection(request: Request):
    """Handle language selection and connect to WebSocket or start transcription"""
    try:
        # Get restaurant_id from query parameters (passed from incoming-call)
        # Fallback to environment variable then LIMF if not in query (e.g. direct POST or timeout)
        restaurant_id_from_query = request.query_params.get("restaurant_id")
        restaurant_id = restaurant_id_from_query or os.getenv("RESTAURANT_ID", "LIMF")
        logger.info(f"Handling language selection for restaurant_id: {restaurant_id}")

        # Get the selected language digit from either form data (POST) or query params (GET)
        if request.method == "POST":
            form_data = await request.form()
            selected_digit = form_data.get("Digits", "1")
            call_sid = form_data.get("CallSid")
            caller_phone = form_data.get("From")
        else:  # GET
            selected_digit = request.query_params.get("Digits", "1")
            call_sid = request.query_params.get("CallSid")
            caller_phone = request.query_params.get("From")

        # Determine language from digit
        language = "chinese" if selected_digit == "2" else "english"

        logger.info(f"Language selected: {language} (digit {selected_digit}) for call {call_sid} at restaurant {restaurant_id}")

        # Get restaurant configuration using the determined restaurant_id
        restaurant_config = get_restaurant_config(restaurant_id)
        twilio_voice_en = restaurant_config.get("TWILIO_VOICE_EN", "Polly.Joanna-Neural")
        twilio_voice_zh = restaurant_config.get("TWILIO_VOICE_ZH", "Polly.Zhiyu-Neural")

        # Update caller info with language preference using the global store
        # Also pass restaurant_id to be stored if needed later, or ensure it's passed to WebSocket
        from app.api.websocket import store_caller_info 
        if caller_phone and call_sid:
            # Store language and potentially restaurant_id if your store_caller_info supports it
            # For now, just language. restaurant_id will be passed to WebSocket via parameters.
            store_caller_info(call_sid, caller_phone, language)

        # Create TwiML response
        response = VoiceResponse()

        # Get host information for building URLs
        from app.config import settings
        base_ws_url = settings.PUBLIC_BASE_URL.replace("http", "ws")

        # Determine WebSocket URL based on language
        if language == "english":
            ws_url = f"{base_ws_url}/api/media-stream" # Correct endpoint for English/Deepgram
        else: # chinese
            ws_url = f"{base_ws_url}/api/ws/{call_sid}" # Endpoint for Chinese/Google Speech

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
            
            # Get restaurant configuration using the determined restaurant_id
            # This was already done above, so restaurant_config and twilio_voice_zh are set
            # chinese_voice = restaurant_config.get("TWILIO_VOICE_ZH", "Polly.Zhiyu-Neural") # Already fetched

            # Initial greeting in Chinese, potentially restaurant-specific
            restaurant_name_cn = restaurant_config.get("RESTAURANT_NAME_CN", "我们的餐厅") # Default if not set
            response.say(
                f"您好！欢迎致电{restaurant_name_cn}，正在帮您连接",
                voice=twilio_voice_zh, # Use the fetched Chinese voice
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
            # Add the new parameter to activate Deepgram STT for Chinese
            stream.parameter(name="use_deepgram_stt_chinese", value="true")
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


# ─── Menu Endpoints ─────────────────────────────────────────────────
from app.utils.thirty_nine_miles import ( # Using user-confirmed filename module
    get_extracted_dishes as get_menu_extracted_dishes,
    find_dish_by_chinese_name as find_menu_dish_by_chinese_name,
    add_order as add_menu_order,
    ApiOrderDto as MenuApiOrderDto
)

@menu_router.get("/extracted-dishes/{portal_id}")
async def get_extracted_dishes_endpoint(portal_id: str):
    """Fetches and processes the detailed menu to return a flat list of dishes from the Menu API."""
    try:
        result = await get_menu_extracted_dishes(portal_id)
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("message", "Failed to extract dishes from Menu"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /extracted-dishes/{portal_id} (Menu) endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@menu_router.get("/full-menu/{portal_id}")
async def get_full_menu_endpoint(portal_id: str):
    """Fetches and returns the entire menu for a given portal."""
    try:
        result = await get_menu_extracted_dishes(portal_id)
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("message", "Failed to extract dishes from Menu"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /full-menu/{portal_id} endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@menu_router.get("/find-dish/{portal_id}/{chinese_name}")
async def find_dish_by_name_endpoint(portal_id: str, chinese_name: str):
    """Finds a dish by its Chinese name using the Menu data."""
    try:
        dish_info = await find_menu_dish_by_chinese_name(portal_id, chinese_name)
        if dish_info:
            return {"success": True, "data": dish_info}
        else:
            raise HTTPException(status_code=404, detail=f"Dish with Chinese name '{chinese_name}' not found in portal {portal_id} via Menu API.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /find-dish/{portal_id}/{chinese_name} (Menu) endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@menu_router.post("/order/add")
async def add_order_to_menu_endpoint(order_data: MenuApiOrderDto):
    """Creates a new order in the Menu system."""
    try:
        result = await add_menu_order(order_data)
        return result
    except HTTPException:
        raise 
    except Exception as e:
        logger.error(f"Error in /order/add (Menu) endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.api_route("/v1/human-handoff-twiml", methods=["GET", "POST"])
async def human_handoff_twiml(request: Request):
    """
    This TwiML endpoint is used to handle the human handoff.
    It dials the human agent.
    """
    from app.constants import HUMAN_AGENT_PHONE_NUMBER
    response = VoiceResponse()
    response.say("Now redirecting you to our human assistant, please wait.", voice="Polly.Joanna-Neural")
    response.dial(HUMAN_AGENT_PHONE_NUMBER)
    return Response(content=str(response), media_type="application/xml")
