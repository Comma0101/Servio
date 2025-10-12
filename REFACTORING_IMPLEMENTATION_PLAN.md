# Order Flow Redesign: Implementation Plan

This document outlines the specific code changes required to refactor the order flow system. The goal is to replace the ambiguous `order_summary` tool with two new, explicit tools: `finalize_current_item` and `place_order`, and to fix the critical state leakage bug.

**Status**: NOT YET IMPLEMENTED - This plan describes changes that need to be made to the codebase.

---

## Phase 1: Critical Safety Fix - State Management

### File: `app/api/websocket.py`

**Objective:** Add the missing call to clear the combo order manager's state when a call ends to prevent state leakage.

**Location:** Function `cleanup_call_data()` around line 60-75

**Modification:**

```python
------- SEARCH
    if redis_client.exists(call_sid):
        redis_client.delete(call_sid)
        logger.info(f"Removed caller info for CallSid: {call_sid} from Redis")
=======
    if redis_client.exists(call_sid):
        redis_client.delete(call_sid)
        logger.info(f"Removed caller info for CallSid: {call_sid} from Redis")

    # --- START: CRITICAL STATE CLEANUP ---
    from app.handlers.combo_order_manager import combo_order_manager
    from app.handlers.order_manager import order_manager

    combo_order_manager.clear_order(call_sid)
    order_manager.clear_cart(call_sid)
    logger.info(f"Cleared combo and order manager state for CallSid: {call_sid}")
    # --- END: CRITICAL STATE CLEANUP ---
+++++++ REPLACE
```

---

## Phase 2: New Tool Architecture

### Step 1: Define New Tool Schemas

#### File: `app/handlers/common_tool_defs.py`

**Objective:** Add the schemas for the new `finalize_current_item` and `place_order` tools. The old `ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI` will be removed in Phase 5 after testing.

**Location:** After line 300 in the English Agent section

**Modification:**

```python
------- SEARCH
# --- Tool Schemas for English Agent (OpenAI/Deepgram compatible) ---

ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
=======
# --- Tool Schemas for English Agent (OpenAI/Deepgram compatible) ---

FINALIZE_CURRENT_ITEM_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "finalize_current_item",
    "description": "Finalizes the current item being configured and adds it to the cart. Call this tool after the user confirms their selections for an item are complete. This replaces the 'order_summary' with 'IN PROGRESS' pattern.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": []
    }
}

PLACE_ORDER_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "place_order",
    "description": "Places the entire order with the restaurant's POS system. Call this tool ONLY when the customer explicitly states they are finished ordering (e.g., 'that's all', 'place the order'). This replaces the 'order_summary' with 'DONE' pattern.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": []
    }
}

# DEPRECATED: Will be removed in Phase 5
ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI: Dict[str, Any] = {
+++++++ REPLACE
```

### Step 2: Implement New Tool Handlers

#### File: `app/handlers/english_tool_logic.py`

**Objective:** Add the handler functions for the new tools. This is the most complex part as it requires migrating ~500 lines of logic from `handle_order_summary_thirty_nine_miles_en`.

**Location:** Add new functions around line 1150 (before the old handler)

**Part A - Add `handle_finalize_current_item`:**

```python
async def handle_finalize_current_item(
    function_call_id: str,
    function_name: str,
    deepgram_service,
    call_sid: Optional[str]
):
    """
    Finalizes the current active item (standard or combo) and adds it to the main cart.
    This replaces the 'order_summary' with summary='IN PROGRESS' pattern.
    """
    logger.info(f"Handling {function_name} for call_sid: {call_sid}")

    response_payload = {}
    item_added = None

    # Check for active combo first
    if combo_order_manager.is_active(call_sid):
        item_added = combo_order_manager.finalize_and_get_combo(call_sid)
        if item_added:
            order_manager.add_item_to_cart(
                call_sid,
                item_added.get("name"),
                item_added.get("quantity", 1),
                item_added.get("options", {})
            )
            logger.info(f"Finalized and moved combo '{item_added.get('name')}' to cart for call {call_sid}.")
            response_payload["status"] = "ITEM_ADDED"
            response_payload["message_for_agent"] = f"Okay, I've added the {item_added.get('name')} to your order. Anything else?"
        else:
            logger.error(f"Failed to finalize active combo for call {call_sid}.")
            response_payload["status"] = "ERROR"
            response_payload["message_for_agent"] = "I'm sorry, there was an error finalizing that item. Let's try again."

    # Check for active standard item
    elif order_manager.is_item_active(call_sid):
        item_added = order_manager.finalize_item(call_sid)
        if item_added:
            order_manager.add_item_to_cart(
                call_sid,
                item_added.get("name"),
                item_added.get("quantity", 1),
                item_added.get("options", {})
            )
            logger.info(f"Finalized standard item '{item_added.get('name')}' and moved to cart for call {call_sid}.")
            response_payload["status"] = "ITEM_ADDED"
            response_payload["message_for_agent"] = f"Okay, I've added the {item_added.get('name')} to your order. Anything else?"
        else:
            logger.error(f"Failed to finalize active standard item for call {call_sid}.")
            response_payload["status"] = "ERROR"
            response_payload["message_for_agent"] = "I'm sorry, there was an error finalizing that item. Let's try again."

    # No active item
    else:
        logger.warning(f"No active item to finalize for call {call_sid}.")
        response_payload["status"] = "NO_ACTIVE_ITEM"
        response_payload["message_for_agent"] = "I'm sorry, there was no active item to finalize. What would you like to order?"

    # Include current cart in response
    response_payload["current_cart"] = order_manager.get_cart(call_sid)

    # Clean response for TTS
    cleaned_response = clean_response_for_tts(response_payload)
    message_for_agent = cleaned_response.get("message_for_agent", "Item added to your order.")

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": message_for_agent
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name} (ID: {function_call_id})")
```

