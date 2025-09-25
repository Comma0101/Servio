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
from app.utils.thirty_nine_miles import find_dish_by_english_name, get_extracted_dishes, TextStore, ApiOrderItem, ApiOrderDto, LangCode, add_order as add_thirty_nine_miles_order, MenuProductOptionGroup, MenuProductOption # Added more imports
from app.constants import THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT # Assuming LIMF uses takeout by default
from app.handlers.order_manager import order_manager # Import the new unified order manager
from app.handlers.combo_order_manager import combo_order_manager
from app.services.call_state_service import set_next_tool, clear_next_tool
import random # For recommendations
import re # Added for is_primarily_english
from typing import List
import copy

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# The old centralized cart (`call_carts`) is now deprecated and all cart
# functionality is managed by the singleton `order_manager` instance.

# Comprehensive manual mapping for all protein and size variations to their exact POS names.
PROTEIN_NAME_MAPPING = {
    "1 lb": {
        "shrimp head on": "1 Ib. shrimp head on",
        "headless shrimp": "1 lb. headless shrimp",
        "peeled tail on": "1  Ib Peeled Tail On",
        "fresh crawfish": "1 lb. fresh crawfish",
        "frozen crawfish": "1 lb. Frz crawfish",
        "crawfish": "1 lb crawfish",
        "lobster tail": "1 （pc). Lobster Tail",
        "king crab legs": "1 lb. King Crab Legs 1 corn 1 order pot",
        "snow crab legs": "1 lb. Snow Crab Legs",
        "dungeness legs": "1 lb. Dungeness Legs",
        "black mussels": "1 lb. Black Mussels",
        "green mussels": "1 lb. Green Mussels",
        "clams": "1 lb. Clams",
        "scallop": "1 lb. Scallop"
    },
    "0.5 lbs": {
        "shrimp head on": "half a pound （shrimp   head on)",
        "headless shrimp": "half a pound（headless shrimp）",
        "peeled tail on": "half a pound Peeled Tail On",
        "fresh crawfish": "half pound of fresh crawfish",
        "frozen crawfish": "half a pound 0.5 frz crawfish",
        "black mussels": "half a pound（black mussels）",
        "green mussels": "half a pound(green mussels )",
        "clams": "half a pound（clams）",
        "scallop": "half a pound（scallop）"
    }
}

def clear_cart(call_sid: str):
    """Clears the cart for a given call_sid using the new order manager."""
    order_manager.clear_cart(call_sid)
    logger.info(f"Cleared cart for call_sid: {call_sid} via order_manager")

# Define a constant for the mark name
FINAL_AUDIO_MARK_NAME = "final_message_played"

def clean_text_for_tts(text: str) -> str:
    """Removes common formatting characters to make text safe for TTS."""
    if not text:
        return ""
    return text.replace("*", "").strip()

def _get_english_name(name_dict: Dict[str, Any], default: str = "") -> str:
    """
    Safely retrieves the English name from a name dictionary,
    falling back to the Chinese name if the English one is null or empty.
    This is a specific workaround for the current menu data source.
    """
    if not name_dict:
        return default
    name_en = name_dict.get("en")
    if name_en and isinstance(name_en, str) and name_en.strip():
        return name_en
    name_zh = name_dict.get("zh")
    if name_zh and isinstance(name_zh, str) and name_zh.strip():
        return name_zh
    return default

def _clean_option_for_tts(option_name: str) -> str:
    """Cleans up an option name for more natural-sounding text-to-speech."""
    if not option_name:
        return ""
    # This regex removes prefixes like '1 lb.', '1 (pc).', and '(3pc)' for cleaner TTS
    cleaned = re.sub(r'^\d+\s?(\(pc\))?\.?\s?(lb|ib|in)\.?\s*|\s*\(\d+pc\)$', '', option_name, flags=re.IGNORECASE).strip()
    return cleaned

def _is_protein_group(group_name: str) -> bool:
    """Checks if an option group is for selecting proteins based on a list of known keywords."""
    protein_group_keywords = ["choose one", "included", "meat option", "choose your crab", "pick 1 pound"]
    
    # Also check for "choose" and "one" separately to catch variations
    if "choose" in group_name.lower() and "one" in group_name.lower():
        return True
        
    return any(keyword in group_name.lower() for keyword in protein_group_keywords)

def is_primarily_english(text: str) -> bool:
    """
    Checks if the text is primarily English characters, allowing spaces,
    common punctuation, and numbers.
    """
    if not text:
        return True
    # This regex allows basic Latin alphabet, digits, and common punctuation.
    # It will not match most characters from other languages.
    return bool(re.fullmatch(r"^[a-zA-Z0-9\s!\"#$%&'()*+,-./:;<=>?@[\\\]^_`{|}~]*$", text))

