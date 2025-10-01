# File: /home/comma/Documents/Servio/app/services/call_state_service.py

import asyncio
import logging
import time
from typing import Dict, Optional, Set

# Configure logging
logger = logging.getLogger(__name__)

# Global state tracking
_call_states: Dict[str, Dict] = {}
_final_messages: Dict[str, Dict] = {}
_media_events: Dict[str, Dict] = {}

async def register_call(call_sid: str, stream_sid: str, caller_phone: Optional[str] = None):
    """Register a new call in the state service"""
    _call_states[call_sid] = {
        "stream_sid": stream_sid,
        "caller_phone": caller_phone,
        "start_time": time.time(),
        "last_activity": time.time(),
        "status": "active",
        "pending_tts": set(),
        "completed_tts": set(),
        "next_tool_to_use": None,  # For stateful tool enforcement
        "order_placed_successfully": False # To prevent duplicate order submissions
    }
    logger.info(f"Registered call {call_sid} with stream {stream_sid}")
    return True

async def register_final_message(call_sid: str, utterance_id: str):
    """Register that a final message has been sent for this call"""
    if call_sid not in _call_states:
        logger.warning(f"Attempted to register final message for unknown call: {call_sid}")
        return False
    
    # Track this message as a pending TTS message
    _call_states[call_sid]["pending_tts"].add(utterance_id)
    _call_states[call_sid]["last_activity"] = time.time()
    
    # Mark this as the final message that should trigger hangup when complete
    _final_messages[call_sid] = {
        "utterance_id": utterance_id,
        "registered_at": time.time(),
        "completed": False
    }
    
    logger.info(f"Registered final message {utterance_id} for call {call_sid}")
    return True

async def register_tts_completion(stream_sid: str, utterance_id: str):
    """Register that a TTS message has completed playing"""
    # Find the call_sid for this stream
    call_sid = None
    for cid, state in _call_states.items():
        if state.get("stream_sid") == stream_sid:
            call_sid = cid
            break
    
    if not call_sid:
        logger.warning(f"TTS completion for unknown stream: {stream_sid}, utterance: {utterance_id}")
        return False
    
    # Mark this utterance as completed
    if utterance_id in _call_states[call_sid]["pending_tts"]:
        _call_states[call_sid]["pending_tts"].remove(utterance_id)
        _call_states[call_sid]["completed_tts"].add(utterance_id)
        _call_states[call_sid]["last_activity"] = time.time()
        
        # Check if this was the final message
        if call_sid in _final_messages and _final_messages[call_sid]["utterance_id"] == utterance_id:
            _final_messages[call_sid]["completed"] = True
            logger.info(f"Final message {utterance_id} completed for call {call_sid}")
            return True
    
    return False

async def register_tts_started(stream_sid: str, utterance_id: str) -> bool:
    """Register that TTS has started for a specific utterance"""
    # Find the call_sid for this stream
    call_sid = await get_call_sid_from_stream(stream_sid)
    if not call_sid:
        logger.warning(f"TTS start for unknown stream: {stream_sid}, utterance: {utterance_id}")
        return False
    
    # Mark this as a pending utterance if not already
    if call_sid in _call_states:
        if utterance_id not in _call_states[call_sid]["pending_tts"]:
            _call_states[call_sid]["pending_tts"].add(utterance_id)
        
        _call_states[call_sid]["last_activity"] = time.time()
        logger.info(f"Marked utterance {utterance_id} as started TTS for call {call_sid}")
        
        # Check if this is a final message
        if call_sid in _final_messages and _final_messages[call_sid]["utterance_id"] == utterance_id:
            _final_messages[call_sid]["tts_started"] = time.time()
            logger.info(f"Final message {utterance_id} has started TTS for call {call_sid}")
        
        return True
    
    return False

async def should_terminate_call(call_sid: str) -> bool:
    """Check if a call should be terminated based on final message completion"""
    if call_sid not in _call_states:
        return False
    
    if call_sid in _final_messages and _final_messages[call_sid]["completed"]:
        return True
    
    # Additional safety check for long-running calls with final messages
    if call_sid in _final_messages:
        # If it's been more than 15 seconds since final message registration, terminate
        elapsed = time.time() - _final_messages[call_sid]["registered_at"]
        if elapsed > 15:
            logger.warning(f"Forcing call termination after 15s timeout on final message: {call_sid}")
            return True
    
    return False