**Part B - Add `handle_place_order` (Migrated from old handler):**

Note: This function contains the critical order placement logic from lines 1200-1700+ of the old `handle_order_summary_thirty_nine_miles_en` function. The full implementation is ~500 lines and includes:

- Duplicate order prevention
- Cart validation
- Family Combo special validation (3 proteins, 5 free items, crab selection)
- Protein mapping using PROTEIN_NAME_MAPPING
- Option group reconstruction
- POS payload building with ApiOrderDto
- Tax calculation
- 39Miles API call
- SMS confirmation
- Error handling

```python
async def handle_place_order(
    function_call_id: str,
    function_name: str,
    deepgram_service,
    websocket: WebSocket,
    stream_sid: str,
    caller_phone: Optional[str],
    call_sid: Optional[str],
    client_id: Optional[str]
):
    """
    Validates the cart and places the final order with the Thirty-Nine Miles POS system.
    This replaces the 'order_summary' with summary='DONE' pattern.

    This function contains migrated logic from handle_order_summary_thirty_nine_miles_en.
    """
    # --- DUPLICATE ORDER PREVENTION ---
    if await has_order_been_placed(call_sid):
        logger.warning(f"Rejected duplicate 'place_order' call for {call_sid}.")
        response_content = "The order has already been placed. The call should now be ending."
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": response_content
        }
        await deepgram_service.send_json(response)
        return

    # Get cart items (single source of truth)
    cart_items = order_manager.get_cart(call_sid)

    if not cart_items:
        response_content = "Your cart is empty. Please add items before placing an order."
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": response_content
        }
        await deepgram_service.send_json(response)
        return

    logger.info(f"--- PLACING ORDER --- CallSid: {call_sid}, Items in cart: {len(cart_items)}")

    # Determine portal_id
    portal_id = None
    if client_id == "LIMF":
        portal_id = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT

    if not portal_id:
        logger.error(f"Cannot place order: Portal ID not found for client_id: {client_id}")
        response_content = "Configuration error: Cannot determine the restaurant portal."
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": response_content
        }
        await deepgram_service.send_json(response)
        return

    try:
        # Filter for English items only
        english_ai_items = []
        for item in cart_items:
            item_name = item.get("name")
            if item_name and is_primarily_english(item_name):
                english_ai_items.append(item)
            else:
                logger.warning(f"Skipping non-English item '{item_name}' in cart for call {call_sid}")

        if not english_ai_items:
            response_content = "No valid English items found in the cart to process."
            raise ValueError("No valid English items for order placement.")

        # Save order to internal DB
        internal_db_id = await save_order_details(call_sid, english_ai_items, None, True)
        logger.info(f"Saved order to DB (id: {internal_db_id}) for call {call_sid}")

        # --- PRE-ORDER VALIDATION ---
        # This section validates all items against the menu and checks required options
        # Special handling for Family Combo with its 3 proteins, 5 free items, and crab requirements
        # (Full validation logic from original function lines ~1300-1450)

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

            # Family Combo validation
            if "family combo" in item_name.lower():
                item_options = item.get("options", {})

                # Check crab
                if not item_options.get("crab"):
                    error_msg = "The Family Combo is missing a crab selection."
                    logger.error(f"Validation failed: {error_msg}")
                    response_content = f"Order rejected. {error_msg} Please specify the crab type."
                    response = {
                        "type": "FunctionCallResponse",
                        "id": function_call_id,
                        "name": function_name,
                        "content": response_content
                    }
                    await deepgram_service.send_json(response)
                    return

                # Check 3 proteins
                proteins = item_options.get("proteins", [])
                if len(proteins) != 3:
                    error_msg = f"The Family Combo requires 3 protein selections, but {len(proteins)} were found."
                    logger.error(f"Validation failed: {error_msg}")
                    response_content = f"Order rejected. {error_msg}"
                    response = {
                        "type": "FunctionCallResponse",
                        "id": function_call_id,
                        "name": function_name,
                        "content": response_content
                    }
                    await deepgram_service.send_json(response)
                    return

                # Check 5 free items
                free_items = item_options.get("free_items", [])
                if len(free_items) != 5:
                    error_msg = f"The Family Combo requires 5 free item selections, but {len(free_items)} were found."
                    logger.error(f"Validation failed: {error_msg}")
                    response_content = f"Order rejected. {error_msg}"
                    response = {
                        "type": "FunctionCallResponse",
                        "id": function_call_id,
                        "name": function_name,
                        "content": response_content
                    }
                    await deepgram_service.send_json(response)
                    return

            # Standard required options validation
            # (Rest of validation logic from original function)

        # --- BUILD POS PAYLOAD ---
        # This section reconstructs the order items with proper option group structure
        # (Full payload building logic from original function lines ~1450-1650)

        processed_pos_items: List[ApiOrderItem] = []
        calculated_item_subtotal = 0.0

        for ai_item in english_ai_items:
            item_name_en = ai_item.get("name")
            quantity = ai_item.get("quantity", 0)

            if not item_name_en or quantity <= 0:
                continue

            found_pos_dishes = await find_dish_by_english_name(portal_id, item_name_en)
            if not found_pos_dishes:
                continue

            pos_dish_info = found_pos_dishes[0]
            pos_dish_details = pos_dish_info.get("dish_details", {})

            # Build ApiOrderItem with proper structure
            # (Complex logic for combos, proteins, options - lines ~1500-1650)

            # Placeholder for complex item building
            # The actual implementation should be copied from the original function

        # --- SUBMIT TO POS ---
        tax_rate = 0.08
        tax_amount = round(calculated_item_subtotal * tax_rate, 2)
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

        logger.info(f"Submitting order to 39Miles POS. Call: {call_sid}")
        tnm_pos_response = await add_thirty_nine_miles_order(api_order_to_pos)

        if tnm_pos_response and tnm_pos_response.get("success"):
            external_pos_order_id = tnm_pos_response.get("data")
            logger.info(f"Order placed successfully. External ID: {external_pos_order_id}")

            # Build confirmation message
            summary_parts = []
            for item in english_ai_items:
                item_name = item.get("name")
                options = item.get("options", {})
                # Build readable summary (logic from lines ~1670-1700)
                summary_parts.append(item_name)

            order_summary_str = "; ".join(summary_parts)
            final_confirmation_text = f"Okay, your order {external_pos_order_id} is confirmed. It includes {order_summary_str}, for a total of ${final_payment_amount:.2f}. Thank you for your call, goodbye!"

            # Set flags
            await set_order_placed_flag(call_sid)
            if deepgram_service:
                deepgram_service.is_final_confirmation_sent = True

            # Send SMS
            if caller_phone:
                # SMS logic from original function
                pass

            # Clear cart
            order_manager.clear_cart(call_sid)

            response_content = final_confirmation_text
        else:
            error_msg = tnm_pos_response.get("message", "Unknown error") if tnm_pos_response else "No response"
            response_content = f"We encountered an issue submitting your order: {error_msg}. Please try again."
            if deepgram_service:
                deepgram_service.is_final_confirmation_sent = True

    except Exception as e:
        logger.error(f"Error in place_order: {e}", exc_info=True)
        response_content = "An internal error occurred while placing your order."

    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": clean_text_for_tts(response_content)
    }
    await deepgram_service.send_json(response)
    logger.info(f"Sent FunctionCallResponse for {function_name}")
```