async def handle_function_call(
    function_request: Dict[str, Any],
    deepgram_service,
    websocket: WebSocket,
    stream_sid: str,
    caller_phone: Optional[str],
    call_sid: Optional[str],
    client_id: Optional[str] # Added client_id
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
    function_name = function_request.get("function_name", "")
    function_call_id = function_request.get("function_call_id", "")
    input_data = function_request.get("input", {})
    
    logger.info(f"Function call from Deepgram: {function_name}")
    logger.info(f"Function call ID: {function_call_id}")
    logger.info(f"Function input: {json.dumps(input_data)}")
    
    try:
        # Handle different function calls
        if function_name == "order_summary":
            await handle_order_summary_thirty_nine_miles_en( # Changed to new specific handler
                function_call_id,
                function_name, # Pass function_name
                input_data,
                deepgram_service,
                websocket, 
                stream_sid,
                caller_phone,
                call_sid,
                client_id # Pass client_id
            )
        elif function_name == "check_menu_item_english":
            portal_id_to_use = None
            if client_id == "LIMF": # This mapping might need to be more robust or configurable
                portal_id_to_use = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT
            # Add other client_id to portal_id mappings here if necessary
            
            if not portal_id_to_use:
                logger.error(f"Cannot determine portal_id for client_id: {client_id} in check_menu_item_english")
                # Send error response back to Deepgram
                error_output = "Internal configuration error: Cannot determine restaurant portal."
                response = {
                    "type": "FunctionCallResponse",
                    "id": function_call_id,
                    "name": function_name,
                    "content": error_output
                }
                await deepgram_service.send_json(response)
                return

            await handle_check_menu_item_english(
                function_call_id,
                function_name, # Pass function_name
                input_data,
                deepgram_service,
                portal_id_to_use,
                call_sid # For logging/DB
            )
        elif function_name == "list_dishes_by_category_english":
            portal_id_to_use = None
            if client_id == "LIMF":
                portal_id_to_use = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT
            # Add other client_id to portal_id mappings here
            if not portal_id_to_use:
                logger.error(f"Cannot determine portal_id for client_id: {client_id} in list_dishes_by_category_english")
                error_output = "Internal configuration error: Cannot determine restaurant portal."
                response = {
                    "type": "FunctionCallResponse",
                    "id": function_call_id,
                    "name": function_name,
                    "content": error_output
                }
                await deepgram_service.send_json(response)
                return
            await handle_list_dishes_by_category_english(
                function_call_id,
                function_name, # Pass function_name
                input_data,
                deepgram_service,
                portal_id_to_use,
                call_sid 
            )
        elif function_name == "recommend_dishes_english":
            portal_id_to_use = None
            if client_id == "LIMF":
                portal_id_to_use = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT
            # Add other client_id to portal_id mappings here
            if not portal_id_to_use:
                logger.error(f"Cannot determine portal_id for client_id: {client_id} in recommend_dishes_english")
                error_output = "Internal configuration error: Cannot determine restaurant portal."
                response = {
                    "type": "FunctionCallResponse",
                    "id": function_call_id,
                    "name": function_name,
                    "content": error_output
                }
                await deepgram_service.send_json(response)
                return
            await handle_recommend_dishes_english(
                function_call_id,
                function_name, # Pass function_name
                input_data,
                deepgram_service,
                portal_id_to_use,
                call_sid
            )
        elif function_name == "get_random_menu_categories_english":
            portal_id_to_use = None
            if client_id == "LIMF":
                portal_id_to_use = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT
            # Add other client_id to portal_id mappings here
            if not portal_id_to_use:
                logger.error(f"Cannot determine portal_id for client_id: {client_id} in get_random_menu_categories_english")
                error_output = "Internal configuration error: Cannot determine restaurant portal."
                response = {
                    "type": "FunctionCallResponse",
                    "id": function_call_id,
                    "name": function_name,
                    "content": error_output
                }
                await deepgram_service.send_json(response)
                return
            await handle_get_random_menu_categories_english(
                function_call_id,
                function_name,
                deepgram_service,
                portal_id_to_use,
                call_sid
            )
        elif function_name == "send_menu_link":
            await handle_send_menu_link(
                function_call_id,
                function_name,
                deepgram_service,
                caller_phone,
                client_id,
                call_sid
            )
        elif function_name == "handle_standard_item_selection":
            await handle_standard_item_selection(
                function_call_id,
                function_name,
                input_data,
                deepgram_service,
                call_sid
            )
        elif function_name == "handle_combo_item_selection":
            await handle_combo_item_selection(
                function_call_id,
                function_name,
                input_data,
                deepgram_service,
                call_sid
            )
        else:
            logger.warning(f"Unknown function call: {function_name}")
            response_content_str = f"The function {function_name} is not implemented."
            response = {
                "type": "FunctionCallResponse",
                "id": function_call_id,
                "name": function_name,
                "content": response_content_str
            }
            await deepgram_service.send_json(response)
            
    except Exception as e:
        logger.error(f"Error handling function call '{function_name}': {e}", exc_info=True)
        if function_call_id:
            try:
                error_message_for_agent = "Sorry, there was an error processing your request."
                error_response = {
                    "type": "FunctionCallResponse",
                    "id": function_call_id,
                    "name": function_name,
                    "content": error_message_for_agent
                }
                await deepgram_service.send_json(error_response)
            except Exception as e2:
                logger.error(f"Error sending error response: {e2}")

async def handle_check_menu_item_english(
    function_call_id: str,
    function_name: str, # Added function_name
    input_data: Dict[str, Any],
    deepgram_service,
    portal_id: str,
    call_sid: Optional[str]
):
    """Handles the check_menu_item_english function call."""
    dish_name_en_query = input_data.get("dish_name_en")
    logger.info(f"Handling {function_name} for '{dish_name_en_query}' in portal {portal_id} (CallSid: {call_sid})")

    output_payload = {"found": False, "dish_name_en_queried": dish_name_en_query, "message_for_agent": f"Dish '{dish_name_en_query}' was not found."}

    if not dish_name_en_query:
        logger.warning("dish_name_en not provided in check_menu_item_english call.")
        output_payload["message_for_agent"] = "Dish name was not provided by the agent."
    else:
        try:
            found_dishes_from_pos = await find_dish_by_english_name(portal_id, dish_name_en_query)
            
            if found_dishes_from_pos:
                # Filter for dishes that have a valid English or fallback Chinese name.
                valid_matches = []
                for dish_data in found_dishes_from_pos:
                    dish_details = dish_data.get("dish_details", {})
                    name_to_check = _get_english_name(dish_details.get("name"))
                    if name_to_check and is_primarily_english(name_to_check):
                        valid_matches.append(dish_data)
                
                if valid_matches:
                    # For simplicity, take the first valid match.
                    first_valid_match_details = valid_matches[0].get("dish_details", {})
                    name_en_to_use = _get_english_name(first_valid_match_details.get("name"), dish_name_en_query)
                    price_en = first_valid_match_details.get("price")

                    # Universal cleaning for all option group names before sending to AI
                    raw_option_groups = first_valid_match_details.get("optionGroups") # Correctly get potential None
                    if raw_option_groups is None:
                        raw_option_groups = [] # Default to empty list if no options exist

                    for group in raw_option_groups:
                        if group.get("name") and group.get("name", {}).get("en"):
                            group["name"]["en"] = clean_text_for_tts(group["name"]["en"])

                    # If the item has no required options and is not a combo, add it directly to the cart.
                    has_required_options = any(group.get("isRequired") for group in raw_option_groups)
                    
                    if not has_required_options:
                        quantity = input_data.get("quantity", 1)
                        order_manager.add_item_to_cart(call_sid, name_en_to_use, quantity, {})
                        output_payload["added_to_cart"] = True
                        output_payload["message_for_agent"] = f"Okay, I've added {quantity} {name_en_to_use} to your order."
                    # --- START OF NEW LOGIC ---
                    # Check if the item is one of the special combos that needs the combo manager
                    name_lower = name_en_to_use.lower()
                    if "customized combo" in name_lower or "combo #1" in name_lower or "combo #2" in name_lower or "combo #3" in name_lower:
                        # Fetch the full menu to pass to the specialized manager
                        live_menu_response = await get_extracted_dishes(portal_id)
                        live_menu_data = live_menu_response.get("data", [])
                        
                        # Use the specialized combo manager for a better conversational start
                        response_from_manager = combo_order_manager.start_combo_order(name_en_to_use, call_sid, live_menu_data)
                        
                        output_payload["message_for_agent"] = response_from_manager.get("message_for_agent")
                        output_payload["tool_to_use"] = "handle_combo_item_selection"
                        await set_next_tool(call_sid, "handle_combo_item_selection")
                    # --- END OF NEW LOGIC ---
                    else:
                        # For all other items with options, use the generic manager
                        quantity = input_data.get("quantity", 1)
                        response_from_manager = order_manager.start_item(first_valid_match_details, quantity, call_sid)
                        output_payload["message_for_agent"] = response_from_manager.get("message_for_agent")
                        output_payload["tool_to_use"] = "handle_standard_item_selection"

                    output_payload["found"] = True
                    
                    output_payload["message_for_agent"] = clean_text_for_tts(output_payload["message_for_agent"])
                    # Simplified ambiguity handling: if more than one valid match, flag it.
                    if len(valid_matches) > 1:
                        output_payload["is_ambiguous"] = True
                        ambiguous_names = [
                            _get_english_name(match.get("dish_details", {}).get("name"))
                            for match in valid_matches
                        ]
                        # Filter out any empty names that might have slipped through
                        ambiguous_names = [name for name in ambiguous_names if name]
                        output_payload["ambiguous_matches_en"] = ambiguous_names
                        output_payload["message_for_agent"] = f"Found multiple items for '{dish_name_en_query}'. Options: {', '.join(ambiguous_names)}. Please clarify."
                    else:
                        output_payload["is_ambiguous"] = False
                    logger.info(f"Dish '{dish_name_en_query}' (matched to '{name_en_to_use}') found in portal {portal_id}.")
                else:
                    logger.info(f"Dish '{dish_name_en_query}' found in POS, but no valid English or fallback name available. Treating as not found.")
                    output_payload["message_for_agent"] = f"Sorry, I couldn't find an entry for '{dish_name_en_query}' on the menu."
            else:
                logger.info(f"Dish '{dish_name_en_query}' not found in portal {portal_id}.")
                # output_payload["message_for_agent"] is already "Dish not found."

        except Exception as e:
            logger.error(f"Error calling find_dish_by_english_name for '{dish_name_en_query}': {e}", exc_info=True)
            output_payload["message_for_agent"] = "An error occurred while checking the menu."
            output_payload["error_details"] = str(e)

    # Before sending to the agent, remove the 'original_name' field used for backend processing
    # to avoid confusing the TTS.
    if output_payload.get("found") and output_payload.get("dish_details", {}).get("options"):
        for group in output_payload["dish_details"]["options"]:
            if group.get("options"):
                for option in group["options"]:
                    option.pop("original_name", None) # Use pop for safe removal

    logger.info(f"Full output payload for {function_name}: {json.dumps(output_payload, indent=2)}")

    # The 'content' field must contain the full JSON payload for the agent to process options,
    # as per the system prompt rule #4. The agent is designed to parse this JSON.
    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": json.dumps(output_payload)
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {response['content']}")


async def handle_standard_item_selection(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str]
):
    """
    Handles the user's selection for a standard item's options using the generic OrderManager.
    """
    user_input = input_data.get("user_input")
    logger.info(f"Handling {function_name} with user_input '{user_input}' for a standard item (CallSid: {call_sid})")

    if not user_input:
        response_payload = {
            "status": "REPROMPT",
            "message_for_agent": "I'm sorry, I didn't catch that. Could you please repeat your selection?"
        }
    else:
        logger.info(f"Routing selection to generic OrderManager for call_sid: {call_sid}")
        response_payload = order_manager.process_selection(user_input, call_sid)

        # --- START OF FIX ---
        # Ensure the AI gets the updated cart to prevent stale summaries.
        if response_payload.get("status") == "ITEM_COMPLETE":
            response_payload["current_cart"] = order_manager.get_cart(call_sid)
        # --- END OF FIX ---

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": json.dumps(response_payload)
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {response['content']}")


