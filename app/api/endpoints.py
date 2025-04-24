"""
API endpoints for handling Twilio voice calls and webhooks
"""
from fastapi import APIRouter, Request, Response, HTTPException
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
import os
import logging
from app.utils.constants import get_restaurant_config
from app.utils.twilio import get_call_details

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api", tags=["voice"])

@router.post("/incoming-call")
async def handle_incoming_call(request: Request):
    """Handle incoming calls from Twilio"""
    try:
        # Get restaurant configuration
        restaurant_id = os.getenv("RESTAURANT_ID", "LIMF")
        from app.constants import CONSTANTS
        restaurant_config = CONSTANTS.get(restaurant_id, {})
        twilio_voice = restaurant_config.get("TWILIO_VOICE", "Polly.Joanna-Neural")
        
        # Parse form data from the request
        form_data = await request.form()
        
        # Extract and log key information
        caller_phone = form_data.get("From")
        call_sid = form_data.get("CallSid")
        account_sid = form_data.get("AccountSid")
        
        # Store this information in application state for later use
        from app.api.websocket import set_caller_info
        if caller_phone:
            set_caller_info(call_sid, caller_phone)
            
        # Log the incoming call data
        logger.info(f"Received incoming call: CallSid={call_sid}, From={caller_phone}, AccountSid={account_sid}")
        
        # Create TwiML response
        response = VoiceResponse()
        
        # Add greeting with natural pauses for better TTS flow
        response.say(
            "Please wait while we connect your call to the restaurant assistant.",
            voice=twilio_voice
        )
        response.pause(length=1)
        
        # Get host information for the WebSocket connection
        host = request.url.hostname
        port = request.url.port
        scheme = "wss" if request.url.scheme == "https" else "ws"
        
        # Create the WebSocket URL
        # If running locally with port, include it; otherwise assume production with proper domain
        if port and port not in (80, 443):
            ws_url = f"{scheme}://{host}:{port}/media-stream"
        else:
            ws_url = f"{scheme}://{host}/media-stream"
            
        logger.info(f"Setting up WebSocket connection to: {ws_url}")
        
        # Connect to the WebSocket for real-time audio
        connect = Connect()
        connect.stream(url=ws_url)
        response.append(connect)
        
        # Return the TwiML response
        return Response(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"Error handling incoming call: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
@router.post("/call-status")
async def handle_call_status(request: Request):
    """Handle Twilio call status callbacks"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        
        logger.info(f"Call {call_sid} status changed to: {call_status}")
        
        # If the call has ended, update the database
        if call_status in ("completed", "busy", "failed", "no-answer", "canceled"):
            from app.services.database_service import save_call_end
            await save_call_end(call_sid)
            logger.info(f"Call {call_sid} marked as ended with status: {call_status}")
        
        # Return an empty TwiML response
        response = VoiceResponse()
        return Response(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"Error handling call status callback: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/call/{call_sid}")
async def get_call(call_sid: str):
    """Get details for a specific call"""
    try:
        call_details = get_call_details(call_sid)
        if call_details.get("success", False):
            return call_details
        else:
            raise HTTPException(status_code=404, detail=f"Call not found or error retrieving details")
    except Exception as e:
        logger.error(f"Error retrieving call details: {e}")
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

# Create additional router for database operations
db_router = APIRouter(prefix="/api/db", tags=["database"])

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
