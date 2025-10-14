# Cart Modification Feature Requirements

## Document Purpose
This document provides a comprehensive guide for implementing cart modification features in the Servio voice ordering system. Currently, users cannot modify items after adding them to the cart - they can only clear the entire cart and start over.

---

## Executive Summary

### Current State: Critical Gap in User Experience

**What Works:**
- ✅ Add items to cart
- ✅ View cart contents
- ✅ Clear entire cart
- ✅ Place order

**What's Missing:**
- ❌ Remove individual items from cart
- ❌ Change item quantity
- ❌ Edit item options (flavor, spice, etc.)
- ❌ Modify combo selections after finalization

**Impact:** Poor user experience - customers must clear their entire order to fix a single mistake.

---

## User Stories & Scenarios

### Scenario 1: Remove Item
**User:** "Actually, remove the oyster from my order"

**Current Behavior:** AI cannot remove it - must clear entire cart

**Expected Behavior:**
1. AI identifies which item to remove
2. Removes item from cart
3. Confirms: "I've removed the oyster. Your order now has [remaining items]."

### Scenario 2: Change Quantity
**User:** "Change that to 2 orders" or "Make it 3 portions"

**Current Behavior:** AI cannot modify quantity - must re-add item

**Expected Behavior:**
1. AI identifies which item to modify (usually the last one added)
2. Updates quantity
3. Confirms: "I've changed the oyster to 2 portions. Total is now $X.XX"

### Scenario 3: Edit Item Options
**User:** "Can I change the flavor on that combo to mild instead of spicy?"

**Current Behavior:** AI cannot edit - must remove and re-add

**Expected Behavior:**
1. AI identifies which item to edit
2. Updates the specific option
3. Confirms: "I've changed the combo to mild flavor. Anything else?"

### Scenario 4: Modify Last Item
**User:** "Wait, change that" (referring to the item just added)

**Current Behavior:** Unclear what to do

**Expected Behavior:**
1. AI assumes reference is to the most recently added item
2. Prompts: "What would you like to change about the [item name]?"
3. Guides user through modification

---

## Technical Analysis

### Current Architecture

#### OrderManager (`app/handlers/order_manager.py`)
```python
class OrderManager:
    def __init__(self):
        self.active_items: Dict[str, Dict[str, Any]] = {}  # Items being configured
        self.carts: Dict[str, List[Dict[str, Any]]] = {}   # Finalized items
    
    # Existing methods:
    def add_item_to_cart(self, call_sid, item_name, quantity, options)  # ✅
    def get_cart(self, call_sid) -> List[Dict[str, Any]]                # ✅
    def clear_cart(self, call_sid)                                       # ✅
    
    # Missing methods:
    # ❌ remove_from_cart(call_sid, item_identifier)
    # ❌ update_quantity(call_sid, item_identifier, new_quantity)
    # ❌ edit_cart_item(call_sid, item_identifier)
```

**Cart Structure:**
```python
self.carts[call_sid] = [
    {
        "name": "Raw Oyster (6 PCs)",
        "quantity": 1,
        "options": {}
    },
    {
        "name": "FAMILY COMBO",
        "quantity": 1,
        "options": {
            "proteins": ["Shrimp head on", "Black Mussels", "Clams"],
            "crab": "Snow Crab Legs",
            "free_items": ["Corn", "Potatoes", "Sausage", "Boiled Egg", "Broccoli"],
            "Pick your flavor": "Garlic Butter",
            "Pick your spicy level": "Mild"
        }
    }
]
```

#### ComboOrderManager (`app/handlers/combo_order_manager.py`)
```python
class ComboOrderManager:
    # Has update_combo_selection() but only during configuration
    # Once finalized → cannot be modified
    
    def update_combo_selection(self, user_input, order, call_sid):
        # Only works in "AWAITING_FINAL_CONFIRMATION" state
        # After moving to cart → inaccessible
```