async def handle_combo_item_selection(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str]
):
    """
    Handles the user's selection for a combo item's options using the ComboOrderManager.
    """
    user_input = input_data.get("user_input")
    logger.info(f"Handling {function_name} with user_input '{user_input}' for a combo item (CallSid: {call_sid})")

    if not user_input:
        response_payload = {
            "status": "REPROMPT",
            "message_for_agent": "I'm sorry, I didn't catch that. Could you please repeat your selection?"
        }
    else:
        logger.info(f"Routing selection to ComboOrderManager for call_sid: {call_sid}")
        response_payload = combo_order_manager.process_selection(user_input, call_sid)

        # Check if the combo has been finalized
        if response_payload.get("status") == "ITEM_READY_FOR_CART":
            item_to_add = response_payload.get("item_to_add")
            if item_to_add:
                # Add the completed combo to the main cart
                order_manager.add_item_to_cart(
                    call_sid,
                    item_to_add.get("name"),
                    item_to_add.get("quantity", 1),
                    item_to_add.get("options", {})
                )
                logger.info(f"Moved completed combo '{item_to_add.get('name')}' to main cart for call {call_sid}.")
            
            # --- START OF FIX ---
            # Ensure the AI gets the updated cart to prevent stale summaries.
            response_payload["current_cart"] = order_manager.get_cart(call_sid)
            # --- END OF FIX ---
            
            # Explicitly clear the state override now that the combo flow is complete
            await clear_next_tool(call_sid)
        
        # If the combo flow is still ongoing, perpetuate the state override
        elif response_payload.get("tool_to_use"):
            await set_next_tool(call_sid, response_payload["tool_to_use"])

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": json.dumps(response_payload)
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {response['content']}")


async def handle_list_dishes_by_category_english(
    function_call_id: str,
    function_name: str, # Added function_name
    input_data: Dict[str, Any],
    deepgram_service,
    portal_id: str,
    call_sid: Optional[str]
):
    """Handles the list_dishes_by_category_english function call."""
    category_name_en = input_data.get("category_name_en")
    logger.info(f"Handling {function_name} for category '{category_name_en}' in portal {portal_id} (CallSid: {call_sid})")

    output_payload = {
        "category_name_en": category_name_en,
        "dishes": [],
        "message_for_agent": f"Could not find dishes for category '{category_name_en}'."
    }

    if not category_name_en:
        logger.warning("category_name_en not provided in list_dishes_by_category_english call.")
        output_payload["message_for_agent"] = "Category name was not provided by the agent."
    else:
        try:
            menu_response = await get_extracted_dishes(portal_id)
            if menu_response and menu_response.get("success"):
                all_dishes_from_pos = menu_response.get("data", [])
                
                # Filter by category first
                category_dishes = [
                    dish for dish in all_dishes_from_pos
                    if dish.get("category_name_en", "").lower() == category_name_en.lower()
                ]
                
                if category_dishes:
                    valid_dishes = []
                    for dish_detail in category_dishes:
                        # The get_extracted_dishes function already provides a direct dish_name_en
                        name_to_check = dish_detail.get("dish_name_en")
                        if name_to_check and is_primarily_english(name_to_check):
                            valid_dishes.append({
                                "name_en": name_to_check,
                                "price": dish_detail.get("price")
                            })
                    
                    if valid_dishes:
                        output_payload["dishes"] = valid_dishes
                        dish_names_str = ", ".join([d["name_en"] for d in valid_dishes])
                        output_payload["message_for_agent"] = f"In the '{category_name_en}' category, we have: {dish_names_str}."
                        logger.info(f"Found {len(valid_dishes)} dishes for category '{category_name_en}' in portal {portal_id}.")
                    else:
                        logger.info(f"Found dishes for category '{category_name_en}', but none with valid English names.")
                        output_payload["message_for_agent"] = f"Sorry, I couldn't find any English named dishes in the '{category_name_en}' category."
                else:
                    logger.info(f"No dishes found for category '{category_name_en}' in portal {portal_id}.")
                    output_payload["message_for_agent"] = f"Sorry, I couldn't find any dishes in the '{category_name_en}' category."
            else:
                error_msg = menu_response.get('message', 'Unknown error') if menu_response else "No response"
                logger.error(f"Failed to get extracted dishes for portal {portal_id}: {error_msg}")
                output_payload["message_for_agent"] = "There was an error retrieving the menu."
        except Exception as e:
            logger.error(f"Error processing {function_name} for '{category_name_en}': {e}", exc_info=True)
            output_payload["message_for_agent"] = "An error occurred while fetching category dishes."
            output_payload["error_details"] = str(e)

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": output_payload.get("message_for_agent", "An error occurred while listing dishes.")
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {response['content']}")


async def handle_recommend_dishes_english(
    function_call_id: str,
    function_name: str, # Added function_name
    input_data: Dict[str, Any],
    deepgram_service,
    portal_id: str,
    call_sid: Optional[str]
):
    """Handles the recommend_dishes_english function call."""
    count = input_data.get("count", 3)
    category_name_en = input_data.get("category_name_en") # Optional
    
    logger.info(f"Handling {function_name} for count={count}, category='{category_name_en}' in portal {portal_id} (CallSid: {call_sid})")

    output_payload = {
        "recommended_dishes": [],
        "message_for_agent": "Sorry, I couldn't find any dishes to recommend at the moment."
    }
    if category_name_en:
        output_payload["category_queried_en"] = category_name_en

    try:
        menu_response = await get_extracted_dishes(portal_id)
        if menu_response and menu_response.get("success"):
            all_dishes_from_pos = menu_response.get("data", [])
            
            candidate_dishes_for_recommendation = all_dishes_from_pos
            if category_name_en:
                candidate_dishes_for_recommendation = [
                    dish for dish in all_dishes_from_pos
                    if dish.get("category_name_en", "").lower() == category_name_en.lower()
                ]
                if not candidate_dishes_for_recommendation:
                    output_payload["message_for_agent"] = f"Sorry, I couldn't find any dishes in the '{category_name_en}' category to recommend."
            
            # Further filter candidates for valid English names
            valid_candidate_dishes = []
            if candidate_dishes_for_recommendation:
                for dish_detail in candidate_dishes_for_recommendation:
                    name_to_check = dish_detail.get("dish_name_en")
                    if name_to_check and is_primarily_english(name_to_check):
                        valid_candidate_dishes.append(dish_detail)
            
            if valid_candidate_dishes:
                num_to_recommend = min(count, len(valid_candidate_dishes))
                num_to_recommend = max(0, num_to_recommend) # Ensure not negative
                
                recommended_dishes_details = []
                if num_to_recommend > 0:
                    recommended_dishes_details = random.sample(valid_candidate_dishes, num_to_recommend)
                
                dishes_to_return = []
                valid_recommended_dish_names = []
                for dish_detail in recommended_dishes_details:
                    # Already pre-filtered for valid English names
                    name_en = dish_detail.get("dish_name_en")
                    dishes_to_return.append({
                        "name_en": name_en,
                        "price": dish_detail.get("price")
                    })
                    valid_recommended_dish_names.append(name_en)
                
                if dishes_to_return:
                    output_payload["recommended_dishes"] = dishes_to_return
                    dish_names_str = ", ".join(valid_recommended_dish_names)
                    
                    if category_name_en:
                        output_payload["message_for_agent"] = f"From the '{category_name_en}' category, how about: {dish_names_str}?"
                    else:
                        output_payload["message_for_agent"] = f"Sure, I can recommend these: {dish_names_str}."
                elif category_name_en: # Had candidates in category, but none were valid English
                     output_payload["message_for_agent"] = f"I found items in the '{category_name_en}' category, but none with clear English names to recommend."
                else: # No category, but all items lacked valid English names or menu was empty
                     output_payload["message_for_agent"] = "I couldn't find any suitable English named dishes to recommend from the menu."

                logger.info(f"Recommended {len(dishes_to_return)} valid English dishes. Category: '{category_name_en}'. Portal: {portal_id}.")

            elif not category_name_en and not all_dishes_from_pos: 
                 output_payload["message_for_agent"] = "The menu seems to be empty right now, so I can't make any recommendations."
            # If category_name_en was specified but no candidate_dishes, or no valid English ones, message is already set.

        else:
            error_msg = menu_response.get('message', 'Unknown error') if menu_response else "No response"
            logger.error(f"Failed to get extracted dishes for portal {portal_id} for recommendation: {error_msg}")
            output_payload["message_for_agent"] = "There was an error retrieving the menu for recommendations."
    except Exception as e:
        logger.error(f"Error processing {function_name}: {e}", exc_info=True)
        output_payload["message_for_agent"] = "An error occurred while preparing recommendations."
        output_payload["error_details"] = str(e)

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": output_payload.get("message_for_agent", "An error occurred while recommending dishes.")
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {response['content']}")