**Note:** The above `handle_place_order` is abbreviated. The actual implementation should copy the **complete validation and payload building logic** from `handle_order_summary_thirty_nine_miles_en` (lines 1200-1700+), including:

- `process_customized_combo_options()`
- `process_combo_proteins()`
- `process_other_combo_options()`
- All the Family Combo special handling
- Complete POS payload construction
- SMS confirmation with detailed item formatting

**Part C - Update Function Dispatcher:**

```python
------- SEARCH
        # Handle different function calls
        if function_name == "order_summary":
            await handle_order_summary_thirty_nine_miles_en(
                function_call_id,
                function_name,
                input_data,
                deepgram_service,
                websocket,
                stream_sid,
                caller_phone,
                call_sid,
                client_id
            )
=======
        # Handle different function calls
        if function_name == "finalize_current_item":
            await handle_finalize_current_item(
                function_call_id,
                function_name,
                deepgram_service,
                call_sid
            )
        elif function_name == "place_order":
            await handle_place_order(
                function_call_id,
                function_name,
                deepgram_service,
                websocket,
                stream_sid,
                caller_phone,
                call_sid,
                client_id
            )
        # DEPRECATED: Remove in Phase 5 after testing
        elif function_name == "order_summary":
            await handle_order_summary_thirty_nine_miles_en(
                function_call_id,
                function_name,
                input_data,
                deepgram_service,
                websocket,
                stream_sid,
                caller_phone,
                call_sid,
                client_id
            )
+++++++ REPLACE
```