#### Available AI Tools (`app/handlers/common_tool_defs.py`)
```python
# Existing:
FINALIZE_CURRENT_ITEM_TOOL_SCHEMA      # Add to cart
PLACE_ORDER_TOOL_SCHEMA                # Submit order
CHECK_MENU_ITEM_TOOL_SCHEMA_EN_OPENAI  # Search menu
PROCESS_ORDER_SELECTION_TOOL_SCHEMA    # Configure standard items
PROCESS_COMBO_SELECTION_TOOL_SCHEMA    # Configure combos

# Missing:
# ❌ REMOVE_CART_ITEM_TOOL_SCHEMA
# ❌ UPDATE_CART_QUANTITY_TOOL_SCHEMA
# ❌ EDIT_CART_ITEM_TOOL_SCHEMA
# ❌ VIEW_CART_DETAILS_TOOL_SCHEMA
```

---

## Implementation Requirements

### Phase 1: Core Cart Modification Methods

#### 1.1 Remove Item from Cart
**File:** `app/handlers/order_manager.py`

```python
def remove_from_cart(self, call_sid: str, item_identifier: str) -> Dict[str, Any]:
    """
    Removes an item from the cart by name or index.
    
    Args:
        call_sid: The call session ID
        item_identifier: Either item name (str) or index (int as str: "0", "1", etc.)
    
    Returns:
        {
            "status": "SUCCESS" | "NOT_FOUND" | "ERROR",
            "message_for_agent": str,
            "removed_item": Dict (if successful),
            "updated_cart": List[Dict]
        }
    """
    if call_sid not in self.carts or not self.carts[call_sid]:
        return {
            "status": "ERROR",
            "message_for_agent": "Your cart is empty, there's nothing to remove."
        }
    
    cart = self.carts[call_sid]
    
    # Try to parse as index first
    try:
        index = int(item_identifier)
        if 0 <= index < len(cart):
            removed = cart.pop(index)
            logger.info(f"Removed item at index {index} from cart for {call_sid}: {removed['name']}")
            return {
                "status": "SUCCESS",
                "message_for_agent": f"I've removed the {removed['name']} from your order.",
                "removed_item": removed,
                "updated_cart": self.get_cart(call_sid)
            }
    except ValueError:
        pass  # Not an index, try name matching
    
    # Try fuzzy name matching
    cart_item_names = [item["name"] for item in cart]
    best_match, score = process.extractOne(item_identifier, cart_item_names)
    
    if score >= 80:
        for i, item in enumerate(cart):
            if item["name"] == best_match:
                removed = cart.pop(i)
                logger.info(f"Removed '{removed['name']}' from cart for {call_sid}")
                return {
                    "status": "SUCCESS",
                    "message_for_agent": f"I've removed the {removed['name']} from your order.",
                    "removed_item": removed,
                    "updated_cart": self.get_cart(call_sid)
                }
    
    return {
        "status": "NOT_FOUND",
        "message_for_agent": f"I couldn't find '{item_identifier}' in your cart. Would you like me to read what you have?"
    }
```

#### 1.2 Update Item Quantity
**File:** `app/handlers/order_manager.py`