async def handle_get_random_menu_categories_english(
    function_call_id: str,
    function_name: str,
    deepgram_service,
    portal_id: str,
    call_sid: Optional[str]
):
    """Handles the get_random_menu_categories_english function call."""
    logger.info(f"Handling {function_name} in portal {portal_id} (CallSid: {call_sid})")

    output_payload = {
        "categories": [],
        "message_for_agent": "Sorry, I couldn't retrieve any menu categories at the moment."
    }

    try:
        menu_response = await get_extracted_dishes(portal_id)
        if menu_response and menu_response.get("success"):
            all_dishes = menu_response.get("data", [])
            
            # Get unique, valid English category names
            english_categories = sorted(list(set(
                dish.get("category_name_en") 
                for dish in all_dishes 
                if dish.get("category_name_en") and is_primarily_english(dish.get("category_name_en"))
            )))
            
            if english_categories:
                num_to_select = min(3, len(english_categories))
                selected_categories = random.sample(english_categories, num_to_select)
                
                output_payload["categories"] = selected_categories
                categories_str = ", ".join(selected_categories)
                output_payload["message_for_agent"] = f"We have several categories, including: {categories_str}. Which one would you like to hear about?"
                logger.info(f"Found and selected {num_to_select} random English categories for portal {portal_id}.")
            else:
                logger.info(f"No valid English categories found for portal {portal_id}.")
                output_payload["message_for_agent"] = "I couldn't find any English menu categories to suggest."
        else:
            error_msg = menu_response.get('message', 'Unknown error') if menu_response else "No response"
            logger.error(f"Failed to get extracted dishes for portal {portal_id} for categories: {error_msg}")
            output_payload["message_for_agent"] = "There was an error retrieving the menu categories."
    except Exception as e:
        logger.error(f"Error processing {function_name}: {e}", exc_info=True)
        output_payload["message_for_agent"] = "An error occurred while fetching menu categories."
        output_payload["error_details"] = str(e)

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": output_payload.get("message_for_agent", "An error occurred while getting categories.")
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {response['content']}")


async def handle_send_menu_link(
    function_call_id: str,
    function_name: str,
    deepgram_service,
    caller_phone: Optional[str],
    client_id: Optional[str],
    call_sid: Optional[str]
):
    """Handles the send_menu_link function call."""
    logger.info(f"Handling {function_name} for client '{client_id}' to caller '{caller_phone}' (CallSid: {call_sid})")

    output_payload = {
        "sms_sent": False,
        "message_for_agent": "Could not send the menu link."
    }

    if not caller_phone:
        logger.warning(f"Cannot send menu link for call {call_sid}: caller_phone is missing.")
        output_payload["message_for_agent"] = "I'm sorry, I don't have a phone number to send the link to."
    elif not client_id:
        logger.error(f"Cannot send menu link for call {call_sid}: client_id is missing.")
        output_payload["message_for_agent"] = "Internal configuration error: Cannot identify the restaurant."
    else:
        try:
            from app.utils.constants import get_restaurant_config
            config = get_restaurant_config(client_id)
            menu_url = config.get("MENU_URL")
            restaurant_name = config.get("RESTAURANT_NAME", "our restaurant")

            if menu_url:
                sms_body = f"Here is the menu for {restaurant_name}: {menu_url}"
                
                import functools
                loop = asyncio.get_event_loop()
                sms_task = functools.partial(send_sms, caller_phone, sms_body, client_id)
                loop.run_in_executor(None, sms_task)
                
                logger.info(f"Scheduled SMS with menu link to {caller_phone} for client {client_id}.")
                output_payload["sms_sent"] = True
                output_payload["message_for_agent"] = f"I've just sent a text message with a link to our menu to your number."
            else:
                logger.error(f"MENU_URL not found in config for client_id: {client_id}")
                output_payload["message_for_agent"] = "I'm sorry, I couldn't find the menu link to send."

        except Exception as e:
            logger.error(f"Error processing {function_name} for call {call_sid}: {e}", exc_info=True)
            output_payload["message_for_agent"] = "An unexpected error occurred while trying to send the menu link."
            output_payload["error_details"] = str(e)

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": output_payload.get("message_for_agent", "An error occurred.")
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {response['content']}")


# The handle_send_combo_menu_sms function is now obsolete and has been removed.
# The _incorrectly_named_handle_order_summary_actually_list_dishes function is removed.