async def register_media_event(stream_sid: str, event_type: str, event_data: Dict):
    """Register a media event that might indicate TTS completion"""
    # Store the event
    if stream_sid not in _media_events:
        _media_events[stream_sid] = []
    
    _media_events[stream_sid].append({
        "type": event_type,
        "data": event_data,
        "timestamp": time.time()
    })
    
    # Check if this is an indication of TTS completion
    # Twilio standards for track completion
    if event_type == "media" and event_data.get("track", {}).get("state") in ["ended", "completed"]:
        # This is a standard indication of track completion in WebRTC
        logger.info(f"Media track completed for stream {stream_sid}")
        
        # Get the call_sid
        call_sid = None
        for cid, state in _call_states.items():
            if state.get("stream_sid") == stream_sid:
                call_sid = cid
                break
        
        if call_sid and call_sid in _final_messages:
            # Mark the final message as completed regardless of utterance tracking
            # This is a backup mechanism using WebRTC standards
            utterance_id = _final_messages[call_sid]["utterance_id"]
            await register_tts_completion(stream_sid, utterance_id)
            return True
    
    return False

async def get_call_sid_from_stream(stream_sid: str) -> Optional[str]:
    """Get the call_sid associated with a stream_sid"""
    for call_sid, state in _call_states.items():
        if state.get("stream_sid") == stream_sid:
            return call_sid
    return None

async def remove_call_state(call_sid: str):
    """Remove the state associated with a completed call."""
    if call_sid in _call_states:
        del _call_states[call_sid]
        logger.info(f"Removed call state for call_sid: {call_sid}")
    else:
        logger.warning(f"Attempted to remove state for non-existent call_sid: {call_sid}")
        
    # Optional: Clean up other related states if necessary
    if call_sid in _final_messages:
        del _final_messages[call_sid]
        logger.debug(f"Removed final message state for call_sid: {call_sid}")
        
    # Media events might grow; consider cleaning them too if needed, 
    # though stream_sid might be better key if calls can reuse streams (unlikely)
    # stream_sid_to_remove = None
    # for cid, state in list(_call_states.items()): # Use list for safe iteration while deleting
    #     if cid == call_sid:
    #         stream_sid_to_remove = state.get("stream_sid")
    #         break
    # if stream_sid_to_remove and stream_sid_to_remove in _media_events:
    #     del _media_events[stream_sid_to_remove]
    #     logger.debug(f"Removed media events for stream_sid: {stream_sid_to_remove}")

async def set_next_tool(call_sid: str, tool_name: str):
    """Sets the specific tool that must be used on the next turn."""
    if call_sid in _call_states:
        _call_states[call_sid]["next_tool_to_use"] = tool_name
        _call_states[call_sid]["last_activity"] = time.time()
        logger.info(f"State override: Next tool for call {call_sid} MUST be '{tool_name}'")
    else:
        logger.warning(f"Attempted to set next tool for unknown call_sid: {call_sid}")

async def get_and_clear_next_tool(call_sid: str) -> Optional[str]:
    """Retrieves and clears the specific tool for the current turn."""
    if call_sid in _call_states:
        tool_name = _call_states[call_sid].get("next_tool_to_use")
        if tool_name:
            logger.info(f"State override: Retrieved next tool '{tool_name}' for call {call_sid}. Clearing state.")
            _call_states[call_sid]["next_tool_to_use"] = None
            _call_states[call_sid]["last_activity"] = time.time()
            return tool_name
    return None

async def clear_next_tool(call_sid: str):
    """Clears the specific tool override for a call."""
    if call_sid in _call_states:
        if _call_states[call_sid].get("next_tool_to_use"):
            logger.info(f"State override: Clearing next tool for call {call_sid}.")
            _call_states[call_sid]["next_tool_to_use"] = None
            _call_states[call_sid]["last_activity"] = time.time()

async def set_order_placed_flag(call_sid: str):
    """Sets the flag indicating an order has been successfully placed."""
    if call_sid in _call_states:
        _call_states[call_sid]["order_placed_successfully"] = True
        _call_states[call_sid]["last_activity"] = time.time()
        logger.info(f"State update: Order has been successfully placed for call {call_sid}.")
    else:
        logger.warning(f"Attempted to set order placed flag for unknown call_sid: {call_sid}")

async def has_order_been_placed(call_sid: str) -> bool:
    """Checks if an order has already been successfully placed for the call."""
    if call_sid in _call_states:
        return _call_states[call_sid].get("order_placed_successfully", False)
    return False