```python
def update_quantity(self, call_sid: str, item_identifier: str, new_quantity: int) -> Dict[str, Any]:
    """
    Updates the quantity of an item in the cart.
    
    Args:
        call_sid: The call session ID
        item_identifier: Item name or index
        new_quantity: New quantity (must be >= 1)
    
    Returns:
        {
            "status": "SUCCESS" | "NOT_FOUND" | "INVALID_QUANTITY" | "ERROR",
            "message_for_agent": str,
            "updated_item": Dict (if successful),
            "updated_cart": List[Dict]
        }
    """
    if new_quantity < 1:
        return {
            "status": "INVALID_QUANTITY",
            "message_for_agent": "The quantity must be at least 1. Did you mean to remove this item?"
        }
    
    if call_sid not in self.carts or not self.carts[call_sid]:
        return {
            "status": "ERROR",
            "message_for_agent": "Your cart is empty."
        }
    
    cart = self.carts[call_sid]
    
    # Try index first
    try:
        index = int(item_identifier)
        if 0 <= index < len(cart):
            old_quantity = cart[index]["quantity"]
            cart[index]["quantity"] = new_quantity
            logger.info(f"Updated quantity for item at index {index}: {old_quantity} → {new_quantity}")
            return {
                "status": "SUCCESS",
                "message_for_agent": f"I've changed the {cart[index]['name']} to {new_quantity} portion{'s' if new_quantity > 1 else ''}.",
                "updated_item": cart[index],
                "updated_cart": self.get_cart(call_sid)
            }
    except ValueError:
        pass
    
    # Try fuzzy name matching
    cart_item_names = [item["name"] for item in cart]
    best_match, score = process.extractOne(item_identifier, cart_item_names)
    
    if score >= 80:
        for item in cart:
            if item["name"] == best_match:
                old_quantity = item["quantity"]
                item["quantity"] = new_quantity
                logger.info(f"Updated quantity for '{item['name']}': {old_quantity} → {new_quantity}")
                return {
                    "status": "SUCCESS",
                    "message_for_agent": f"I've changed the {item['name']} to {new_quantity} portion{'s' if new_quantity > 1 else ''}.",
                    "updated_item": item,
                    "updated_cart": self.get_cart(call_sid)
                }
    
    return {
        "status": "NOT_FOUND",
        "message_for_agent": f"I couldn't find '{item_identifier}' in your cart."
    }
```

#### 1.3 Edit Cart Item (Restart Configuration)
**File:** `app/handlers/order_manager.py`

```python
def edit_cart_item(self, call_sid: str, item_identifier: str) -> Dict[str, Any]:
    """
    Removes an item from cart and restarts its configuration process.
    This allows the user to change options like flavor, spice level, etc.
    
    Args:
        call_sid: The call session ID
        item_identifier: Item name or index
    
    Returns:
        {
            "status": "SUCCESS" | "NOT_FOUND" | "ERROR",
            "message_for_agent": str,
            "item_to_reconfigure": Dict (dish details for restart),
            "original_quantity": int
        }
    """
    if call_sid not in self.carts or not self.carts[call_sid]:
        return {
            "status": "ERROR",
            "message_for_agent": "Your cart is empty."
        }
    
    cart = self.carts[call_sid]
    item_to_edit = None
    item_index = -1
    
    # Try index first
    try:
        index = int(item_identifier)
        if 0 <= index < len(cart):
            item_to_edit = cart[index]
            item_index = index
    except ValueError:
        pass
    
    # Try fuzzy name matching
    if not item_to_edit:
        cart_item_names = [item["name"] for item in cart]
        best_match, score = process.extractOne(item_identifier, cart_item_names)
        
        if score >= 80:
            for i, item in enumerate(cart):
                if item["name"] == best_match:
                    item_to_edit = item
                    item_index = i
                    break
    
    if not item_to_edit:
        return {
            "status": "NOT_FOUND",
            "message_for_agent": f"I couldn't find '{item_identifier}' in your cart."
        }
    
    # Remove from cart
    cart.pop(item_index)
    
    # NOTE: The calling code will need to fetch dish_details from the menu
    # and call start_item() or combo manager to restart configuration
    
    return {
        "status": "SUCCESS",
        "message_for_agent": f"Let's update the {item_to_edit['name']}. I'll ask you the questions again.",
        "item_to_reconfigure": item_to_edit,
        "original_quantity": item_to_edit.get("quantity", 1)
    }
```

#### 1.4 Get Cart Summary (Enhanced)
**File:** `app/handlers/order_manager.py`

