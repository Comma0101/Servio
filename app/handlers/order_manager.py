import logging
from typing import Dict, Any, Optional, List
from thefuzz import process
from app.utils.text_normalization import normalize_for_matching, normalize_options_for_matching, clean_option_group_name

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
        # NEW: Track items awaiting confirmation
        self.items_awaiting_confirmation: Dict[str, Dict[str, Any]] = {}
        # NEW: Track pending option clarifications during modification
        # Format: {call_sid: {"option_group": "FLAVOR", "awaiting_value": True}}
        self.pending_option_clarification: Dict[str, Dict[str, Any]] = {}

    def is_active(self, call_sid: str) -> bool:
        """Check if an item is being configured for a given call_sid."""
        return call_sid in self.active_items

    def is_awaiting_confirmation(self, call_sid: str) -> bool:
        """Check if an item is awaiting confirmation."""
        return call_sid in self.items_awaiting_confirmation

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
        
        # Build complete summary with cleaned option group names
        summary_parts = []
        summary_parts.append(f"I have {quantity} {item_name}")
        
        for option_group, selection in selections.items():
            # Clean the option group name for natural speech
            cleaned_group_name = clean_option_group_name(option_group)
            summary_parts.append(f"{cleaned_group_name}: {selection}")
        
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
        user_input_lower = user_input.lower().strip()
        
        # Check if we're expecting a value for a specific option (stateful clarification)
        pending_clarification = self.pending_option_clarification.get(call_sid)
        if pending_clarification:
            detected_option = pending_clarification["option_group"]
            logger.info(f"Using pending clarification for option '{detected_option}'")
            # Clear the pending clarification
            del self.pending_option_clarification[call_sid]
            
            # IMPORTANT: Skip all detection logic and jump directly to value matching
            # We already know which option group to modify from pending state
            dish_details = item.get("dish_details", {})
            option_groups = dish_details.get("optionGroups", [])
            
            # Find the target group
            target_group = None
            for group in option_groups:
                group_name = _get_english_name(group.get("name", {}))
                if group_name == detected_option:
                    target_group = group
                    break
            
            if not target_group:
                return {
                    "status": "ERROR",
                    "message_for_agent": "I couldn't find that option to change."
                }
            
            # Extract options and match the user's value
            options_in_group = [_get_english_name(opt.get("name", {})) for opt in target_group.get("options", [])]
            
            # Use fuzzy matching to find the selection
            normalized_input = normalize_for_matching(user_input)
            normalized_options, norm_to_orig_map = normalize_options_for_matching(options_in_group)
            
            best_match_normalized, score = process.extractOne(normalized_input, normalized_options)
            
            if score < 70:
                # Still can't find a good match - ask again
                cleaned_option_name = clean_option_group_name(detected_option)
                options_str = ", ".join(options_in_group)
                
                # Re-store the pending clarification for another attempt
                self.pending_option_clarification[call_sid] = {
                    "option_group": detected_option,
                    "awaiting_value": True
                }
                logger.info(f"Re-set pending clarification for option '{detected_option}' - user input still unclear")
                
                return {
                    "status": "NEED_CLARIFICATION",
                    "message_for_agent": f"I'm sorry, I didn't catch that. For {cleaned_option_name}, your options are: {options_str}. Which would you like?"
                }
            
            # Get original option name
            best_match = norm_to_orig_map[best_match_normalized]
            
            # Update the selection
            item["selections"][detected_option] = best_match
            logger.info(f"Modified {detected_option} to {best_match} for call {call_sid}")
            
            # Build updated summary with cleaned option group names
            summary_parts = []
            summary_parts.append(f"I have {item.get('quantity', 1)} {item.get('item_name')}")
            
            for option_group, selection in item.get("selections", {}).items():
                # Clean the option group name for natural speech
                cleaned_group_name = clean_option_group_name(option_group)
                summary_parts.append(f"{cleaned_group_name}: {selection}")
            
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
        
        # No pending clarification - proceed with normal modification flow
        # Check if user wants to cancel/finalize without more changes
        cancel_phrases = ["no", "that's it", "i'm done", "done changing", "nothing else", "no changes", "looks good"]
        if not pending_clarification and any(phrase in user_input_lower for phrase in cancel_phrases):
            # Only treat as confirmation if it's clear they're done (not a simple "no" in other contexts)
            if any(phrase in user_input_lower for phrase in ["that's it", "i'm done", "done changing", "nothing else", "no changes"]):
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
        
        # Check if user is confirming (yes/correct/good/etc)
        confirmation_phrases = ["yes", "correct", "right", "good", "yep", "yeah", "that's right", "sounds good", "perfect"]
        if not pending_clarification and any(phrase in user_input_lower for phrase in confirmation_phrases):
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
        
        # Extract option group names and create mappings
        available_options = {}  # {lowercase_name: group}
        option_name_map = {}  # {lowercase_name: original_name}
        
        for group in option_groups:
            group_name = _get_english_name(group.get("name", {}))
            if group_name:
                available_options[group_name.lower()] = group
                option_name_map[group_name.lower()] = group_name
        
        # NEW: Parse "change X to Y" or just "X to Y" patterns where X is current value, Y is new value
        import re
        from thefuzz import fuzz
        
        # Make "change" prefix optional to handle Deepgram AI preprocessing
        change_pattern = r'(?:change\s+)?(.+?)\s+to\s+(.+)'
        match = re.search(change_pattern, user_input_lower)
        
        detected_option = None
        new_value_hint = None
        current_selections = item.get("selections", {})
        
        if match:
            # Extract what user wants to change FROM and TO
            from_value = match.group(1).strip()
            new_value_hint = match.group(2).strip()
            
            logger.info(f"Parsed modification request: from='{from_value}' to='{new_value_hint}'")
            
            # Strategy 1: Exact substring match in current selections
            for option_group, current_selection in current_selections.items():
                if current_selection and from_value in current_selection.lower():
                    detected_option = option_group
                    logger.info(f"Strategy 1: Detected option '{option_group}' by exact substring match '{from_value}' in '{current_selection}'")
                    break
            
            # Strategy 2: Fuzzy match from_value against current selections
            if not detected_option:
                best_match_score = 0
                best_match_group = None
                for option_group, current_selection in current_selections.items():
                    if current_selection:
                        score = fuzz.partial_ratio(from_value, current_selection.lower())
                        if score > 70 and score > best_match_score:
                            best_match_score = score
                            best_match_group = option_group
                
                if best_match_group:
                    detected_option = best_match_group
                    logger.info(f"Strategy 2: Detected option '{detected_option}' by fuzzy matching '{from_value}' (score: {best_match_score})")
            
            # Strategy 3: Search through ALL options in ALL groups for a match
            if not detected_option:
                best_match_score = 0
                best_match_group = None
                
                for group_name_lower, group_data in available_options.items():
                    options_in_group = [_get_english_name(opt.get("name", {})) for opt in group_data.get("options", [])]
                    
                    for opt in options_in_group:
                        if opt:
                            opt_lower = opt.lower()
                            # Check exact substring first
                            if from_value in opt_lower or opt_lower in from_value:
                                detected_option = option_name_map[group_name_lower]
                                logger.info(f"Strategy 3a: Detected option '{detected_option}' by finding '{from_value}' in option '{opt}'")
                                break
                            
                            # Check fuzzy match
                            score = fuzz.partial_ratio(from_value, opt_lower)
                            if score > 70 and score > best_match_score:
                                best_match_score = score
                                best_match_group = option_name_map[group_name_lower]
                    
                    if detected_option:
                        break
                
                if not detected_option and best_match_group:
                    detected_option = best_match_group
                    logger.info(f"Strategy 3b: Detected option '{detected_option}' by fuzzy matching '{from_value}' against all options (score: {best_match_score})")
            
            # Strategy 4: Match as option group keyword
            if not detected_option:
                for option_name_lower in available_options.keys():
                    if from_value in option_name_lower or option_name_lower in from_value:
                        detected_option = option_name_map[option_name_lower]
                        logger.info(f"Strategy 4: Detected option '{detected_option}' by keyword match")
                        break
                
                # Check common keyword variations
                if not detected_option:
                    if "flavor" in from_value or "favour" in from_value or "favor" in from_value:
                        detected_option = next((name for name_lower, name in option_name_map.items() if "flavor" in name_lower), None)
                        if detected_option:
                            logger.info(f"Strategy 4: Detected option '{detected_option}' by flavor keyword")
                    elif "spicy" in from_value or "spice" in from_value:
                        detected_option = next((name for name_lower, name in option_name_map.items() if "spicy" in name_lower or "spice" in name_lower), None)
                        if detected_option:
                            logger.info(f"Strategy 4: Detected option '{detected_option}' by spice keyword")
        else:
            # No "change X to Y" pattern - try to detect option from user input (original logic)
            for option_name_lower, group in available_options.items():
                if option_name_lower in user_input_lower:
                    detected_option = option_name_map[option_name_lower]
                    break
            
            # Also check for common keywords
            if not detected_option:
                if "flavor" in user_input_lower or "favour" in user_input_lower:
                    detected_option = next((name for name_lower, name in option_name_map.items() if "flavor" in name_lower), None)
                elif "spicy" in user_input_lower or "spice" in user_input_lower:
                    detected_option = next((name for name_lower, name in option_name_map.items() if "spicy" in name_lower or "spice" in name_lower), None)
            
            # FALLBACK: If still not detected and there's only ONE option, use it
            if not detected_option and len(available_options) == 1:
                detected_option = list(option_name_map.values())[0]
                logger.info(f"Auto-detected single option group: {detected_option}")
            
            # NEW: If we detected an option but user input seems to be ONLY the option name (no value),
            # immediately prompt for the value instead of trying to match against option values
            if detected_option:
                # Use clean_option_group_name to normalize the detected option for better comparison
                cleaned_detected_option = clean_option_group_name(detected_option).lower()
                
                # Normalize user input for comparison
                normalized_user_input = normalize_for_matching(user_input)
                
                # Calculate similarity between normalized inputs
                from thefuzz import fuzz
                similarity = fuzz.ratio(normalized_user_input, cleaned_detected_option)
                
                # If similarity is high (>70), user likely said just the option name
                if similarity > 70:
                    logger.info(f"User input '{user_input}' appears to be just option name '{detected_option}' (normalized: '{normalized_user_input}' vs '{cleaned_detected_option}', similarity: {similarity})")
                    
                    # Get the target group and immediately prompt for value
                    target_group = available_options.get(detected_option.lower())
                    if target_group:
                        options_in_group = [_get_english_name(opt.get("name", {})) for opt in target_group.get("options", [])]
                        cleaned_option_name = clean_option_group_name(detected_option)
                        options_str = ", ".join(options_in_group)
                        
                        # Set pending clarification
                        self.pending_option_clarification[call_sid] = {
                            "option_group": detected_option,
                            "awaiting_value": True
                        }
                        logger.info(f"Set pending clarification for option '{detected_option}' (user said option name only)")
                        
                        return {
                            "status": "NEED_CLARIFICATION",
                            "message_for_agent": f"For {cleaned_option_name}, your options are: {options_str}. Which would you like?"
                        }
        
        if not detected_option:
            # Ask for clarification with cleaned option names
            option_names = ", ".join([clean_option_group_name(_get_english_name(g.get("name", {}))) for g in option_groups])
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
        
        # Use the hint if we extracted it from "change X to Y", otherwise use full input
        search_text = new_value_hint if new_value_hint else user_input
        
        # Use fuzzy matching to find the selection
        normalized_input = normalize_for_matching(search_text)
        normalized_options, norm_to_orig_map = normalize_options_for_matching(options_in_group)
        
        best_match_normalized, score = process.extractOne(normalized_input, normalized_options)
        
        if score < 70:
            # Clean the option name for the error message
            cleaned_option_name = clean_option_group_name(detected_option)
            options_str = ", ".join(options_in_group)
            
            # Store the pending clarification so next user input is treated as value for this option
            self.pending_option_clarification[call_sid] = {
                "option_group": detected_option,
                "awaiting_value": True
            }
            logger.info(f"Set pending clarification for option '{detected_option}' on call {call_sid}")
            
            return {
                "status": "NEED_CLARIFICATION",
                "message_for_agent": f"For {cleaned_option_name}, your options are: {options_str}. Which would you like?"
            }
        
        # Get original option name
        best_match = norm_to_orig_map[best_match_normalized]
        
        # Update the selection
        item["selections"][detected_option] = best_match
        logger.info(f"Modified {detected_option} to {best_match} for call {call_sid}")
        
        # Build updated summary with cleaned option group names
        summary_parts = []
        summary_parts.append(f"I have {item.get('quantity', 1)} {item.get('item_name')}")
        
        for option_group, selection in item.get("selections", {}).items():
            # Clean the option group name for natural speech
            cleaned_group_name = clean_option_group_name(option_group)
            summary_parts.append(f"{cleaned_group_name}: {selection}")
        
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

        # Normalize user input and options for more robust matching
        normalized_input = normalize_for_matching(user_input)
        normalized_options, norm_to_orig_map = normalize_options_for_matching(options)
        
        best_match_normalized, score = process.extractOne(normalized_input, normalized_options)

        if score < 75:  # Lowered from 80 to 75 for better tolerance
            options_str = ", ".join(options)
            return {
                "status": "REPROMPT",
                "message_for_agent": f"I'm sorry, I didn't understand that. For {group_name}, your options are: {options_str}. Which would you like?"
            }
        
        # Get the original option name from the normalized match
        best_match = norm_to_orig_map[best_match_normalized]
        
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
            # NEW: Enter confirmation instead of immediately adding to cart
            return self.enter_confirmation_state(call_sid)

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
        
        # Clean the option group name for natural speech
        cleaned_group_name = clean_option_group_name(group_name)
        
        # Customize prompt based on option type
        if "flavor" in cleaned_group_name.lower():
            message_for_agent = f"For the {item['item_name']}, what flavor would you like? Your options are: {options_str}."
        elif "spicy" in cleaned_group_name.lower() or "spice" in cleaned_group_name.lower():
            message_for_agent = f"For the {item['item_name']}, what {cleaned_group_name} would you like? Your options are: {options_str}."
        else:
            message_for_agent = f"For the {item['item_name']}, what would you like for {cleaned_group_name}? Your options are: {options_str}."

        # Add skip option for optional extras groups
        if "extras" in cleaned_group_name.lower():
            message_for_agent += " You can also say 'no extras' to skip."
        elif "extra sauce" in cleaned_group_name.lower():
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
        
        # Try fuzzy matching against the dish names with normalization
        normalized_input = normalize_for_matching(user_input)
        normalized_matches, norm_to_orig_map = normalize_options_for_matching(ambiguous_matches)
        
        best_match_normalized, score = process.extractOne(normalized_input, normalized_matches)
        
        if score >= 75:  # Lowered from 80 to 75
            # Get the original dish name from the normalized match
            best_match = norm_to_orig_map[best_match_normalized]
            
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
        
        # Reprompt if we couldn't understand - use natural language format
        if len(ambiguous_matches) == 2:
            options_str = f"{ambiguous_matches[0]} or {ambiguous_matches[1]}"
        else:
            options_str = ", ".join(ambiguous_matches[:-1]) + f", or {ambiguous_matches[-1]}"
        
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