# Corrected signature and new function for Thirty Nine Miles English Order Summary
async def handle_order_summary_thirty_nine_miles_en(
    function_call_id: str,
    function_name: str, 
    input_data: Dict[str, Any],
    deepgram_service,
    websocket: WebSocket, 
    stream_sid: str,      
    caller_phone: Optional[str],
    call_sid: Optional[str],
    client_id: Optional[str] 
):
    """Handles the order_summary function call for Thirty Nine Miles (English)."""
    # Defensive logging to track every invocation of this critical function.
    logger.info(f"--- ATTEMPTING TO PLACE ORDER --- Function: {function_name}, CallSid: {call_sid}, FunctionCallID: {function_call_id}")
    logger.info(f"Input data: {json.dumps(input_data)}")

    # --- STATE MANAGEMENT GATEKEEPER ---
    if order_manager.is_active(call_sid):
        logger.warning(f"Rejected 'order_summary' call for call_sid: {call_sid} because an item configuration is active.")
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": json.dumps({
                "status": "REJECTED",
                "message_for_agent": "An item is currently being configured. You must use the 'process_order_selection' tool to continue guiding the user."
            })
        }
        await deepgram_service.send_json(response)
        return

    ai_items = input_data.get("items", [])
    ai_total_price = input_data.get("total_price")
    summary_status = input_data.get("summary")

    # --- SINGLE SOURCE OF TRUTH: THE CART ---
    # The AI's item list is IGNORED. We only use items from the order_manager's cart.
    cart_items = order_manager.get_cart(call_sid)
    if cart_items:
        logger.info(f"Using {len(cart_items)} items from the order_manager cart for call_sid: {call_sid} as the source of truth.")
        ai_items = cart_items # Use the cart items, not the AI's potentially stale list
    else:
        logger.warning(f"Cart is empty for call_sid: {call_sid}. The AI provided {len(ai_items)} items, but this may be incorrect if items were added previously.")
        # If the cart is empty, we proceed with what the AI sent, but this is a potential state mismatch.
        if not ai_items:
             response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": "Your order is empty. Please add items to your cart before checking out."}
             raise ValueError("Cart is empty and AI provided no items for order summary.")

    call_id_for_db = call_sid or f"unknown_call_{int(time.time())}"
    
    response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": "An unexpected error occurred while processing your order."}
    final_confirmation_text_for_tts = "" # For "DONE" status, this will be the text Deepgram speaks

    try:
        english_ai_items = []
        for item in ai_items:
            item_name = item.get("name")
            if item_name and is_primarily_english(item_name):
                english_ai_items.append(item)
            else:
                logger.warning(f"Skipping item with non-English or missing name '{item_name}' in order summary for call {call_sid}")
        
        if not english_ai_items and ai_items:
             response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": "No valid English item names found in the order to process."}
             raise ValueError("No valid English item names for order summary.")
        if not english_ai_items: # No items at all
            response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": "No items provided in the order."}
            raise ValueError("No items provided for order summary.")

        internal_db_id = await save_order_details(call_id_for_db, english_ai_items, ai_total_price, summary_status == "DONE")
        logger.info(f"Saved AI order summary (English items) to local DB (id: {internal_db_id}) for call {call_sid}")

        if summary_status != "DONE":
            response_content_payload = {"status": "OK", "order_placed": False, "internal_order_id": internal_db_id, "message_for_agent": "Order items noted. Anything else?"}
            # For "IN PROGRESS", the agent usually continues the conversation.
            # The 'message_for_agent' here is for internal logging or if Deepgram needs a structured reply.
            # Often, for IN_PROGRESS, no explicit FunctionCallResponse content is needed for TTS,
            # as the agent's prompt guides it to continue.
            # However, sending a status is good practice.
        else: # summary_status == "DONE"
            logger.info(f"Order summary DONE for call {call_sid}. Placing order with Thirty Nine Miles (English flow).")
            
            portal_id = None
            if client_id == "LIMF": 
                portal_id = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT
            elif client_id: # Expand this if other client_ids use 39-miles
                from app.utils.constants import get_restaurant_config # Ensure get_restaurant_config is imported or available
                config = get_restaurant_config(client_id)
                portal_id = config.get("PORTAL_ID_TAKEOUT") or config.get("PORTAL_ID_THIRTY_NINE_MILES_TAKEOUT") # Check for specific key
            
            if not portal_id:
                logger.error(f"Cannot place order: Portal ID for Thirty Nine Miles not found for client_id: {client_id}. Call: {call_sid}")
                response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": "Configuration error: Cannot determine the restaurant portal for Thirty Nine Miles."}
                raise ValueError("Portal ID not found for Thirty Nine Miles order placement.")

            # --- Pre-Order Validation Step ---
            missing_items_messages = []
            for item in english_ai_items:
                item_name = item.get("name")
                if not item_name:
                    continue

                item_details_list = await find_dish_by_english_name(portal_id, item_name)
                if not item_details_list:
                    missing_items_messages.append(f"Could not verify item '{item_name}' on the menu.")
                    continue

                item_details = item_details_list[0].get("dish_details", {})
                option_groups = item_details.get("optionGroups", [])
                if not option_groups:
                    continue

                for group in option_groups:
                    # Skip validation for Customized Combo as it has its own logic
                    if "customized combo" in item_name.lower():
                        continue

                    if group.get("isRequired"):
                        group_name_en = _get_english_name(group.get("name", {}))
                        display_group_name = f"'{group_name_en}'" if group_name_en else "a required selection (e.g., protein)"
                        
                        item_options = item.get("options", {})
                        has_selection_for_group = False
                        
                        # --- START: ROBUST VALIDATION LOGIC ---
                        # Special check for fixed-price combo protein selections
                        is_protein_group = _is_protein_group(group_name_en) and "free" not in group_name_en.lower()
                        if "combo #" in item_name.lower() and is_protein_group:
                            if item_options.get("fixed_combo_selections"):
                                has_selection_for_group = True
                        else:
                            # Original validation for all other items and options
                            possible_option_names = {_get_english_name(opt.get("name", {})) for opt in group.get("options", [])}
                            possible_option_names = {name for name in possible_option_names if name}

                            if isinstance(item_options, dict):
                                if group_name_en in item_options:
                                    selected_value = item_options[group_name_en]
                                    if selected_value in possible_option_names:
                                        has_selection_for_group = True
                                    else:
                                        logger.warning(f"Validation mismatch: Selection '{selected_value}' for group '{group_name_en}' is not in the list of possible options: {possible_option_names}")
                            elif isinstance(item_options, list):
                                if any(opt in possible_option_names for opt in item_options):
                                    has_selection_for_group = True
                        # --- END: ROBUST VALIDATION LOGIC ---

                        if not has_selection_for_group:
                            error_msg = f"Order rejected. The item '{item_name}' is missing a selection for {display_group_name}. You must ask the user for their choice."
                            logger.error(f"Validation failed for call {call_sid}: {error_msg}")
                            response_content_payload = {"status": "REJECTED", "order_placed": False, "message_for_agent": error_msg}
                            final_response_to_deepgram = {
                                "type": "FunctionCallResponse", "id": function_call_id, "name": function_name,
                                "content": json.dumps(response_content_payload)
                            }
                            await deepgram_service.send_json(final_response_to_deepgram)
                            return

            processed_pos_items: List[ApiOrderItem] = []
            calculated_item_subtotal = 0.0

            for ai_item in english_ai_items:
                item_name_en = ai_item.get("name")
                quantity = ai_item.get("quantity", 0)
                if not item_name_en or quantity <= 0: continue

                found_pos_dishes = await find_dish_by_english_name(portal_id, item_name_en)
                
                if not found_pos_dishes:
                    missing_items_messages.append(f"Item '{item_name_en}' was not found on the menu.")
                    continue

                # Find the first valid English match from the search results.
                pos_dish_info = next((d for d in found_pos_dishes if d.get("dish_details", {}).get("name", {}).get("en") and is_primarily_english(d.get("dish_details", {}).get("name", {}).get("en"))), None)
                
                if not pos_dish_info:
                    missing_items_messages.append(f"Could not confirm a valid English menu item for '{item_name_en}'.")
                    continue
                
                pos_dish_details = pos_dish_info.get("dish_details", {})
                menu_item_name_en_from_pos = pos_dish_details.get("name", {}).get("en", item_name_en)
                
                # Use TextStore for name, ensuring English is primary
                name_obj_for_pos = TextStore(en=menu_item_name_en_from_pos, zh=pos_dish_details.get("name", {}).get("zh"))
                
                # In the new "add to cart" model, the item from the cart is the source of truth.
                # We no longer need to enrich it by calling the combo_order_manager again.
                # The options should already be perfectly structured in ai_item["options"].

                product_options_for_pos = []
                ai_options = ai_item.get("options", {})
                pos_options = copy.deepcopy(ai_options) if ai_options else {}
                item_price = 0.0
                item_weight = ai_item.get("weight")

                all_option_groups_from_menu = pos_dish_details.get("optionGroups", [])

                # Case 1: Combo item with a dictionary of selections
                if "customized combo" in item_name_en.lower() and isinstance(ai_options, dict) and ai_options:
                    logger.info(f"Processing options for combo item: {item_name_en}")
                    
                    # This is the new, more robust logic for combo processing.
                    product_options_for_pos, item_price = await process_customized_combo_options(
                        item_name_en, 
                        pos_options, 
                        all_option_groups_from_menu
                    )
                    
                    logger.info(f"Reconstructed {len(product_options_for_pos)} option groups for combo. Final calculated price: {item_price}")
                
                # Case 2: Fixed-price combo item
                elif "combo" in item_name_en.lower() and pos_dish_details.get("price", 0) > 0:
                    logger.info(f"Processing options for fixed-price combo item: {item_name_en}")
                    reconstructed_option_groups = []
                    item_price = float(pos_dish_details.get("price", 0.0))

                    protein_option_groups_from_menu = []
                    for g in pos_dish_details.get("optionGroups", []):
                        name = g.get("name", {})
                        if not name:
                            continue
                        
                        en_name = name.get("en")
                        zh_name = name.get("zh")
                        
                        # Check English name first
                        if isinstance(en_name, str) and "choose one" in en_name.lower():
                            protein_option_groups_from_menu.append(g)
                            continue
                            
                        # If not in English, check Chinese name
                        if isinstance(zh_name, str) and "choose one" in zh_name.lower():
                            protein_option_groups_from_menu.append(g)

                    if isinstance(pos_options, dict):
                        # Handle the special list of protein selections first
                        if 'fixed_combo_selections' in pos_options:
                            protein_selections = pos_options.pop('fixed_combo_selections')
                            
                            all_possible_protein_options = []
                            for group in protein_option_groups_from_menu:
                                all_possible_protein_options.extend(group.get("options", []))

                            matched_protein_options = []
                            for selected_protein in protein_selections:
                                found_option = next((opt for opt in all_possible_protein_options if _get_english_name(opt.get("name", {})) == selected_protein), None)
                                if found_option:
                                    matched_protein_options.append(MenuProductOption(**found_option))
                                    item_price += found_option.get("adjustPrice", 0.0)
                                else:
                                    logger.warning(f"Could not find menu option for fixed combo protein: {selected_protein}")
                            
                            if matched_protein_options:
                                reconstructed_option_groups.append(MenuProductOptionGroup(
                                    name=TextStore(en="CHOOSE ONE", zh="CHOOSE ONE"),
                                    options=matched_protein_options
                                ))

                        # Now, process the rest of the options (flavor, spice, etc.)
                        for selection_key, selected_values in pos_options.items():
                            target_group = next((g for g in all_option_groups_from_menu if _get_english_name(g.get("name", {})).strip().lower() == selection_key.strip().lower()), None)
                            
                            if not target_group:
                                logger.warning(f"Could not find any option group named '{selection_key}' for combo '{item_name_en}'.")
                                continue

                            if not isinstance(selected_values, list):
                                selected_values = [selected_values]

                            all_possible_options = target_group.get("options", [])
                            
                            matched_options_for_group = []
                            for selected_value in selected_values:
                                selected_value_name = selected_value if isinstance(selected_value, str) else selected_value.get("name")
                                if not selected_value_name:
                                    continue

                                found_option = next((opt for opt in all_possible_options if _get_english_name(opt.get("name", {})) == selected_value_name), None)
                                
                                if found_option:
                                    matched_options_for_group.append(MenuProductOption(**found_option))
                                    item_price += found_option.get("adjustPrice", 0.0)
                                else:
                                    logger.warning(f"Could not find selected option '{selected_value_name}' in the '{selection_key}' group.")
                            
                            if matched_options_for_group:
                                reconstructed_option_groups.append(MenuProductOptionGroup(
                                    name=TextStore(**target_group.get("name", {})),
                                    options=matched_options_for_group
                                ))

                    product_options_for_pos = reconstructed_option_groups
                    logger.info(f"Reconstructed {len(product_options_for_pos)} option groups for fixed-price combo. Final calculated price: {item_price}")

                # Case 3: Non-combo item with a dictionary of {group_name: selection}
                elif isinstance(ai_options, dict) and ai_options:
                    logger.info(f"Processing options for non-combo item with dict options: {item_name_en}")
                    reconstructed_option_groups = []
                    item_price = float(pos_dish_details.get("price", 0.0)) # Start with base price

                    for selection_key, selected_value in ai_options.items():
                        target_group = next((g for g in all_option_groups_from_menu if _get_english_name(g.get("name", {})).strip().lower() == selection_key.strip().lower()), None)
                        
                        if not target_group:
                            logger.warning(f"Could not find option group '{selection_key}' for item '{item_name_en}'.")
                            continue

                        found_option = next((opt for opt in target_group.get("options", []) if _get_english_name(opt.get("name", {})).strip().lower() == str(selected_value).strip().lower()), None)

                        if found_option:
                            # Ensure the names sent to the POS have the English fallback applied.
                            group_name_for_pos = TextStore(en=_get_english_name(target_group.get("name", {})), zh=target_group.get("name", {}).get("zh"))
                            option_name_for_pos = TextStore(en=_get_english_name(found_option.get("name", {})), zh=found_option.get("name", {}).get("zh"))
                            
                            # Create a copy of the found option to avoid modifying the original menu data
                            option_for_pos_data = found_option.copy()
                            option_for_pos_data['name'] = option_name_for_pos

                            reconstructed_option_groups.append(MenuProductOptionGroup(
                                name=group_name_for_pos,
                                options=[MenuProductOption(**option_for_pos_data)]
                            ))
                            item_price += found_option.get("adjustPrice", 0.0)
                        else:
                            logger.warning(f"Could not find selected option '{selected_value}' in group '{selection_key}' for item '{item_name_en}'.")
                    
                    product_options_for_pos = reconstructed_option_groups
                    logger.info(f"Reconstructed {len(product_options_for_pos)} option groups for non-combo dict. Final price: {item_price}")

                # Case 4: No options or item is not a combo
                else:
                    if pos_options:
                        logger.warning(f"Item '{item_name_en}' has options in an unhandled format: {type(pos_options)}. Skipping.")
                    item_price = float(pos_dish_details.get("price", 0.0))

                processed_pos_items.append(ApiOrderItem(
                    name=name_obj_for_pos,
                    category_id=str(pos_dish_info.get("category_id")),
                    product_id=str(pos_dish_info.get("dish_id")),
                    quantity=quantity,
                    weight=item_weight,
                    final_price=item_price,
                    product_options=product_options_for_pos
                ))
                calculated_item_subtotal += item_price * quantity
            
            if missing_items_messages:
                msg = " ".join(missing_items_messages) + " Cannot complete the order."
                logger.error(f"Order placement failed (missing/unconfirmed items) for call {call_sid}: {msg}")
                response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": msg}
                raise ValueError(msg)

            if not processed_pos_items:
                response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": "No valid items to place in the order."}
                raise ValueError("No valid items for POS order placement.")

            # TODO: Get tax rate from config (e.g., get_restaurant_config(client_id).get("TAX_RATE", 0.05))
            tax_rate_from_config = 0.08 # Example, make this configurable
            tax_amount = round(calculated_item_subtotal * tax_rate_from_config, 2)
            final_payment_amount = round(calculated_item_subtotal + tax_amount, 2)
            
            api_order_to_pos = ApiOrderDto(
                portal_id=portal_id,
                customer_phone=caller_phone or "N/A",
                user_name=caller_phone or "AI Voice Order",
                lang_code=LangCode.EN,
                order_items=processed_pos_items,
                item_subtotal=round(calculated_item_subtotal, 2),
                tax=tax_amount,
                tips=0.0,
                bag_fee=0.0,
                final_payment=final_payment_amount
            )
            logger.info(f"Attempting to place English order with 39Miles. Payload: {api_order_to_pos.model_dump_json(indent=2)}. Call: {call_sid}")
            
            # from app.utils.thirty_nine_miles import add_order as add_thirty_nine_miles_order (already imported)
            tnm_pos_response = await add_thirty_nine_miles_order(api_order_to_pos)
            logger.info(f"39Miles add_order response (English flow): {tnm_pos_response}. Call: {call_sid}")

            if tnm_pos_response and tnm_pos_response.get("success"):
                external_pos_order_id = tnm_pos_response.get("data")
                if isinstance(external_pos_order_id, str) and external_pos_order_id:
                    logger.info(f"Successfully placed English order with 39Miles. External Order ID: {external_pos_order_id}. Call: {call_sid}")
                    
                    # --- START: ENHANCED TTS CONFIRMATION ---
                    summary_parts = []
                    for item in english_ai_items:
                        item_name = item.get("name")
                        options = item.get("options", {})
                        
                        options_list = []
                        if isinstance(options, dict):
                            # Handle fixed combo proteins first
                            if 'fixed_combo_selections' in options:
                                options_list.extend([_clean_option_for_tts(opt) for opt in options.get('fixed_combo_selections', [])])
                            
                            # Handle all other options
                            for key, value in options.items():
                                if key.lower() == 'proteins' and isinstance(value, list):
                                    # Special handling for customized combo proteins
                                    protein_details = []
                                    for protein in value:
                                        if isinstance(protein, dict):
                                            name = protein.get('name', '')
                                            size = protein.get('size', '')
                                            protein_details.append(f"{size} of {name}")
                                    if protein_details:
                                        options_list.append(", ".join(protein_details))
                                elif key.lower() != 'fixed_combo_selections':
                                    # General handling for other options (flavor, spice, etc.)
                                    if isinstance(value, str):
                                        options_list.append(_clean_option_for_tts(value))
                                    elif value is not None:
                                        # Fallback for unexpected types, prevents crash
                                        options_list.append(str(value))
                        
                        if options_list:
                            summary_parts.append(f"{item_name} with {', '.join(options_list)}")
                        else:
                            summary_parts.append(item_name)
                    
                    order_summary_str = "; ".join(summary_parts)
                    final_confirmation_text_for_tts = f"Okay, your order {external_pos_order_id} is confirmed. It includes {order_summary_str}, for a total of ${final_payment_amount:.2f}. It will be ready for pickup shortly. Thank you for your call, goodbye!"
                    # --- END: ENHANCED TTS CONFIRMATION ---

                    response_content_payload = {"status": "OK", "order_placed": True, "external_order_id": external_pos_order_id, "internal_order_id": internal_db_id, "message_for_agent": final_confirmation_text_for_tts}
                    
                    if deepgram_service:
                        deepgram_service.is_final_confirmation_sent = True

                    if caller_phone:
                        dishes_sms_str = []
                        for item in english_ai_items:
                            item_str = f"{item.get('quantity', 1)}x {item.get('name', 'Item')}"
                            
                            options_desc = []
                            if isinstance(item.get("options"), dict):
                                selections = item.get("options", {})
                                
                                # Handle customized combo proteins
                                if 'proteins' in selections:
                                    protein_details_list = []
                                    for p in selections.get("proteins", []):
                                        if isinstance(p, dict):
                                            protein_name = p.get('name')
                                            protein_size = p.get('size', '1 lb')
                                            if protein_name:
                                                protein_details_list.append(f"{protein_size} {protein_name}")
                                    if protein_details_list:
                                        options_desc.append(f"Proteins: {', '.join(protein_details_list)}")
                                
                                # Handle fixed-price combo selections
                                if 'fixed_combo_selections' in selections:
                                    selections_list = selections.get("fixed_combo_selections", [])
                                    if selections_list:
                                        options_desc.append(f"Selections: {', '.join(selections_list)}")

                                # Handle all other options, ensuring not to re-add proteins
                                for key, value in selections.items():
                                    if key.lower() not in ["proteins", "fixed_combo_selections"]:
                                        # Clean up the key for display
                                        formatted_key = key.replace("_", " ").title()
                                        if isinstance(value, list):
                                            # Join list items for a clean string
                                            value_str = ', '.join(map(str, value))
                                            options_desc.append(f"{formatted_key}: {value_str}")
                                        else:
                                            options_desc.append(f"{formatted_key}: {value}")

                            elif isinstance(item.get("options"), list):
                                # Handle simple lists of options for non-combo items
                                options_desc.extend(item.get("options", []))
                            
                            if options_desc:
                                item_str += f" ({'; '.join(options_desc)})"
                            
                            dishes_sms_str.append(item_str)
                        
                        dishes_final_str = "; ".join(dishes_sms_str)
                        from app.utils.constants import get_restaurant_config
                        sms_body_en = f"Your order {external_pos_order_id} with {get_restaurant_config(client_id).get('RESTAURANT_NAME', 'us')} is confirmed! Items: {dishes_final_str}. Total: ${api_order_to_pos.final_payment:.2f}. Thank you!"
                        
                        import functools
                        loop = asyncio.get_event_loop()
                        sms_task_en = functools.partial(send_sms, caller_phone, sms_body_en, client_id) 
                        loop.run_in_executor(None, sms_task_en)
                        logger.info(f"Scheduled English SMS confirmation for {caller_phone}")
                    
                    # Clear the cart after successful order placement
                    clear_cart(call_sid)
                else: 
                    final_confirmation_text_for_tts = "Your order has been submitted, but we couldn't get a confirmation number at this moment. Please check with us shortly."
                    response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": final_confirmation_text_for_tts}
                    if deepgram_service:
                        deepgram_service.is_final_confirmation_sent = True
            else: 
                err_msg_from_pos = tnm_pos_response.get("message", "an unknown issue") if tnm_pos_response else "no response from the POS"
                final_confirmation_text_for_tts = f"We encountered an issue submitting your order: {err_msg_from_pos}. Please try again or call us directly."
                response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": final_confirmation_text_for_tts}
                if deepgram_service:
                    deepgram_service.is_final_confirmation_sent = True
        
        # For "DONE" status, the content sent back to Deepgram should be the text for TTS.
        # For "IN PROGRESS", it should be the message for the agent.
        if summary_status == "DONE" and final_confirmation_text_for_tts:
            response_output_content = final_confirmation_text_for_tts
        else:
            response_output_content = response_content_payload.get("message_for_agent", "An error occurred processing the order.")

    except ValueError as ve: # Catch specific ValueErrors raised for flow control
        logger.error(f"Value error processing order summary (English): {ve} for call {call_sid}")
        if "message_for_agent" not in response_content_payload or not response_content_payload["message_for_agent"]:
            response_content_payload["message_for_agent"] = str(ve)
        response_output_content = response_content_payload.get("message_for_agent", "An error occurred.")


    except Exception as e:
        logger.error(f"Error processing order summary (English): {e} for call {call_sid}", exc_info=True)
        response_content_payload = {"status": "ERROR", "order_placed": False, "message_for_agent": "An internal error occurred while processing your order. Please try again."}
        response_output_content = response_content_payload.get("message_for_agent", "An internal error occurred.")

    # Determine the content of the 'output' field. It should always be a simple string
    # that the agent can use to form its next response.
    output_content_for_deepgram = response_output_content

    final_response_to_deepgram = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": output_content_for_deepgram
    }
    await deepgram_service.send_json(final_response_to_deepgram)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id}): {output_content_for_deepgram}")