```python
def get_cart_summary(self, call_sid: str) -> Dict[str, Any]:
    """
    Returns a detailed, user-friendly summary of the cart.
    
    Returns:
        {
            "item_count": int,
            "items": List[Dict],
            "summary_text": str (for TTS)
        }
    """
    cart = self.get_cart(call_sid)
    
    if not cart:
        return {
            "item_count": 0,
            "items": [],
            "summary_text": "Your cart is empty."
        }
    
    summary_parts = []
    for i, item in enumerate(cart, 1):
        qty = item.get("quantity", 1)
        name = item.get("name", "item")
        
        item_desc = f"{qty} {name}" if qty > 1 else name
        
        # Add options summary for combos
        options = item.get("options", {})
        if options and isinstance(options, dict):
            option_parts = []
            if "proteins" in options and options["proteins"]:
                option_parts.append(f"proteins: {', '.join(options['proteins'])}")
            if "Pick your flavor" in options:
                option_parts.append(f"flavor: {options['Pick your flavor']}")
            if "Pick your spicy level" in options:
                option_parts.append(f"spice: {options['Pick your spicy level']}")
            
            if option_parts:
                item_desc += f" ({', '.join(option_parts)})"
        
        summary_parts.append(f"{i}. {item_desc}")
    
    summary_text = "Your order has:\n" + "\n".join(summary_parts)
    
    return {
        "item_count": len(cart),
        "items": cart,
        "summary_text": summary_text
    }
```

### Phase 2: AI Tool Definitions

#### 2.1 Remove Item Tool
**File:** `app/handlers/common_tool_defs.py`

```python
REMOVE_CART_ITEM_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "remove_cart_item",
    "description": "Removes a specific item from the cart. Use when customer says 'remove', 'take out', 'delete', etc.",
    "parameters": {
        "type": "object",
        "properties": {
            "item_identifier": {
                "type": "string",
                "description": "The name of the item to remove (e.g., 'oyster', 'combo', 'fries'). Can also be 'last' to remove the most recent item."
            }
        },
        "required": ["item_identifier"]
    }
}
```

#### 2.2 Update Quantity Tool
**File:** `app/handlers/common_tool_defs.py`

```python
UPDATE_CART_QUANTITY_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "update_cart_quantity",
    "description": "Changes the quantity of an item in the cart. Use when customer says 'make it 2', 'change to 3 portions', etc.",
    "parameters": {
        "type": "object",
        "properties": {
            "item_identifier": {
                "type": "string",
                "description": "The name of the item to update. Can be 'last' for the most recent item."
            },
            "new_quantity": {
                "type": "integer",
                "description": "The new quantity (must be 1 or greater).",
                "minimum": 1
            }
        },
        "required": ["item_identifier", "new_quantity"]
    }
}
```

#### 2.3 Edit Cart Item Tool
**File:** `app/handlers/common_tool_defs.py`

```python
EDIT_CART_ITEM_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "edit_cart_item",
    "description": "Allows customer to change options (flavor, spice, etc.) on a cart item by restarting its configuration. Use when customer says 'change the flavor', 'make it spicy', etc.",
    "parameters": {
        "type": "object",
        "properties": {
            "item_identifier": {
                "type": "string",
                "description": "The name of the item to edit. Can be 'last' for the most recent item."
            }
        },
        "required": ["item_identifier"]
    }
}
```

#### 2.4 View Cart Tool
**File:** `app/handlers/common_tool_defs.py`

```python
VIEW_CART_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "view_cart",
    "description": "Shows the customer what's currently in their cart. Use when customer asks 'what do I have?', 'what's in my order?', etc.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": []
    }
}
```

### Phase 3: Handler Functions

#### 3.1 Remove Item Handler
**File:** `app/handlers/english_tool_logic.py`

```python
async def handle_remove_cart_item(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str]
):
    """Handles removing an item from the cart."""
    item_identifier = input_data.get("item_identifier", "").strip()
    
    if not item_identifier:
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": "I'm sorry, I didn't catch which item you want to remove. Could you specify?"
        }
        await deepgram_service.send_json(response)
        return
    
    # Handle "last" as shorthand for most recent item
    if item_identifier.lower() == "last":
        cart = order_manager.get_cart(call_sid)
        if cart:
            item_identifier = str(len(cart) - 1)  # Use index of last item
        else:
            response = {
                "type": "FunctionCallResponse",
                "id": function_call_id,
                "name": function_name,
                "content": "Your cart is empty, there's nothing to remove."
            }
            await deepgram_service.send_json(response)
            return
    
    result = order_manager.remove_from_cart(call_sid, item_identifier)
    
    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": result.get("message_for_agent", "Item removed.")
    }
    await deepgram_service.send_json(response)
    logger.info(f"Remove cart item result for {call_sid}: {result['status']}")
```

