import logging
from typing import Dict, Any, Optional, List
from thefuzz import process

logger = logging.getLogger(__name__)

def _get_english_name(name_dict: Dict[str, Any], default: str = "the item") -> str:
    """
    Safely retrieves the English name from a name dictionary,
    falling back to the Chinese name if the English one is null or empty.
    This is a specific workaround for the current menu data source.
    """
    if not name_dict:
        return default
    name_en = name_dict.get("en")
    if name_en and name_en.strip():
        return name_en
    # Fallback to 'zh' field if 'en' is missing, as per restaurant's data structure
    name_zh = name_dict.get("zh")
    if name_zh and name_zh.strip():
        return name_zh
    return default

class OrderManager:
    def __init__(self):
        # Stores items that are actively being configured (e.g., choosing options)
        self.active_items: Dict[str, Dict[str, Any]] = {}
        # Stores the final cart for each call
        self.carts: Dict[str, List[Dict[str, Any]]] = {}
        # Stores pending ambiguous menu items awaiting user clarification
        self.pending_ambiguous_items: Dict[str, Dict[str, Any]] = {}

    def is_active(self, call_sid: str) -> bool:
        """Check if an item is being configured for a given call_sid."""
        return call_sid in self.active_items

    def start_item(self, dish_details: Dict[str, Any], quantity: int, call_sid: str) -> Dict[str, Any]:
        """
        Starts the process of configuring a new menu item that has required options.
        """
        name_en = _get_english_name(dish_details.get("name"), "the item")
        
        # For testing, include all option groups, not just required ones.
        all_option_groups = dish_details.get("optionGroups", [])

        if not all_option_groups:
            return {"status": "ERROR", "message_for_agent": f"The item '{name_en}' has no options to configure."}

        self.active_items[call_sid] = {
            "item_name": name_en,
            "quantity": quantity,
            "dish_details": dish_details,
            "option_groups_to_process": all_option_groups,
            "current_step": 0,
            "selections": {}
        }
        logger.info(f"Started item '{name_en}' for call {call_sid}. Asking for {len(all_option_groups)} option groups.")
        
        return self.get_next_question(call_sid)

    def process_selection(self, user_input: str, call_sid: str) -> Dict[str, Any]:
        """
        Processes the user's selection for the current option step.
        """
        if not self.is_active(call_sid):
            return {"status": "ERROR", "message_for_agent": "There is no active item to make a selection for."}

        item = self.active_items[call_sid]
        current_group = item["option_groups_to_process"][item["current_step"]]
        group_name = _get_english_name(current_group.get("name"), "the current option")

        raw_options = [_get_english_name(opt.get("name"), "") for opt in current_group.get("options", [])]
        options = [opt for opt in raw_options if opt]

        if not options:
            logger.error(f"No valid English or Chinese options found for group '{group_name}' in item '{item['item_name']}' for call {call_sid}.")
            return {"status": "ERROR", "message_for_agent": f"I'm sorry, there seems to be an issue with the options for {group_name}."}

        # Handle the "no extras" case for both optional groups
        is_optional_extras_group = "pick your extras" in group_name.lower() or "extra sauce" in group_name.lower()
        user_wants_to_skip = "no" in user_input.lower() or "none" in user_input.lower() or "skip" in user_input.lower()
        
        if is_optional_extras_group and user_wants_to_skip:
            logger.info(f"User declined optional group '{group_name}' for call {call_sid}.")
            # Don't store any selection, just advance to the next step
            item["current_step"] += 1
            return self.get_next_question(call_sid)

        best_match, score = process.extractOne(user_input, options)

        if score < 80:
            options_str = ", ".join(options)
            return {
                "status": "REPROMPT",
                "message_for_agent": f"I'm sorry, I didn't understand that. For {group_name}, your options are: {options_str}. Which would you like?"
            }
        
        # Store the selection
        item["selections"][group_name] = best_match
        logger.info(f"Selection made for call {call_sid}: {group_name} = {best_match}")
        
        # Advance to the next step
        item["current_step"] += 1
        
        return self.get_next_question(call_sid)

    def get_next_question(self, call_sid: str) -> Dict[str, Any]:
        """
        Determines the next question to ask the user or finalizes the item.
        """
        if not self.is_active(call_sid):
            return {"status": "ERROR", "message_for_agent": "There is no active item to get the next question for."}

        item = self.active_items[call_sid]
        
        # Check if all required steps are completed
        if item["current_step"] >= len(item["option_groups_to_process"]):
            # Finalize the item and add it to the cart
            self.add_item_to_cart(call_sid, item["item_name"], item["quantity"], item["selections"])
            self.clear_active_item(call_sid)
            
            selections_summary = ", ".join([f"{v}" for k, v in item['selections'].items()])
            return {
                "status": "ITEM_COMPLETE",
                "message_for_agent": f"Okay, I've added {item['quantity']} {item['item_name']} with {selections_summary} to your order. What else can I get for you?",
                "current_cart": self.get_cart(call_sid)
            }

        # Ask the next question
        current_group = item["option_groups_to_process"][item["current_step"]]
        group_name = _get_english_name(current_group.get("name"), "the next option")
        
        raw_options = [_get_english_name(opt.get("name"), "") for opt in current_group.get("options", [])]
        options = [opt for opt in raw_options if opt]

        if not options:
            logger.error(f"No valid English or Chinese options found for group '{group_name}' in item '{item['item_name']}' for call {call_sid} to ask the next question.")
            self.clear_active_item(call_sid)
            return {"status": "ERROR", "message_for_agent": f"I'm sorry, there was an issue retrieving the options for {group_name}. Let's try adding that item again later. What else can I get for you?"}

        options_str = ", ".join(options)
        message_for_agent = f"For the {item['item_name']}, what would you like for {group_name}? Your options are: {options_str}."

        # Add skip option for both optional extras groups
        if "pick your extras" in group_name.lower():
            message_for_agent += " You can also say 'no extras' to skip."
        elif "extra sauce" in group_name.lower():
            message_for_agent += " You can also say 'no extra sauce' to skip."

        return {
            "status": "AWAITING_SELECTION",
            "message_for_agent": message_for_agent
        }

    def add_item_to_cart(self, call_sid: str, item_name: str, quantity: int, options: Dict[str, Any]):
        """Adds a fully configured item to the cart."""
        if call_sid not in self.carts:
            self.carts[call_sid] = []
        
        # Simple logic for now, can be expanded to handle updates
        self.carts[call_sid].append({
            "name": item_name,
            "quantity": quantity,
            "options": options
        })
        logger.info(f"Added '{item_name}' to cart for call {call_sid}.")

    def get_cart(self, call_sid: str) -> List[Dict[str, Any]]:
        """Retrieves the current cart for a call."""
        return self.carts.get(call_sid, [])

    def clear_cart(self, call_sid: str):
        """Clears the cart for a call."""
        if call_sid in self.carts:
            del self.carts[call_sid]
            logger.info(f"Cleared cart for call_sid: {call_sid}")

    def clear_active_item(self, call_sid: str):
        """Clears the active item being configured."""
        if call_sid in self.active_items:
            del self.active_items[call_sid]

    def set_pending_ambiguous(self, call_sid: str, ambiguous_matches: List[str], matched_dishes: List[Dict[str, Any]], original_query: str, quantity: int = 1):
        """
        Store pending ambiguous menu items awaiting user clarification.
        
        Args:
            call_sid: The call session ID
            ambiguous_matches: List of English dish names that matched
            matched_dishes: Full dish details for each match
            original_query: The user's original query
            quantity: Quantity requested
        """
        self.pending_ambiguous_items[call_sid] = {
            "ambiguous_matches": ambiguous_matches,
            "matched_dishes": matched_dishes,
            "original_query": original_query,
            "quantity": quantity
        }
        logger.info(f"Set pending ambiguous items for {call_sid}: {len(ambiguous_matches)} matches for '{original_query}'")

    def has_pending_ambiguous(self, call_sid: str) -> bool:
        """Check if there's a pending ambiguous selection for this call."""
        return call_sid in self.pending_ambiguous_items

    def resolve_ambiguous(self, call_sid: str, user_input: str) -> Dict[str, Any]:
        """
        Resolve the pending ambiguous selection based on user input.
        
        Returns:
            {
                "status": "SUCCESS" | "REPROMPT" | "ERROR",
                "message_for_agent": str,
                "selected_dish": Dict (if successful),
                "quantity": int
            }
        """
        if not self.has_pending_ambiguous(call_sid):
            return {
                "status": "ERROR",
                "message_for_agent": "There is no pending menu selection to resolve."
            }
        
        pending = self.pending_ambiguous_items[call_sid]
        ambiguous_matches = pending["ambiguous_matches"]
        matched_dishes = pending["matched_dishes"]
        quantity = pending["quantity"]
        
        # Try to parse ordinal numbers (first, second, third, etc.)
        ordinal_map = {
            "first": 0, "1st": 0, "one": 0, "1": 0,
            "second": 1, "2nd": 1, "two": 1, "2": 1,
            "third": 2, "3rd": 2, "three": 2, "3": 2,
            "fourth": 3, "4th": 3, "four": 3, "4": 3,
            "fifth": 4, "5th": 4, "five": 4, "5": 5
        }
        
        user_input_lower = user_input.lower().strip()
        
        # Check for ordinal selection
        for key, index in ordinal_map.items():
            if key in user_input_lower:
                if index < len(matched_dishes):
                    selected_dish = matched_dishes[index]
                    selected_name = ambiguous_matches[index]
                    
                    # Clear the pending state
                    del self.pending_ambiguous_items[call_sid]
                    
                    logger.info(f"Resolved ambiguous selection for {call_sid}: '{selected_name}' (index {index})")
                    
                    return {
                        "status": "SUCCESS",
                        "selected_dish": selected_dish,
                        "selected_name": selected_name,
                        "quantity": quantity
                    }
        
        # Try fuzzy matching against the dish names
        best_match, score = process.extractOne(user_input, ambiguous_matches)
        
        if score >= 80:
            # Find the corresponding dish details
            for i, name in enumerate(ambiguous_matches):
                if name == best_match:
                    selected_dish = matched_dishes[i]
                    
                    # Clear the pending state
                    del self.pending_ambiguous_items[call_sid]
                    
                    logger.info(f"Resolved ambiguous selection for {call_sid}: '{best_match}' (fuzzy match, score {score})")
                    
                    return {
                        "status": "SUCCESS",
                        "selected_dish": selected_dish,
                        "selected_name": best_match,
                        "quantity": quantity
                    }
        
        # Reprompt if we couldn't understand
        options_str = ", ".join([f"{i+1}. {name}" for i, name in enumerate(ambiguous_matches)])
        return {
            "status": "REPROMPT",
            "message_for_agent": f"I'm sorry, I didn't catch that. Which one would you like? {options_str}"
        }

    def clear_pending_ambiguous(self, call_sid: str):
        """Clear the pending ambiguous state."""
        if call_sid in self.pending_ambiguous_items:
            del self.pending_ambiguous_items[call_sid]
            logger.info(f"Cleared pending ambiguous items for {call_sid}")

    def remove_from_cart(self, call_sid: str, item_identifier: str) -> Dict[str, Any]:
        """
        Removes an item from the cart by name or index.
        
        Args:
            call_sid: The call session ID
            item_identifier: Either item name (str) or index (int as str: "0", "1", etc.) or "last"
        
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
        
        # Handle "last" keyword
        if item_identifier.lower() == "last":
            if cart:
                removed = cart.pop()
                logger.info(f"Removed last item from cart for {call_sid}: {removed['name']}")
                return {
                    "status": "SUCCESS",
                    "message_for_agent": f"I've removed the {removed['name']} from your order.",
                    "removed_item": removed,
                    "updated_cart": self.get_cart(call_sid)
                }
        
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

    def update_quantity(self, call_sid: str, item_identifier: str, new_quantity: int) -> Dict[str, Any]:
        """
        Updates the quantity of an item in the cart.
        
        Args:
            call_sid: The call session ID
            item_identifier: Item name or index or "last"
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
        
        # Handle "last" keyword
        if item_identifier.lower() == "last":
            if cart:
                index = len(cart) - 1
                old_quantity = cart[index]["quantity"]
                cart[index]["quantity"] = new_quantity
                logger.info(f"Updated quantity for last item: {old_quantity} → {new_quantity}")
                return {
                    "status": "SUCCESS",
                    "message_for_agent": f"I've changed the {cart[index]['name']} to {new_quantity} portion{'s' if new_quantity > 1 else ''}.",
                    "updated_item": cart[index],
                    "updated_cart": self.get_cart(call_sid)
                }
        
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

    def edit_cart_item(self, call_sid: str, item_identifier: str) -> Dict[str, Any]:
        """
        Removes an item from cart and restarts its configuration process.
        This allows the user to change options like flavor, spice level, etc.
        
        Args:
            call_sid: The call session ID
            item_identifier: Item name or index or "last"
        
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
        
        # Handle "last" keyword
        if item_identifier.lower() == "last":
            if cart:
                item_index = len(cart) - 1
                item_to_edit = cart[item_index]
        else:
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

# Singleton instance
order_manager = OrderManager()