async def process_customized_combo_options(
    item_name_en: str,
    pos_options: Dict[str, Any],
    all_option_groups_from_menu: List[Dict[str, Any]]
) -> (List[MenuProductOptionGroup], float):
    """
    Processes the options for a customized combo, handling both proteins and other selections.
    Returns a tuple of (reconstructed_option_groups, total_item_price).
    """
    reconstructed_option_groups = []
    item_price = 0.0

    # Handle proteins first
    if 'proteins' in pos_options:
        protein_selections = pos_options.pop('proteins')
        protein_groups, protein_price = await process_combo_proteins(protein_selections, all_option_groups_from_menu)
        reconstructed_option_groups.extend(protein_groups)
        item_price += protein_price

    # Handle other options (flavor, spice, extras)
    for selection_key, selected_values in pos_options.items():
        other_groups, other_price = await process_other_combo_options(
            item_name_en,
            selection_key,
            selected_values,
            all_option_groups_from_menu
        )
        reconstructed_option_groups.extend(other_groups)
        item_price += other_price
        
    return reconstructed_option_groups, item_price

async def process_combo_proteins(
    protein_selections: List[Dict[str, Any]],
    all_option_groups_from_menu: List[Dict[str, Any]]
) -> (List[MenuProductOptionGroup], float):
    """Processes protein selections for a customized combo."""
    reconstructed_groups = []
    total_price = 0.0
    
    all_protein_options_from_menu = []
    protein_option_groups_from_menu = []
    for g in all_option_groups_from_menu:
        name_obj = g.get("name")
        if isinstance(name_obj, dict):
            name_en = name_obj.get("en")
            name_zh = name_obj.get("zh")

            # Determine the effective English name, checking 'zh' field as a fallback
            effective_name_en = ""
            if name_en and is_primarily_english(name_en):
                effective_name_en = name_en
            elif name_zh and is_primarily_english(name_zh):
                effective_name_en = name_zh
            
            # Now, perform the check on the corrected name
            if effective_name_en:
                name_en_lower = effective_name_en.lower()
                if "choose one" in name_en_lower or "half a pound" in name_en_lower:
                    protein_option_groups_from_menu.append(g)

    for group in protein_option_groups_from_menu:
        all_protein_options_from_menu.extend(group.get("options", []))

    matched_protein_options_for_pos = []
    for protein_info in protein_selections:
        protein_name = protein_info.get("name")
        size_str = str(protein_info.get("size", "0")).lower().replace("lbs", "").replace("lb", "").strip()
        try:
            ordered_weight = float(size_str)
        except ValueError:
            logger.warning(f"Invalid weight format for {protein_name}: {protein_info.get('size')}")
            continue
        
        size_key = "0.5 lbs" if ordered_weight < 1.0 else "1 lb"
        pos_protein_name = PROTEIN_NAME_MAPPING.get(size_key, {}).get(protein_name)

        if not pos_protein_name:
            logger.warning(f"Could not find protein '{protein_name}' with size '{size_key}' in the mapping.")
            continue

        # Use the robust _get_english_name helper to handle null 'en' fields
        found_menu_option = next((opt for opt in all_protein_options_from_menu if _get_english_name(opt.get("name", {})) == pos_protein_name), None)
        
        if found_menu_option:
            base_price = found_menu_option.get("adjustPrice", 0.0)
            # Use the robust getter here as well for safety
            menu_opt_name_lower = _get_english_name(found_menu_option.get("name", {})).lower()
            
            base_weight = 1.0 if "half a pound" not in menu_opt_name_lower and "0.5" not in menu_opt_name_lower else 0.5
            
            if base_weight > 0:
                price_for_protein = (ordered_weight / base_weight) * base_price
                total_price += price_for_protein
                logger.info(f"Price for {ordered_weight}lbs of {protein_name}: ${price_for_protein:.2f}")
            
            option_with_weight = MenuProductOption(**found_menu_option)
            option_with_weight.weight = ordered_weight
            matched_protein_options_for_pos.append(option_with_weight)
        else:
            logger.warning(f"Could not find menu option for mapped protein: {pos_protein_name}")

    if matched_protein_options_for_pos and protein_option_groups_from_menu:
        first_protein_group = protein_option_groups_from_menu[0]
        reconstructed_groups.append(MenuProductOptionGroup(
            name=TextStore(**first_protein_group.get("name", {})),
            options=matched_protein_options_for_pos
        ))
        
    return reconstructed_groups, total_price

