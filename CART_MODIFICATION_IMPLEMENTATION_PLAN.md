# Cart Modification Implementation Plan

## Document Purpose
This document provides a step-by-step implementation plan for adding flexible cart modification capabilities to the Servio voice ordering system. This plan enables modifications at two levels: during confirmation and anytime after items are in the cart.

**Created:** October 25, 2025  
**Status:** Ready for Implementation  
**Priority:** High

---

## Design Decisions (Approved)

Based on user requirements, the following design choices have been confirmed:

1. **Modification Scope**: When user says "change the combo" → Ask them what option to change (conversational)
2. **Protein Swaps**: When user says "change the shrimp" → Remove shrimp and restart protein selection
3. **Confirmation Verbosity**: Read complete summary at confirmation step
4. **Error Handling**: If modification creates invalid state → Guide user to fix it (don't auto-revert)

---

## Architecture Overview

### Two-Level Modification System

```
┌─────────────────────────────────────────────────────────────┐
│                   LEVEL 1: CONFIRMATION                      │
│  (Before item moves to cart - in active_items)              │
│                                                              │
│  User Flow:                                                  │
│  1. AI: "I have combo with shrimp, mild. Correct?"         │
│  2. User: "Change to spicy"                                 │
│  3. AI: [modifies option directly in active_items]         │
│  4. AI: "Updated to spicy. Anything else?"                  │
│  5. User: "No" → Move to cart                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   LEVEL 2: CART MODIFICATION                 │
│  (After item is in cart - extract and modify)               │
│                                                              │
│  User Flow:                                                  │
│  1. User: "Change the combo"                                │
│  2. AI: [extracts from cart to modification_sessions]      │
│  3. AI: "What would you like to change?"                    │
│  4. User: "The flavor"                                      │
│  5. AI: "What flavor instead of Garlic Butter?"             │
│  6. User: "Cajun"                                           │
│  7. AI: [updates in modification_sessions]                 │
│  8. AI: "Updated. Anything else to change?"                 │
│  9. User: "No" → Move back to cart                          │
└─────────────────────────────────────────────────────────────┘
```

### State Management

```python
# Three distinct states per call_sid:
order_manager.active_items[call_sid]          # NEW items being configured
order_manager.modification_sessions[call_sid]  # EXISTING items being modified
order_manager.carts[call_sid]                  # FINALIZED items ready for order
```

---

## Implementation Phases

## PHASE 1: Confirmation-Level Modifications (Standard Items)

### Objective
Enable users to modify standard items (non-combos) during the confirmation step before they're added to cart.

### Files to Modify
1. `app/handlers/order_manager.py` - Add confirmation state
2. `app/handlers/english_tool_logic.py` - Update handlers
3. `app/handlers/common_tool_defs.py` - Add new tools

### Step 1.1: Add Confirmation State to OrderManager

**File:** `app/handlers/order_manager.py`

**Add new state tracking:**
```python
class OrderManager:
    def __init__(self):
        self.active_items: Dict[str, Dict[str, Any]] = {}
        self.carts: Dict[str, List[Dict[str, Any]]] = {}
        self.pending_ambiguous_items: Dict[str, Dict[str, Any]] = {}
        # NEW: Track items awaiting confirmation
        self.items_awaiting_confirmation: Dict[str, Dict[str, Any]] = {}
```

**Add method to enter confirmation:**
```python
def enter_confirmation_state(self, call_sid: str) -> Dict[str, Any]:
    """
    Moves an active item to confirmation state and presents summary.
    
    Returns:
        {
            "status": "AWAITING_CONFIRMATION",
            "message_for_agent": str (complete summary to read),
            "item_summary": Dict (structured data)
        }
    """
    if call_sid not in self.active_items:
        return {
            "status": "ERROR",
            "message_for_agent": "No active item to confirm."
        }
    
    active_item = self.active_items[call_sid]
    item_name = active_item.get("item_name")
    quantity = active_item.get("quantity", 1)
    selections = active_item.get("selections", {})
    
    # Move to confirmation state
    self.items_awaiting_confirmation[call_sid] = active_item
    del self.active_items[call_sid]
    
    # Build complete summary
    summary_parts = []
    summary_parts.append(f"I have {quantity} {item_name}")
    
    for option_group, selection in selections.items():
        summary_parts.append(f"{option_group}: {selection}")
    
    summary_text = ", ".join(summary_parts) + ". Is that correct?"
    
    return {
        "status": "AWAITING_CONFIRMATION",
        "message_for_agent": summary_text,
        "item_summary": {
            "name": item_name,
            "quantity": quantity,
            "selections": selections
        }
    }
```

**Add method to modify option during confirmation:**
```python
def modify_option_during_confirmation(self, call_sid: str, user_input: str) -> Dict[str, Any]:
    """
    Handles modification requests during confirmation state.
    Detects which option to change and updates it.
    
    Args:
        call_sid: The call session ID
        user_input: User's modification request (e.g., "change flavor to mild")
    
    Returns:
        {
            "status": "MODIFIED" | "NEED_CLARIFICATION" | "CONFIRMED",
            "message_for_agent": str,
            "item_summary": Dict (updated)
        }
    """
    if call_sid not in self.items_awaiting_confirmation:
        return {
            "status": "ERROR",
            "message_for_agent": "No item is awaiting confirmation."
        }
    
    item = self.items_awaiting_confirmation[call_sid]
    
    # Check if user is confirming (yes/correct/good/etc)
    confirmation_phrases = ["yes", "correct", "right", "good", "yep", "yeah", "that's right"]
    if any(phrase in user_input.lower() for phrase in confirmation_phrases):
        # Move to cart
        self.add_item_to_cart(
            call_sid,
            item.get("item_name"),
            item.get("quantity", 1),
            item.get("selections", {})
        )
        del self.items_awaiting_confirmation[call_sid]
        
        return {
            "status": "CONFIRMED",
            "message_for_agent": f"Great! I've added the {item.get('item_name')} to your order. What else can I get for you?",
            "current_cart": self.get_cart(call_sid)
        }
    
    # Detect which option they want to change
    dish_details = item.get("dish_details", {})
    option_groups = dish_details.get("optionGroups", [])
    
    # Extract option group names
    available_options = {}
    for group in option_groups:
        group_name = _get_english_name(group.get("name", {}))
        if group_name:
            available_options[group_name.lower()] = group
    
    # Try to detect option from user input
    detected_option = None
    for option_name_lower, group in available_options.items():
        if option_name_lower in user_input.lower():
            detected_option = _get_english_name(group.get("name", {}))
            break
    
    # Also check for common keywords
    if not detected_option:
        if "flavor" in user_input.lower():
            detected_option = next((name for name in available_options.keys() if "flavor" in name), None)
        elif "spicy" in user_input.lower() or "spice" in user_input.lower():
            detected_option = next((name for name in available_options.keys() if "spicy" in name or "spice" in name), None)
    
    if not detected_option:
        # Ask for clarification
        option_names = ", ".join(available_options.keys())
        return {
            "status": "NEED_CLARIFICATION",
            "message_for_agent": f"What would you like to change? Your options are: {option_names}."
        }
    
    # Get the target group
    target_group = available_options.get(detected_option.lower())
    if not target_group:
        return {
            "status": "ERROR",
            "message_for_agent": "I couldn't find that option to change."
        }
    
    # Extract the new value from user input
    options_in_group = [_get_english_name(opt.get("name", {})) for opt in target_group.get("options", [])]
    
    # Use fuzzy matching to find the selection
    from thefuzz import process
    best_match, score = process.extractOne(user_input, options_in_group)
    
    if score < 70:
        options_str = ", ".join(options_in_group)
        return {
            "status": "NEED_CLARIFICATION",
            "message_for_agent": f"For {detected_option}, your options are: {options_str}. Which would you like?"
        }
    
    # Update the selection
    item["selections"][detected_option] = best_match
    
    # Build updated summary
    summary_parts = []
    summary_parts.append(f"I have {item.get('quantity', 1)} {item.get('item_name')}")
    
    for option_group, selection in item.get("selections", {}).items():
        summary_parts.append(f"{option_group}: {selection}")
    
    summary_text = ", ".join(summary_parts) + ". Is that correct now?"
    
    return {
        "status": "MODIFIED",
        "message_for_agent": summary_text,
        "item_summary": {
            "name": item.get("item_name"),
            "quantity": item.get("quantity", 1),
            "selections": item.get("selections", {})
        }
    }
```

### Step 1.2: Update get_next_question to use Confirmation

**Modify in:** `app/handlers/order_manager.py`

```python
def get_next_question(self, call_sid: str) -> Dict[str, Any]:
    """
    Modified to enter confirmation state after last option is selected.
    """
    if not self.is_active(call_sid):
        return {"status": "ERROR", "message_for_agent": "..."}

    item = self.active_items[call_sid]
    
    # Check if all steps completed
    if item["current_step"] >= len(item["option_groups_to_process"]):
        # NEW: Enter confirmation instead of immediately adding to cart
        return self.enter_confirmation_state(call_sid)
    
    # ... rest of existing code for asking next question ...
```

### Step 1.3: Update Handler in english_tool_logic.py

**File:** `app/handlers/english_tool_logic.py`

**Update handle_standard_item_selection:**
```python
async def handle_standard_item_selection(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str]
):
    """
    Enhanced to handle confirmation state modifications.
    """
    user_input = input_data.get("user_input")
    
    # ... existing ambiguity resolution code ...
    
    # NEW: Check if item is awaiting confirmation
    if order_manager.is_awaiting_confirmation(call_sid):
        logger.info(f"Processing confirmation or modification for call_sid: {call_sid}")
        response_payload = order_manager.modify_option_during_confirmation(call_sid, user_input)
        
        cleaned_response_payload = clean_response_for_tts(response_payload)
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": json.dumps(cleaned_response_payload)
        }
        await deepgram_service.send_json(response)
        logger.info(f"Sent confirmation response for {function_name}")
        return
    
    # ... existing active item processing code ...
```

**Add helper method to OrderManager:**
```python
def is_awaiting_confirmation(self, call_sid: str) -> bool:
    """Check if an item is awaiting confirmation."""
    return call_sid in self.items_awaiting_confirmation
```

---

## PHASE 2: Confirmation-Level Modifications (Combos)

### Objective
Enable users to modify combos during final confirmation in `combo_order_manager.py`.

### Files to Modify
1. `app/handlers/combo_order_manager.py` - Enhance update_combo_selection
2. `app/handlers/english_tool_logic.py` - Update combo handler

### Step 2.1: Enhance update_combo_selection for Targeted Changes

**File:** `app/handlers/combo_order_manager.py`

**Current method:** `update_combo_selection()` - needs enhancement

**Add helper for detecting modification target:**
```python
def _detect_modification_target(self, user_input: str, order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyzes user input to determine what they want to change.
    
    Returns:
        {
            "target_type": "flavor" | "spice" | "protein" | "clarification_needed",
            "target_name": str (option group name),
            "action": "replace" | "remove" | "add"
        }
    """
    user_input_lower = user_input.lower()
    
    # Check for flavor change
    if "flavor" in user_input_lower:
        return {
            "target_type": "flavor",
            "target_name": "Pick your flavor",
            "action": "replace"
        }
    
    # Check for spice level change
    if "spicy" in user_input_lower or "spice" in user_input_lower or "mild" in user_input_lower:
        return {
            "target_type": "spice",
            "target_name": "Pick your spicy level",
            "action": "replace"
        }
    
    # Check for protein changes
    protein_keywords = ["shrimp", "crab", "lobster", "mussels", "clams", "crawfish", "protein"]
    if any(keyword in user_input_lower for keyword in protein_keywords):
        # Determine if adding, removing, or replacing
        if "remove" in user_input_lower or "take out" in user_input_lower:
            action = "remove"
        elif "add" in user_input_lower or "another" in user_input_lower:
            action = "add"
        else:
            action = "replace"  # Default for "change shrimp"
        
        return {
            "target_type": "protein",
            "target_name": "proteins",
            "action": action
        }
    
    # Need clarification
    return {
        "target_type": "clarification_needed",
        "target_name": None,
        "action": None
    }
```

**Enhance update_combo_selection:**
```python
def update_combo_selection(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
    """
    Enhanced to handle targeted modifications during final confirmation.
    """
    # Detect what user wants to change
    modification_target = self._detect_modification_target(user_input, order)
    
    if modification_target["target_type"] == "clarification_needed":
        # Ask user what they want to change
        available_changes = []
        selections = order.get("selections", {})
        
        if "proteins" in selections:
            available_changes.append("proteins")
        if "Pick your flavor" in selections:
            available_changes.append("flavor")
        if "Pick your spicy level" in selections:
            available_changes.append("spice level")
        
        changes_str = ", ".join(available_changes)
        return {
            "action": "reprompt",
            "message_for_agent": f"What would you like to change? You can modify: {changes_str}."
        }
    
    # Handle protein modifications
    if modification_target["target_type"] == "protein":
        return self._handle_protein_modification(user_input, order, call_sid, modification_target["action"])
    
    # Handle flavor changes
    if modification_target["target_type"] == "flavor":
        return self._handle_option_change(user_input, order, call_sid, "Pick your flavor")
    
    # Handle spice changes
    if modification_target["target_type"] == "spice":
        return self._handle_option_change(user_input, order, call_sid, "Pick your spicy level")
    
    # ... existing code ...
```

**Add protein modification handler:**
```python
def _handle_protein_modification(self, user_input: str, order: Dict[str, Any], 
                                 call_sid: str, action: str) -> Dict[str, Any]:
    """
    Handles protein add/remove/replace during confirmation.
    
    For REPLACE: Remove the mentioned protein and restart protein selection
    For REMOVE: Remove protein from list
    For ADD: Add another protein
    """
    selections = order.get("selections", {})
    current_proteins = selections.get("proteins", [])
    
    if action == "remove":
        # Detect which protein to remove
        from thefuzz import process
        best_match, score = process.extractOne(user_input, current_proteins)
        
        if score < 70:
            proteins_str = ", ".join(current_proteins)
            return {
                "action": "reprompt",
                "message_for_agent": f"Which protein would you like to remove? You have: {proteins_str}."
            }
        
        # Remove the protein
        current_proteins = [p for p in current_proteins if p != best_match]
        selections["proteins"] = current_proteins
        
        summary = self.get_order_summary(call_sid)
        return {
            "action": "confirm_order",
            "message_for_agent": f"I've removed the {best_match}. Now I have your combo with {summary}. Is that correct?"
        }
    
    elif action == "replace":
        # Detect which protein to replace
        from thefuzz import process
        protein_keywords = ["shrimp", "crab", "lobster", "mussels", "clams", "crawfish"]
        
        # Find which protein they mentioned
        mentioned_protein = None
        for keyword in protein_keywords:
            if keyword in user_input.lower():
                # Try to match against current proteins
                matches = [p for p in current_proteins if keyword in p.lower()]
                if matches:
                    mentioned_protein = matches[0]
                    break
        
        if not mentioned_protein:
            proteins_str = ", ".join(current_proteins)
            return {
                "action": "reprompt",
                "message_for_agent": f"Which protein would you like to change? You have: {proteins_str}."
            }
        
        # Remove this protein and restart protein selection
        current_proteins = [p for p in current_proteins if p != mentioned_protein]
        selections["proteins"] = current_proteins
        
        # Change state to add new protein
        order["state"] = "AWAITING_PROTEIN_CHOICE"
        
        return {
            "status": "PROMPT_FOR_PROTEIN",
            "message_for_agent": f"I've removed the {mentioned_protein}. What protein would you like instead?",
            "tool_to_use": "handle_combo_item_selection"
        }
    
    elif action == "add":
        # Add another protein
        order["state"] = "AWAITING_PROTEIN_CHOICE"
        
        return {
            "status": "PROMPT_FOR_PROTEIN",
            "message_for_agent": "What additional protein would you like to add?",
            "tool_to_use": "handle_combo_item_selection"
        }
    
    return {
        "action": "reprompt",
        "message_for_agent": "I didn't understand that change. What would you like to modify?"
    }
```

**Add option change handler:**
```python
def _handle_option_change(self, user_input: str, order: Dict[str, Any], 
                         call_sid: str, option_name: str) -> Dict[str, Any]:
    """
    Handles changing a specific option (flavor, spice, etc.) during confirmation.
    """
    dish_details = order.get("dish_details", {})
    option_groups = dish_details.get("optionGroups", [])
    
    # Find the target option group
    target_group = None
    for group in option_groups:
        group_name = self._get_display_name(group.get("name", {}))
        if group_name == option_name:
            target_group = group
            break
    
    if not target_group:
        return {
            "action": "reprompt",
            "message_for_agent": f"I couldn't find the {option_name} option."
        }
    
    # Get available options
    options = [self._clean_option_name_for_tts(self._get_display_name(opt.get("name", {}))) 
               for opt in target_group.get("options", [])]
    options = [opt for opt in options if opt]
    
    # Try to match user input to an option
    from thefuzz import process
    normalized_input = normalize_for_matching(user_input)
    normalized_options, norm_to_orig_map = normalize_options_for_matching(options)
    
    best_match_normalized, score = process.extractOne(normalized_input, normalized_options)
    
    if score < 70:
        options_str = ", ".join(options)
        return {
            "action": "reprompt",
            "message_for_agent": f"For {option_name}, your options are: {options_str}. Which would you like?"
        }
    
    # Get original option name
    best_match = norm_to_orig_map[best_match_normalized]
    
    # Update the selection
    order["selections"][option_name] = best_match
    
    # Present updated summary
    summary = self.get_order_summary(call_sid)
    return {
        "action": "confirm_order",
        "message_for_agent": f"I've changed the {option_name} to {best_match}. Now I have your combo with {summary}. Is that correct?"
    }
```

---

## PHASE 3: Anytime Cart Modifications

### Objective
Enable users to modify items that are already in the cart by extracting them into a modification session.

### Files to Modify
1. `app/handlers/order_manager.py` - Add modification session management
2. `app/handlers/english_tool_logic.py` - Add cart modification handlers
3. `app/handlers/common_tool_defs.py` - Add new tool schemas

### Step 3.1: Add Modification Session Management

**File:** `app/handlers/order_manager.py`

**Add to __init__:**
```python
def __init__(self):
    self.active_items: Dict[str, Dict[str, Any]] = {}
    self.carts: Dict[str, List[Dict[str, Any]]] = {}
    self.pending_ambiguous_items: Dict[str, Dict[str, Any]] = {}
    self.items_awaiting_confirmation: Dict[str, Dict[str, Any]] = {}
    # NEW: Track items being modified from cart
    self.modification_sessions: Dict[str, Dict[str, Any]] = {}
```

**Add method to start cart modification:**
```python
def start_cart_item_modification(self, call_sid: str, item_identifier: str, 
                                 portal_id: str) -> Dict[str, Any]:
    """
    Extracts an item from cart and prepares it for modification.
    
    Args:
        call_sid: The call session ID
        item_identifier: Item name, index, or "last"
        portal_id: Portal ID to fetch fresh dish details
    
    Returns:
        {
            "status": "SUCCESS" | "NOT_FOUND" | "ERROR",
            "message_for_agent": str,
            "modification_type": "standard" | "combo",
            "item_name": str
        }
    """
    if call_sid not in self.carts or not self.carts[call_sid]:
        return {
            "status": "ERROR",
            "message_for_agent": "Your cart is empty."
        }
    
    cart = self.carts[call_sid]
    item_to_modify = None
    item_index = -1
    
    # Handle "last" keyword
    if item_identifier.lower() == "last":
        item_index = len(cart) - 1
        item_to_modify = cart[item_index]
    else:
        # Try index first
        try:
            index = int(item_identifier)
            if 0 <= index < len(cart):
                item_to_modify = cart[index]
                item_index = index
        except ValueError:
            pass
        
        # Try fuzzy name matching
        if not item_to_modify:
            from thefuzz import process
            cart_item_names = [item["name"] for item in cart]
            best_match, score = process.extractOne(item_identifier, cart_item_names)
            
            if score >= 75:
                for i, item in enumerate(cart):
                    if item["name"] == best_match:
                        item_to_modify = item
                        item_index = i
                        break
    
    if not item_to_modify:
        return {
            "status": "NOT_FOUND",
            "message_for_agent": f"I couldn't find '{item_identifier}' in your cart."
        }
    
    # Remove from cart temporarily
    cart.pop(item_index)
    
    # Store in modification session
    self.modification_sessions[call_sid] = {
        "item": item_to_modify,
        "original_index": item_index,
        "modification_state": "SELECTING_OPTION"
    }
    
    # Determine if combo or standard item
    is_combo = "combo" in item_to_modify["name"].lower()
    
    return {
        "status": "SUCCESS",
        "message_for_agent": f"What would you like to change about the {item_to_modify['name']}?",
        "modification_type": "combo" if is_combo else "standard",
        "item_name": item_to_modify["name"]
    }
```

**Add method to process modification:**
```python
def process_cart_modification(self, call_sid: str, user_input: str) -> Dict[str, Any]:
    """
    Processes user's modification request during cart modification session.
    
    This is similar to modify_option_during_confirmation but for cart items.
    """
    if call_sid not in self.modification_sessions:
        return {
            "status": "ERROR",
            "message_for_agent": "No active modification session."
        }
    
    session = self.modification_sessions[call_sid]
    item = session["item"]
    
    # Check if user wants to cancel
    if "cancel" in user_input.lower() or "nevermind" in user_input.lower():
        # Put item back in cart
        self.carts[call_sid].insert(session["original_index"], item)
        del self.modification_sessions[call_sid]
        
        return {
            "status": "CANCELLED",
            "message_for_agent": "Okay, I've kept the original item. What else can I help with?"
        }
    
    # Check if user is done modifying
    done_phrases = ["that's it", "done", "finished", "good", "looks good", "that's all"]
    if any(phrase in user_input.lower() for phrase in done_phrases):
        # Add modified item back to cart
        self.carts[call_sid].append(item)
        del self.modification_sessions[call_sid]
        
        return {
            "status": "COMPLETED",
            "message_for_agent": f"Great! I've updated the {item['name']}. What else can I get for you?",
            "current_cart": self.get_cart(call_sid)
        }
    
    # User wants to change something - detect what
    available_options = list(item.get("options", {}).keys())
    
    # Try to detect which option
    detected_option = None
    for option_name in available_options:
        if option_name.lower() in user_input.lower():
            detected_option = option_name
            break
    
    # Common keywords
    if not detected_option:
        if "flavor" in user_input.lower():
            detected_option = next((opt for opt in available_options if "flavor" in opt.lower()), None)
        elif "spicy" in user_input.lower() or "spice" in user_input.lower():
            detected_option = next((opt for opt in available_options if "spicy" in opt.lower() or "spice" in opt.lower()), None)
        elif "protein" in user_input.lower():
            detected_option = "proteins"
    
    if not detected_option:
        options_str = ", ".join(available_options)
        return {
            "status": "NEED_CLARIFICATION",
            "message_for_agent": f"Which option would you like to change? Available: {options_str}."
        }
    
    # Store what they want to change
    session["modification_state"] = "SELECTING_VALUE"
    session["target_option"] = detected_option
    
    # Get current value
    current_value = item["options"].get(detected_option, "not set")
    
    return {
        "status": "AWAITING_NEW_VALUE",
        "message_for_agent": f"The {detected_option} is currently {current_value}. What would you like to change it to?"
    }
```

**Add method to finalize modification:**
```python
def complete_cart_modification(self, call_sid: str, new_value: str) -> Dict[str, Any]:
    """
    Applies the new value to the option being modified.
    """
    if call_sid not in self.modification_sessions:
        return {
            "status": "ERROR",
            "message_for_agent": "No active modification session."
        }
    
    session = self.modification_sessions[call_sid]
    item = session["item"]
    target_option = session.get("target_option")
    
    if not target_option:
        return {
            "status": "ERROR",
            "message_for_agent": "No option selected for modification."
        }
    
    # Update the value
    item["options"][target_option] = new_value
    
    # Reset state
    session["modification_state"] = "SELECTING_OPTION"
    session["target_option"] = None
    
    return {
        "status": "MODIFIED",
        "message_for_agent": f"I've updated the {target_option} to {new_value}. Would you like to change anything else, or is that good?"
    }
```

### Step 3.2: Add Tool Definitions

**File:** `app/handlers/common_tool_defs.py`

Add new tool schema:

```python
MODIFY_CART_ITEM_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "modify_cart_item",
    "description": "Start modifying an existing item in the cart. Use when customer says 'change the combo', 'edit my order', etc.",
    "parameters": {
        "type": "object",
        "properties": {
            "item_identifier": {
                "type": "string",
                "description": "The name of the item to modify, or 'last' for the most recent item."
            }
        },
        "required": ["item_identifier"]
    }
}
```

### Step 3.3: Add Handler Functions

**File:** `app/handlers/english_tool_logic.py`

Add handler for cart modification:

```python
async def handle_modify_cart_item(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str],
    portal_id: str
):
    """Initiates cart item modification session."""
    item_identifier = input_data.get("item_identifier", "").strip()
    
    if not item_identifier:
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": "Which item would you like to modify?"
        }
        await deepgram_service.send_json(response)
        return
    
    result = order_manager.start_cart_item_modification(call_sid, item_identifier, portal_id)
    
    if result["status"] != "SUCCESS":
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": result.get("message_for_agent", "Couldn't start modification.")
        }
        await deepgram_service.send_json(response)
        return
    
    # Set up for modification flow
    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": result.get("message_for_agent")
    }
    await deepgram_service.send_json(response)
    logger.info(f"Started cart modification session for {call_sid}: {result['item_name']}")
```

Add to main handler router in `handle_function_call()`:

```python
elif function_name == "modify_cart_item":
    portal_id = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT if client_id == "LIMF" else None
    if not portal_id:
        logger.error(f"Cannot modify cart item: portal_id missing for client {client_id}")
        return
    await handle_modify_cart_item(
        function_call_id,
        function_name,
        input_data,
        deepgram_service,
        call_sid,
        portal_id
    )
```

Update `handle_standard_item_selection` to handle modification sessions:

```python
async def handle_standard_item_selection(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str]
):
    user_input = input_data.get("user_input")
    
    # ... existing ambiguity resolution ...
    
    # Check if item is awaiting confirmation
    if order_manager.is_awaiting_confirmation(call_sid):
        # ... existing confirmation code ...
        return
    
    # NEW: Check if in modification session
    if call_sid in order_manager.modification_sessions:
        logger.info(f"Processing cart modification for call_sid: {call_sid}")
        response_payload = order_manager.process_cart_modification(call_sid, user_input)
        
        cleaned_response_payload = clean_response_for_tts(response_payload)
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": json.dumps(cleaned_response_payload)
        }
        await deepgram_service.send_json(response)
        return
    
    # ... existing active item processing ...
```

---

## PHASE 4: Testing & Validation

### Test Scenarios

#### Scenario 1: Standard Item Confirmation Modification
```
User: "I'll have fried rice"
AI: [guides through options]
AI: "I have 1 Fried Rice with chicken and mild. Is that correct?"
User: "Change to spicy"
AI: "I have 1 Fried Rice with chicken and spicy. Is that correct?"
User: "Yes"
AI: "Great! I've added the Fried Rice to your order."

✅ Pass Criteria:
- Confirmation state entered after last option
- Spice level updated without restarting
- Complete summary read at each confirmation
- Item added to cart after confirmation
```

#### Scenario 2: Combo Protein Replacement
```
User: "I'll have the Family Combo"
AI: [guides through configuration]
AI: "I have your Family Combo with shrimp, mussels, clams, snow crab, garlic butter flavor, mild. Is that correct?"
User: "Change the shrimp"
AI: "I've removed the shrimp. What protein would you like instead?"
User: "Lobster tail"
AI: [processes selection]
AI: "I have your Family Combo with lobster tail, mussels, clams, snow crab, garlic butter flavor, mild. Is that correct?"

✅ Pass Criteria:
- Protein removed from list
- Protein selection restarted
- Other selections preserved
- Complete summary includes all proteins
```

#### Scenario 3: Cart Item Modification
```
User: [after items are in cart] "Change the combo"
AI: "What would you like to change about the Family Combo?"
User: "The flavor"
AI: "The Pick your flavor is currently Garlic Butter. What would you like to change it to?"
User: "Cajun"
AI: "I've updated the Pick your flavor to Cajun. Would you like to change anything else, or is that good?"
User: "That's good"
AI: "Great! I've updated the Family Combo. What else can I get for you?"

✅ Pass Criteria:
- Item extracted from cart
- Conversational modification flow
- Only specified option changed
- Item returned to cart
- No data loss
```

#### Scenario 4: Error Recovery
```
User: [modifying combo] "Remove all the proteins"
AI: [all proteins removed]
AI: "Your combo now has no proteins selected. You need at least 3 proteins. Let's add them back."
AI: "For the first protein, what would you like?"

✅ Pass Criteria:
- Invalid state detected
- User guided to fix it
- No auto-revert
- Helpful error message
```

### Unit Tests Needed

**File:** `tests/test_order_manager_modifications.py`

```python
def test_enter_confirmation_state():
    """Test transitioning to confirmation state"""
    
def test_modify_option_during_confirmation():
    """Test changing options at confirmation"""
    
def test_confirmation_with_yes():
    """Test confirming and moving to cart"""
    
def test_start_cart_modification():
    """Test extracting item from cart"""
    
def test_process_cart_modification():
    """Test modifying cart item"""
    
def test_modification_session_cancel():
    """Test canceling modification"""
```

**File:** `tests/test_combo_modifications.py`

```python
def test_protein_replacement():
    """Test replacing protein in combo"""
    
def test_protein_removal():
    """Test removing protein from combo"""
    
def test_flavor_change():
    """Test changing flavor option"""
    
def test_multiple_modifications():
    """Test changing multiple options"""
```

---

## Implementation Checklist

### Phase 1: Standard Item Confirmation
- [ ] Add `items_awaiting_confirmation` dict to OrderManager
- [ ] Implement `enter_confirmation_state()`
- [ ] Implement `modify_option_during_confirmation()`
- [ ] Implement `is_awaiting_confirmation()`
- [ ] Update `get_next_question()` to use confirmation
- [ ] Update `handle_standard_item_selection()` to handle confirmation
- [ ] Test with simple items (fries, oysters)
- [ ] Test with items with multiple options

### Phase 2: Combo Confirmation
- [ ] Implement `_detect_modification_target()`
- [ ] Enhance `update_combo_selection()` for targeted changes
- [ ] Implement `_handle_protein_modification()`
- [ ] Implement `_handle_option_change()`
- [ ] Test with Customized Combo
- [ ] Test with Family Combo
- [ ] Test with fixed combos (Combo #1, #2, #3)

### Phase 3: Cart Modifications
- [ ] Add `modification_sessions` dict to OrderManager
- [ ] Implement `start_cart_item_modification()`
- [ ] Implement `process_cart_modification()`
- [ ] Implement `complete_cart_modification()`
- [ ] Add `MODIFY_CART_ITEM_TOOL_SCHEMA`
- [ ] Implement `handle_modify_cart_item()`
- [ ] Add to function router
- [ ] Update `handle_standard_item_selection()` for modification sessions
- [ ] Test standard item cart modification
- [ ] Test combo cart modification

### Phase 4: Testing & Documentation
- [ ] Write unit tests for all new methods
- [ ] Write integration tests for workflows
- [ ] Test error cases and recovery
- [ ] Update API documentation
- [ ] Create user guide for modification features
- [ ] Performance testing with multiple simultaneous modifications

---

## Rollout Strategy

### Week 1: Foundation
- Implement Phase 1 (Standard Item Confirmation)
- Unit tests and basic integration tests
- Deploy to staging environment

### Week 2: Combo Support
- Implement Phase 2 (Combo Confirmation)
- Enhanced testing with all combo types
- UAT with test users

### Week 3: Cart Modifications
- Implement Phase 3 (Anytime Cart Modifications)
- Complete test coverage
- Stress testing

### Week 4: Production Release
- Gradual rollout (10% → 50% → 100%)
- Monitor error rates and user feedback
- Quick iteration on issues

---

## Success Metrics

### Functional Metrics
- **Confirmation Success Rate**: >95% of items confirmed on first try
- **Modification Success Rate**: >90% of modifications completed successfully
- **Error Recovery Rate**: >85% of invalid states successfully recovered

### User Experience Metrics
- **Average Modifications Per Order**: Track to understand usage
- **Time to Complete Modification**: Should be <30 seconds
- **User Satisfaction**: Post-call survey improvement

### Technical Metrics
- **Response Time**: <200ms for modification operations
- **State Consistency**: 100% - no orphaned sessions
- **Error Rate**: <2% for modification operations

---

## Known Limitations & Future Enhancements

### Current Limitations
1. Cannot modify quantity during confirmation (must use separate flow)
2. Protein swaps require full restart of protein selection
3. No undo functionality
4. Cannot batch modify multiple items

### Future Enhancements
1. **Undo Support**: "Actually, keep the original"
2. **Smart Defaults**: Remember user preferences
3. **Bulk Operations**: "Make everything extra spicy"
4. **Modification History**: Track all changes for analytics
5. **AI Intent Prediction**: Detect likely modifications preemptively

---

## Rollback Plan

If critical issues are discovered:

1. **Immediate**: Disable new tools via feature flag
2. **Fallback**: Revert to old "restart configuration" pattern
3. **Data Safety**: All modifications are in-memory, no data corruption risk
4. **User Impact**: Minimal - users can still order, just without modification convenience

---

## Support & Maintenance

### Monitoring
- Track modification success/failure rates
- Log all state transitions for debugging
- Alert on high error rates (>5%)

### Common Issues & Solutions
1. **Issue**: User confused about what they're changing
   - **Solution**: Make prompts more explicit about current selections

2. **Issue**: Modification creates invalid state
   - **Solution**: Enhanced validation with user guidance

3. **Issue**: Session state lost
   - **Solution**: Implement session recovery from cart

---

## Conclusion

This implementation plan provides a complete roadmap for adding flexible cart modification capabilities to Servio. The two-level system (confirmation + cart modification) addresses user needs while maintaining system reliability.

**Next Steps:**
1. Review and approve this plan
2. Create implementation tickets
3. Begin Phase 1 development
4. Schedule regular check-ins during implementation

**Questions or Concerns?**
Contact the development team for clarification on any aspect of this plan.
