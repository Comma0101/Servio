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

        # Handle the "no extras" case for the optional extras group
        if "pick your extras" in group_name.lower() and "no" in user_input.lower() and "extra" in user_input.lower():
            logger.info(f"User selected 'no extras' for call {call_sid}.")
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

        # Check if the group is the optional extras group and add the "no extras" option.
        if "pick your extras" in group_name.lower():
            message_for_agent += " You can also say 'no extras'."

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

# Singleton instance
order_manager = OrderManager()