### Step 3: Register New Tools in the Audio Handler

#### File: `app/handlers/deepgram_english_audio_handler_refactored.py`

**Objective:** Update the imports and tool list to use the new tools.

**Location:** Lines 34-48 (imports) and lines 66-75 (tool list initialization)

**Part A - Update Imports:**

```python
------- SEARCH
from app/handlers/common_tool_defs import (
    ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
    CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
    # LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
    # RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
    # GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
    SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
    PROCESS_ORDER_SELECTION_TOOL_SCHEMA,
    PROCESS_COMBO_SELECTION_TOOL_SCHEMA
)
=======
from app.handlers.common_tool_defs import (
    FINALIZE_CURRENT_ITEM_TOOL_SCHEMA,
    PLACE_ORDER_TOOL_SCHEMA,
    ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,  # DEPRECATED: Remove in Phase 5
    CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
    SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
    PROCESS_ORDER_SELECTION_TOOL_SCHEMA,
    PROCESS_COMBO_SELECTION_TOOL_SCHEMA
)
+++++++ REPLACE
```

**Part B - Update Tool List:**

```python
------- SEARCH
        self.function_definitions = function_definitions or [
            ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
            CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
            # LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
            # RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
            # GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
            SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
            PROCESS_ORDER_SELECTION_TOOL_SCHEMA, # This will be renamed in the schema file
            PROCESS_COMBO_SELECTION_TOOL_SCHEMA # This will be renamed in the schema file
        ]
=======
        self.function_definitions = function_definitions or [
            FINALIZE_CURRENT_ITEM_TOOL_SCHEMA,
            PLACE_ORDER_TOOL_SCHEMA,
            CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
            SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
            PROCESS_ORDER_SELECTION_TOOL_SCHEMA,
            PROCESS_COMBO_SELECTION_TOOL_SCHEMA
        ]
+++++++ REPLACE
```

### Step 4: Update Tool Registration in websocket.py

#### File: `app/api/websocket.py`

**Objective:** Update the imports and tool list in the websocket handler.

**Location:** Lines 12-20 (imports) and lines 147-155 (tool list)

**Part A - Update Imports:**

```python
------- SEARCH
from app.handlers.common_tool_defs import (
    ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
    CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
    # LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
    # RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
    # GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
    SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
    PROCESS_ORDER_SELECTION_TOOL_SCHEMA
)
=======
from app.handlers.common_tool_defs import (
    FINALIZE_CURRENT_ITEM_TOOL_SCHEMA,
    PLACE_ORDER_TOOL_SCHEMA,
    CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
    SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
    PROCESS_ORDER_SELECTION_TOOL_SCHEMA,
    PROCESS_COMBO_SELECTION_TOOL_SCHEMA
)
+++++++ REPLACE
```

**Part B - Update Tool List:**

```python
------- SEARCH
        all_english_function_definitions = [
            ORDER_SUMMARY_TOOL_SCHEMA_EN_OPENAI,
            CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
            # LIST_DISHES_BY_CATEGORY_TOOL_SCHEMA_EN_OPENAI,
            # RECOMMEND_DISHES_TOOL_SCHEMA_EN_OPENAI,
            # GET_RANDOM_MENU_CATEGORIES_TOOL_SCHEMA_EN_OPENAI,
            SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
            PROCESS_ORDER_SELECTION_TOOL_SCHEMA
        ]
=======
        all_english_function_definitions = [
            FINALIZE_CURRENT_ITEM_TOOL_SCHEMA,
            PLACE_ORDER_TOOL_SCHEMA,
            CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI,
            SEND_MENU_LINK_TOOL_SCHEMA_EN_OPENAI,
            PROCESS_ORDER_SELECTION_TOOL_SCHEMA,
            PROCESS_COMBO_SELECTION_TOOL_SCHEMA
        ]
+++++++ REPLACE
```