#### 3.2 Update Quantity Handler
**File:** `app/handlers/english_tool_logic.py`

```python
async def handle_update_cart_quantity(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str]
):
    """Handles updating the quantity of a cart item."""
    item_identifier = input_data.get("item_identifier", "").strip()
    new_quantity = input_data.get("new_quantity")
    
    if not item_identifier or new_quantity is None:
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": "I need to know which item and what quantity. Could you clarify?"
        }
        await deepgram_service.send_json(response)
        return
    
    # Handle "last" shorthand
    if item_identifier.lower() == "last":
        cart = order_manager.get_cart(call_sid)
        if cart:
            item_identifier = str(len(cart) - 1)
        else:
            response = {
                "type": "FunctionCallResponse",
                "id": function_call_id,
                "name": function_name,
                "content": "Your cart is empty."
            }
            await deepgram_service.send_json(response)
            return
    
    result = order_manager.update_quantity(call_sid, item_identifier, new_quantity)
    
    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": result.get("message_for_agent", "Quantity updated.")
    }
    await deepgram_service.send_json(response)
    logger.info(f"Update quantity result for {call_sid}: {result['status']}")
```

#### 3.3 Edit Item Handler
**File:** `app/handlers/english_tool_logic.py`

```python
async def handle_edit_cart_item(
    function_call_id: str,
    function_name: str,
    input_data: Dict[str, Any],
    deepgram_service,
    call_sid: Optional[str],
    portal_id: str
):
    """Handles editing a cart item by restarting its configuration."""
    item_identifier = input_data.get("item_identifier", "").strip()
    
    if not item_identifier:
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": "Which item would you like to edit?"
        }
        await deepgram_service.send_json(response)
        return
    
    # Handle "last" shorthand
    if item_identifier.lower() == "last":
        cart = order_manager.get_cart(call_sid)
        if cart:
            item_identifier = str(len(cart) - 1)
        else:
            response = {
                "type": "FunctionCallResponse",
                "id": function_call_id,
                "name": function_name,
                "content": "Your cart is empty."
            }
            await deepgram_service.send_json(response)
            return
    
    result = order_manager.edit_cart_item(call_sid, item_identifier)
    
    if result["status"] != "SUCCESS":
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": result.get("message_for_agent", "Couldn't find that item.")
        }
        await deepgram_service.send_json(response)
        return
    
    # Fetch dish details from menu to restart configuration
    item_to_reconfigure = result["item_to_reconfigure"]
    dish_name = item_to_reconfigure["name"]
    quantity = result["original_quantity"]
    
    found_dishes = await find_dish_by_english_name(portal_id, dish_name)
    
    if not found_dishes:
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": f"I'm sorry, I couldn't find '{dish_name}' in the menu. Let's try something else."
        }
        await deepgram_service.send_json(response)
        return
    
    dish_details = found_dishes[0].get("dish_details", {})
    
    # Check if combo or standard item and route appropriately
    if "combo" in dish_name.lower():
        live_menu_response = await get_extracted_dishes(portal_id)
        live_menu_data = live_menu_response.get("data", [])
        restart_response = combo_order_manager.start_combo_order(dish_name, call_sid, live_menu_data)
        message = result["message_for_agent"] + " " + restart_response.get("message_for_agent", "")
    else:
        restart_response = order_manager.start_item(dish_details, quantity, call_sid)
        message = result["message_for_agent"] + " " + restart_response.get("message_for_agent", "")
    
    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": message
    }
    await deepgram_service.send_json(response)
    logger.info(f"Edit cart item: restarted configuration for '{dish_name}'")
```

#### 3.4 View Cart Handler
**File:** `app/handlers/english_tool_logic.py`