async def process_other_combo_options(
    item_name_en: str,
    selection_key: str,
    selected_values: Any,
    all_option_groups_from_menu: List[Dict[str, Any]]
) -> (List[MenuProductOptionGroup], float):
    """Processes non-protein selections for a customized combo."""
    reconstructed_groups = []
    total_price = 0.0

    target_group = next((g for g in all_option_groups_from_menu if _get_english_name(g.get("name", {})).strip().lower() == selection_key.strip().lower()), None)
    if not target_group:
        target_group = next((g for g in all_option_groups_from_menu if _get_english_name(g.get("name", {})).strip() == selection_key.strip()), None)

    if not target_group:
        logger.warning(f"Could not find option group '{selection_key}' for combo '{item_name_en}'.")
        return [], 0.0

    if not isinstance(selected_values, list):
        selected_values = [selected_values]

    matched_options_for_group = []
    for selected_value in selected_values:
        # Use the robust _get_english_name to handle inconsistent data
        found_option = next((opt for opt in target_group.get("options", []) if _get_english_name(opt.get("name", {})).strip().lower() == str(selected_value).strip().lower()), None)
        if found_option:
            matched_options_for_group.append(MenuProductOption(**found_option))
            total_price += found_option.get("adjustPrice", 0.0)
        else:
            logger.warning(f"Could not find selected option '{selected_value}' in group '{selection_key}'.")

    if matched_options_for_group:
        reconstructed_groups.append(MenuProductOptionGroup(
            name=TextStore(**target_group.get("name", {})),
            options=matched_options_for_group
        ))
        
    return reconstructed_groups, total_price


# The deprecated handle_order_summary_Square function.

# # This function is deprecated and related to the Square integration, which is not in use.
# # It will be left as-is but is not expected to be called.
# async def handle_order_summary_Square(
#     function_call_id: str,
#     input_data: Dict[str, Any],
#     deepgram_service,
#     websocket: WebSocket,
#     stream_sid: str,
#     caller_phone: Optional[str],
#     call_sid: Optional[str]
# ):
#     """
#     Handle order summary function call and send response back to Deepgram
    
#     Args:
#         function_call_id: The unique ID for this function call
#         input_data: The input data for the function
#         deepgram_service: The Deepgram service instance
#         websocket: WebSocket connection to Twilio
#         stream_sid: The Twilio stream SID
#         caller_phone: The caller's phone number
#         call_sid: The Twilio call SID
#     """
#     logger.info(f"Handling order_summary function call (ID: {function_call_id})")
#     logger.info(f"Input data: {json.dumps(input_data)}")