---

## Phase 3: System Prompt Rewrite

### File: `app/constants.py`

**Objective:** Rewrite the English system prompt to reflect the new, simplified workflow without the confusing dual-semantic `order_summary` tool.

**Location:** Lines 153-188 (LIMF restaurant SYSTEM_MESSAGE)

**Modification:**

```python
------- SEARCH
        "SYSTEM_MESSAGE": (
        "# ROLE & GOAL\n"
        "You are a professional and efficient phone-ordering AI for \"KK Restaurant\". Your primary goal is to help customers place their orders accurately by following a clear, step-by-step process. You handle one thing at a time to ensure clarity.\n\n"
        "# CORE WORKFLOW & INSTRUCTIONS\n"
        "1.  Always Listen First: If the customer starts speaking, you must stop immediately and listen to their request.\n\n"
        "2.  Handle Menu Inquiries & Specific Dishes:\n"
        "    a. Prioritize Specific Dishes: If the user names a specific DISH (e.g., \"I want the Pad Thai\"), your absolute first priority is to call the `check_menu_item_english` tool to verify it. This rule overrides all others.\n"
        "    b. Offer SMS Menu for General Questions: If, and only if, the user asks a general question about the menu (e.g., \"what's on the menu?\", \"what do you have?\") without naming a specific dish, then you must offer the menu via SMS. Ask them: \"For easier browsing, I can text you a link to our full menu. Would you like that?\"\n"
        "       - If they agree: Call `send_menu_link` and let them know the link is sent.\n"
        "       - If they decline: Ask them to name a specific dish they are looking for. Their next response should be checked for a dish name.\n\n"
        "3.  Handle Specific Categories: \n"
        "     If the user names a specific CATEGORY (e.g., \"Tell me about your soups\"), you MUST inform them that you cannot list dishes by category and ask them to name a specific dish.\n\n"
        "4.  Handle Item Configuration: When the `check_menu_item_english` tool returns a JSON object containing a `\"tool_to_use\"` key, it signals that the user must make a selection. You are now in a guided configuration state.\n"
        "    a. You will be given a `message_for_agent` containing the next question for the user. You MUST ask this exact question.\n"
        "    b. The user's next response is their selection. You MUST call the specific tool indicated by the `\"tool_to_use\"` key (e.g., `handle_standard_item_selection` or `handle_combo_item_selection`) with the user's verbatim response.\n"
        "    c. Continue this guided process, always using the specific tool recommended by the backend, until the item is complete.\n\n"
        "SYSTEM_MESSAGE": (
        "# ROLE & GOAL\n"
        "You are a professional and efficient phone-ordering AI for \"KK Restaurant\". Your primary goal is to help customers place their orders accurately by following a clear, step-by-step process.\n\n"
        "# CORE WORKFLOW & INSTRUCTIONS\n"
        "1.  **Listen First**: If the customer speaks, stop immediately and listen.\n\n"
        "2.  **Handle Menu Inquiries**: \n"
        "    - If the user names a specific DISH, call `check_menu_item_english` immediately.\n"
        "    - For general questions (e.g., 'what do you have?'), offer to send the menu via SMS. If they agree, call `send_menu_link`.\n\n"
        "3.  **Guided Item Configuration**: \n"
        "    - When a tool returns a `tool_to_use` key, you are in a guided flow. Ask the user the exact `message_for_agent` provided.\n"
        "    - Call the specified tool (e.g., `handle_standard_item_selection` or `handle_combo_item_selection`) with the user's next response.\n"
        "    - Continue until the item is complete.\n\n"
        "4.  **Finalize Each Item**: \n"
        "    - Once a user has finished configuring an item, call the `finalize_current_item` tool to add it to the cart.\n"
        "    - The tool returns a confirmation message like 'Okay, I've added [Item Name] to your order. Anything else?'. Say this to the user.\n\n"
        "5.  **Place the Final Order**: \n"
        "    - When the customer indicates they are finished ordering (e.g., 'that's all', 'I'm done'), call the `place_order` tool.\n"
        "    - This finalizes the entire order. Relay the confirmation message to the customer.\n\n"
        "6.  **End the Call**: After calling `place_order` and confirming, the call is over. Do not ask more questions."
        ),
```