```python
async def handle_view_cart(
    function_call_id: str,
    function_name: str,
    deepgram_service,
    call_sid: Optional[str]
):
    """Handles viewing the cart contents."""
    summary = order_manager.get_cart_summary(call_sid)
    
    response = {
        "type": "FunctionCallResponse",
        "id": function_call_id,
        "name": function_name,
        "content": summary["summary_text"]
    }
    await deepgram_service.send_json(response)
    logger.info(f"View cart for {call_sid}: {summary['item_count']} items")
```

### Phase 4: Integration into Main Handler

**File:** `app/handlers/english_tool_logic.py`

Add to `handle_function_call()`:

```python
async def handle_function_call(
    function_request: Dict[str, Any],
    deepgram_service,
    websocket: WebSocket,
    stream_sid: str,
    caller_phone: Optional[str],
    call_sid: Optional[str],
    client_id: Optional[str]
):
    function_name = function_request.get("function_name", "")
    function_call_id = function_request.get("function_call_id", "")
    input_data = function_request.get("input", {})
    
    # ... existing code ...
    
    # Add new handlers
    elif function_name == "remove_cart_item":
        await handle_remove_cart_item(
            function_call_id,
            function_name,
            input_data,
            deepgram_service,
            call_sid
        )
    elif function_name == "update_cart_quantity":
        await handle_update_cart_quantity(
            function_call_id,
            function_name,
            input_data,
            deepgram_service,
            call_sid
        )
    elif function_name == "edit_cart_item":
        portal_id = THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT if client_id == "LIMF" else None
        if not portal_id:
            logger.error(f"Cannot edit cart item: portal_id missing for client {client_id}")
            return
        await handle_edit_cart_item(
            function_call_id,
            function_name,
            input_data,
            deepgram_service,
            call_sid,
            portal_id
        )
    elif function_name == "view_cart":
        await handle_view_cart(
            function_call_id,
            function_name,
            deepgram_service,
            call_sid
        )
    # ... rest of existing code ...
```

---

## Testing Checklist

### Unit Tests

- [ ] `test_remove_from_cart_by_name()`
- [ ] `test_remove_from_cart_by_index()`
- [ ] `test_remove_from_cart_fuzzy_match()`
- [ ] `test_remove_from_cart_not_found()`
- [ ] `test_remove_from_empty_cart()`
- [ ] `test_update_quantity_by_name()`
- [ ] `test_update_quantity_by_index()`
- [ ] `test_update_quantity_invalid()`
- [ ] `test_update_quantity_not_found()`
- [ ] `test_edit_cart_item_standard()`
- [ ] `test_edit_cart_item_combo()`
- [ ] `test_edit_cart_item_not_found()`
- [ ] `test_get_cart_summary_empty()`
- [ ] `test_get_cart_summary_with_items()`

### Integration Tests

- [ ] Test full flow: Add → Remove → Verify cart
- [ ] Test full flow: Add → Update quantity → Verify cart
- [ ] Test full flow: Add combo → Edit → Reconfigure → Verify cart
- [ ] Test "last" identifier for most recent item
- [ ] Test fuzzy matching with typos ("oister" → "oyster")
- [ ] Test multiple items with similar names
- [ ] Test editing item with no required options
- [ ] Test cart persistence across function calls

### End-to-End Tests

**Scenario 1: Simple Remove**
```
User: "I'll have an oyster"
AI: "Okay, I've added 1 Raw Oyster to your order..."
User: "Actually, remove that"
AI: [calls remove_cart_item("last")]
AI: "I've removed the Raw Oyster from your order. What would you like instead?"
```

**Scenario 2: Change Quantity**
```
User: "Two oysters please"
AI: "Okay, I've added 2 Raw Oysters..."
User: "Make it 3"
AI: [calls update_cart_quantity("oyster", 3)]
AI: "I've changed the Raw Oyster to 3 portions."
```

**Scenario 3: Edit Combo**
```
User: "I'll have combo #1"
AI: [guides through configuration]
AI: "I've added COMBO #1 to your order..."
User: "Can I change the flavor to mild?"
AI: [calls edit_cart_item("combo #1")]
AI: "Let's update the COMBO #1. What would you