#     # Extract order details
#     items = input_data.get("items", [])
#     total_price = input_data.get("total_price")
#     status = input_data.get("status")

#     if not items or total_price is None:
#         logger.error("Missing items or total_price in order_summary input")
#         # Optionally send an error response back to Deepgram
#         error_response = {
#             "type": "FunctionCallResponse",
#             "function_call_id": function_call_id,
#             "output": {"status": "error", "message": "Missing order details"}
#         }
#         await deepgram_service.send_json(error_response)
#         return

#     try:
#         # Determine order status
#         summary_status = input_data.get("summary", "IN PROGRESS")
#         logger.info(f"DEBUG: Raw summary status from input: '{summary_status}' (Type: {type(summary_status)})")
#         is_complete_order = summary_status == "DONE"
#         logger.info(f"DEBUG: Calculated is_complete_order based on summary: {is_complete_order}")

#         # Generate confirmation message text
#         confirmation_text = f"Okay, I have {len(items)} items for a total of ${total_price:.2f}."
#         if is_complete_order:
#             confirmation_text += " Your order will be ready for pickup shortly."
#         else:
#             confirmation_text += " Is there anything else?"

#         logger.info(f"Generated confirmation text: {confirmation_text}")

#         # --- Save order and utterances ---
#         logger.info(f"DEBUG: Saving order with is_complete_order = {is_complete_order}")
#         order_id = await save_order_details(call_sid, items, total_price, is_complete_order)
#         await save_utterance(call_sid, "assistant", confirmation_text)
#         logger.info(f"Saved order {order_id} for call {call_sid}")
        
#         # --- Handle Call Completion with Mark Event ---
#         logger.info(f"DEBUG: Checking if is_complete_order is True: {is_complete_order}")
#         if is_complete_order:
#             logger.info(f"Order is complete for call {call_sid}. Processing Square order and payment.")

#             payment_status = "PENDING" # Default status
#             square_order_id = None
#             square_payment_id = None

#             # try:
#             #     # ---> USE ORIGINAL SQUARE LOGIC HERE <--- #
#             #     logger.info(f"Creating order in Square with items: {items}")

#             #     # Get test payment method ID for Square sandbox
#             #     test_payment_method_id = settings.SQUARE_TEST_NONCE

#             #     # Place order via Square - Remove idempotency key as it's not expected by the function
#             #     result = await test_create_order_endpoint(items)
#             #     logger.info(f"Square Order API response: {result}")

#             #     # Add defensive checks for result structure
#             #     if result and isinstance(result, dict) and "order" in result:
#             #         square_order_id = result["order"]["id"]
#             #         # Ensure amount is integer (cents)
#             #         current_order_total = result["order"].get("total_money", {}).get("amount")

#             #         logger.info(f"Square order created successfully! Order ID: {square_order_id}, Total: {current_order_total}")

#             #         if square_order_id and current_order_total is not None:
#             #             # Process payment via Square
#             #             logger.info(f"Processing Square payment for order {square_order_id}, amount: {current_order_total}")
#             #             payment_result = await test_payment_processing(
#             #                 square_order_id,
#             #                 current_order_total,
#             #                 test_payment_method_id
#             #             )
#             #             logger.info(f"Square Payment result: {payment_result}")

#             #             if payment_result and isinstance(payment_result, dict):
#             #                 # Check common Square payment statuses
#             #                 if payment_result.get("status") == "COMPLETED":
#             #                     square_payment_id = payment_result.get("id")
#             #                     payment_status = "PAID"
#             #                     logger.info(f"Square payment successful! Payment ID: {square_payment_id}")
#             #                 elif payment_result.get("status") == "FAILED":
#             #                     payment_status = "FAILED"
#             #                     logger.error(f"Square payment failed! Result: {payment_result}")
#             #                 else:
#             #                     payment_status = payment_result.get("status", "UNKNOWN_STATUS") # Capture other statuses
#             #                     logger.warning(f"Square payment status: {payment_status}. Result: {payment_result}")
#             #             else:
#             #                 payment_status = "FAILED"
#             #                 logger.error("Square payment processing failed or returned unexpected result.")
#             #         else:
#             #             payment_status = "FAILED" # Cannot proceed without order ID or total
#             #             logger.error(f"Cannot process payment. Missing Square order ID ({square_order_id}) or total amount ({current_order_total}).")
#             #     else:
#             #         payment_status = "ORDER_FAILED"
#             #         logger.error(f"Failed to create order in Square or response structure invalid. Result: {result}")

#             # except Exception as sq_err:
#             #     logger.error(f"Error during Square processing for call {call_sid}: {sq_err}", exc_info=True)
#             #     payment_status = "ERROR"
#             #     # Continue with confirmation even if Square fails

#             # # TODO: Optionally update the database record with square_order_id and payment_status
#             # # await update_order_with_square_details(order_id, square_order_id, payment_status)

#             # --- Proceed with User Confirmation (TTS/SMS) ---

#             # 1. Generate final confirmation text (including pickup message)
#             # Use the original confirmation text and add the pickup part
#             final_confirmation_text = f"{confirmation_text} This is confirmation text."
#             logger.info(f"Generated final confirmation text: {final_confirmation_text}")

#             # deepgram_service.is_final_confirmation = True
#             # logger.info("Set final confirmation flag in Deepgram service")


#             # Send the final confirmation message text back to Deepgram
#             # Deepgram Agent will handle TTS generation and send audio back
#             # For order_summary, the output is often a simple string for TTS,
#             # but if it were a complex object, it should also be json.dumps'd.
#             # In this case, final_confirmation_text is already a string.
#             response = {
#                 "type": "FunctionCallResponse",
#                 "function_call_id": function_call_id,
#                 "output": final_confirmation_text # This is already a string
#             }
#             logger.info(f"Sending function call response to trigger TTS: {json.dumps(response)}")
#             await deepgram_service.send_json(response)

#             # --- SMS Sending (already handled asynchronously) ---
#             if caller_phone:
#                 # Use a simple SMS format
#                 items_text = ", ".join([f"{i['quantity']}x {i['name']}" for i in items]) # Recreate items_text if needed
                
#                 # Determine which Order ID to display
#                 display_order_id = square_order_id if square_order_id else order_id
                
#                 # Use the display_order_id in the SMS body
#                 sms_body = f"Your Servio order ({display_order_id}) is confirmed! Items: {items_text}. Total: ${total_price:.2f}. It will be ready shortly. Status: {payment_status}"
                
#                 # Get the current event loop
#                 loop = asyncio.get_running_loop()
                
#                 # Schedule the synchronous send_sms function in the default executor
#                 # Note: We don't await the result here, just schedule it (fire-and-forget)
#                 # loop.run_in_executor(None, send_sms, caller_phone, sms_body)
#                 # Use functools.partial to pass arguments correctly to the executor
#                 import functools
#                 sms_task = functools.partial(send_sms, caller_phone, sms_body)
#                 loop.run_in_executor(None, sms_task)

#                 logger.info(f"Scheduled SMS confirmation via executor for {caller_phone} (Square Status: {payment_status})")
#             else:
#                 logger.warning(f"Cannot send SMS confirmation, caller phone is missing for call {call_sid}")

#         # --- Handle Non-Complete Order ---
#         else:
#             logger.info(f"DEBUG: Entered ELSE block for non-complete order (is_complete_order={is_complete_order})")
#             # If order is not complete, TTS was already sent by handle_transcript
#             logger.info(f"Order not complete for call {call_sid}. TTS already sent by handle_transcript.") # Updated log message
#             # response_payload = {
#             #     "type": "FunctionCallResponse",
#             #     "function_call_id": function_call_id,
#             #     "output": {"status": "processed_intermediately"}, # Send simple status, not full text
#             # }
#             # await deepgram_service.send_json(response_payload)
#             # logger.info(f"Sent FunctionCallResponse status to Deepgram for call {call_sid}")

#     except ValueError as ve:
#         logger.error(f"Value error processing order summary: {ve}")
#         await deepgram_service.send_json({
#             "type": "FunctionCallResponse",
#             "function_call_id": function_call_id,
#             "output": json.dumps({"status": "error", "message": str(ve)}) # Serialize error output
#         })
#     except Exception as e:
#         logger.error(f"Error processing order summary: {e}")
#         logger.error(traceback.format_exc())
#         # Send generic error response to Deepgram
#         await deepgram_service.send_json({
#             "type": "FunctionCallResponse",
#             "function_call_id": function_call_id,
#             "output": json.dumps({"status": "error", "message": "Internal server error"}) # Serialize error output
#         })
