import logging
from typing import Dict, Any, Optional, List
import json
import re
from thefuzz import process

logger = logging.getLogger(__name__)

class ComboOrderManager:
    def __init__(self):
        self.active_orders: Dict[str, Dict[str, Any]] = {}
        self.completed_combos: Dict[str, Dict[str, Any]] = {}
        # The static menu is no longer loaded here.

    FAMILY_COMBO_PROTEIN_CATEGORIES = {
        "Shrimp": ["Shrimp head on", "Shimp Headless", "Peeled Tail On"],
        "Crawfish": ["Frozen Crawfish", "Fresh crawfish"],
        "Mussels": ["Green mussels", "Black Mussels"],
        "Lobster": ["Whole Lobster", "1 PC Lobster Tail"],
        "Clams": ["Clams"],
        "Scallops": ["Scallops on the shell"]
    }

    def _is_protein_group(self, group_name: str) -> bool:
        """
        Identifies if an option group is for a protein selection in a fixed-price combo.
        It specifically looks for keywords that indicate a main choice, excluding free items.
        """
        if not group_name:
            return False
        
        group_name_lower = group_name.lower()
        
        # Exclude groups that are explicitly for free items.
        if "free" in group_name_lower:
            return False
            
        # Keywords that strongly indicate a protein choice in the context of these combos.
        protein_keywords = ["choose one", "included", "pick 1 pound", "choose your crab", "half a pound"]
        
        return any(keyword in group_name_lower for keyword in protein_keywords)

    def _is_free_item_group(self, group_name: str) -> bool:
        """Identifies if an option group is for a free item selection."""
        if not group_name:
            return False
        return "free" in group_name.lower()

    def add_completed_combo(self, call_sid: str, order: Dict[str, Any]):
        self.completed_combos[call_sid] = order

    def get_completed_combo(self, call_sid: str) -> Optional[Dict[str, Any]]:
        return self.completed_combos.get(call_sid)

    def is_active(self, call_sid: str) -> bool:
        """Check if a combo order is currently active for a given call_sid."""
        return call_sid in self.active_orders

    def _get_dish_by_name(self, dish_name: str, menu_data: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Finds a dish by name within the provided live menu data."""
        for item in menu_data:
            # The structure from get_extracted_dishes is a flat list of dish objects
            if item.get("dish_name_en", "").lower() == dish_name.lower():
                return item.get("dish_details") # Return the full details object
        return None

    def start_combo_order(self, dish_name: str, call_sid: str, live_menu_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Starts a combo order using live menu data."""
        dish_details = self._get_dish_by_name(dish_name, live_menu_data)
        if not dish_details:
            return {"message_for_agent": f"Sorry, I couldn't find {dish_name} on the menu."}

        self.active_orders[call_sid] = {
            "dish_name": dish_name,
            "dish_details": dish_details,
            "current_step": 0,
            "selections": {},
            "state": "STARTED"
        }

        if "customized combo" in dish_name.lower():
            self.active_orders[call_sid]["selections"]["proteins"] = []
            self.active_orders[call_sid]["state"] = "AWAITING_PROTEIN_CHOICE"
            return {
                "status": "PROMPT_FOR_PROTEIN",
                "message_for_agent": "The Customized Combo is a great choice! What protein would you like to add? Popular choices include Shrimp, Mussels, and Crab Legs. You can pick one of those, name another choice, or just say 'send the menu' and I'll text it to you.",
                "tool_to_use": "handle_combo_item_selection"
            }
        elif "family combo" in dish_name.lower():
            self.active_orders[call_sid]["selections"]["proteins"] = []
            self.active_orders[call_sid]["selections"]["crab"] = None
            self.active_orders[call_sid]["selections"]["free_items"] = []
            
            option_groups = dish_details.get("optionGroups", [])
            required_protein_count = sum(1 for g in option_groups if self._is_protein_group(self._get_display_name(g.get("name", {}))))
            self.active_orders[call_sid]["required_protein_count"] = required_protein_count

            self.active_orders[call_sid]["state"] = "AWAITING_PROTEIN_CATEGORY"
            protein_categories = list(self.FAMILY_COMBO_PROTEIN_CATEGORIES.keys())
            return {
                "status": "PROMPT_FOR_PROTEIN_CATEGORY",
                "message_for_agent": f"For the Family Combo, you get to pick 3 proteins, and 1 crab to choose. Let's start with the first one. You can choose from {', '.join(protein_categories)}. What type of seafood would you like?",
                "tool_to_use": "handle_combo_item_selection"
            }
        else:
            self.active_orders[call_sid]["selections"]["fixed_combo_selections"] = []
            # Initialize counters for contextual prompting
            self.active_orders[call_sid]["protein_choice_count"] = 0
            self.active_orders[call_sid]["free_item_count"] = 0
            
            # Pre-calculate the number of free items
            option_groups = dish_details.get("optionGroups", [])
            num_free_items = sum(1 for group in option_groups if self._is_free_item_group(self._get_display_name(group.get("name", {}))))
            self.active_orders[call_sid]["num_free_items"] = num_free_items

            self.active_orders[call_sid]["state"] = "AWAITING_OPTION_CHOICE"
            return self.get_next_question(call_sid)

    def process_selection(self, user_input: str, call_sid: str) -> Dict[str, Any]:
        if call_sid not in self.active_orders:
            return {"message_for_agent": "Sorry, I don't have an active combo order for you. Let's start over.", "tool_to_use": "handle_standard_item_selection"}

        order = self.active_orders[call_sid]
        
        response = {}
        if order["state"] == "AWAITING_PROTEIN_CATEGORY":
            response = self._handle_protein_category_selection(user_input, order, call_sid)
        elif order["state"] == "AWAITING_PROTEIN_OPTION_CHOICE":
            response = self._handle_protein_option_choice(user_input, order, call_sid)
        elif order["state"] == "AWAITING_CRAB_CHOICE":
            response = self._handle_crab_choice(user_input, order, call_sid)
        elif order["state"] == "AWAITING_FREE_ITEM_CHOICE":
            response = self._handle_free_item_choice(user_input, order, call_sid)
        elif order["state"] == "AWAITING_PROTEIN_CHOICE":
            response = self._handle_protein_selection(user_input, order, call_sid)
        elif order["state"] == "AWAITING_PROTEIN_CLARIFICATION":
            response = self._handle_protein_clarification(user_input, order, call_sid)
        elif order["state"] == "AWAITING_SIZE_CHOICE":
            response = self._handle_size_selection(user_input, order, call_sid)
        elif order["state"] == "AWAITING_OPTION_CHOICE":
            response = self._handle_option_selection(user_input, order, call_sid)
        elif order["state"] == "AWAITING_OPTIONAL_CHOICE_CONFIRMATION":
            response = self._handle_optional_choice_confirmation(user_input, order, call_sid)
        elif order["state"] == "AWAITING_INFO_DELIVERY_CHOICE":
            response = self._handle_info_delivery_choice(user_input, order, call_sid)
        elif order["state"] == "AWAITING_FINAL_CONFIRMATION":
            response = self._finalize_combo_and_prepare_for_cart(user_input, order, call_sid)
        elif order["state"] == "AWAITING_MORE_PROTEINS":
            response = self._handle_more_proteins(user_input, order, call_sid)
        elif order["state"] == "AWAITING_UPDATE":
            response = self.update_combo_selection(user_input, order, call_sid)
        else:
            response = {"status": "ERROR", "message_for_agent": "Invalid order state."}

        # Ensure all responses from this manager recommend the correct tool, unless the combo is finished.
        if order["state"] not in ["AWAITING_MORE_ITEMS_PROMPT", "AWAITING_FINAL_CONFIRMATION"]:
             response["tool_to_use"] = "handle_combo_item_selection"
        return response

    def _clean_protein_name(self, original_name: str) -> str:
        # This regex now handles "1lb", "half a pound", "half pound of", and similar variations.
        cleaned = re.sub(r'^(1\s?lb\.?|half(\s+a)?\s+pound(\s+of)?)\s*\(?', '', original_name, flags=re.IGNORECASE).strip()
        if cleaned.endswith(')'):
            cleaned = cleaned[:-1].strip()
        return cleaned

    def _clean_option_name_for_tts(self, original_name: str) -> str:
        """Removes prefixes like '1 lb.' for cleaner TTS prompts."""
        if not original_name:
            return ""
        # This regex removes variations of "1 lb", "1.5 Lb.", and leading numbers for cleaner TTS.
        cleaned = re.sub(r'^\d+(\.\d+)?\s?lb?\.?\s*', '', original_name, flags=re.IGNORECASE).strip()
        return cleaned

    def _clean_prompt_text(self, text: str) -> str:
        """Cleans up text for more natural-sounding prompts."""
        if not text:
            return ""
        # General replacements
        text = text.replace("PICK YOUR", "Now choose your")
        
        # Universal regex for 'pc' -> 'piece'/'pieces'
        text = re.sub(r'\(?(\d+)pc\)?', lambda m: f"{m.group(1)} piece" if m.group(1) == '1' else f"{m.group(1)} pieces", text)
        
        return text

    def _is_primarily_english(self, text: str) -> bool:
        """
        Checks if the text is primarily English characters.
        """
        if not text:
            return True
        # This regex allows basic Latin alphabet, digits, and common punctuation.
        return bool(re.fullmatch(r"^[a-zA-Z0-9\s!\"#$%&'()*+,-./:;<=>?@[\\\]^_`{|}~]*$", text))

    def _get_display_name(self, name_obj: Dict[str, Optional[str]]) -> Optional[str]:
        """
        Intelligently selects the display name, falling back to Chinese if the
        English name is missing but contains English characters.
        """
        if not name_obj:
            return None
        
        name_en = name_obj.get("en")
        if name_en:
            return name_en
        
        name_zh = name_obj.get("zh")
        if name_zh and self._is_primarily_english(name_zh):
            return name_zh
            
        return name_zh # Fallback to zh even if it's not English, to avoid returning None if possible

    def user_requests_options(self, user_input: str) -> bool:
        """Check if the user is asking for a list of options."""
        # Expanded list of keywords and phrases
        request_phrases = [
            "option", "choices", "what do you have", "what are the",
            "tell me the", "list the", "can you read me", "read the"
        ]
        return any(phrase in user_input.lower() for phrase in request_phrases)

    def _handle_protein_category_selection(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles the user's choice of a protein category for the Family Combo."""
        protein_categories = list(self.FAMILY_COMBO_PROTEIN_CATEGORIES.keys())
        best_match, score = process.extractOne(user_input, protein_categories)

        if score < 75:
            return {
                "action": "reprompt",
                "message_for_agent": f"I'm sorry, I didn't catch that. Please choose from {', '.join(protein_categories)}."
            }

        options_for_category = self.FAMILY_COMBO_PROTEIN_CATEGORIES[best_match]
        order["state"] = "AWAITING_PROTEIN_OPTION_CHOICE"
        order["current_protein_category"] = best_match
        
        options_text = ", ".join(options_for_category)
        return {
            "status": "PROMPT_FOR_PROTEIN_OPTION",
            "message_for_agent": f"For {best_match}, we have: {options_text}. Which one would you like?"
        }

    def _handle_protein_option_choice(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles the user's choice of a specific protein from a category for the Family Combo."""
        category = order.get("current_protein_category")
        if not category:
            return {"status": "ERROR", "message_for_agent": "Something went wrong, let's try that again."}

        # Get the "clean" options for fuzzy matching against user input
        options_for_category = self.FAMILY_COMBO_PROTEIN_CATEGORIES.get(category, [])
        best_match_clean, score = process.extractOne(user_input, options_for_category)

        if score < 75:
            return {
                "action": "reprompt",
                "message_for_agent": f"I'm sorry, I didn't catch that. For {category}, please choose from {', '.join(options_for_category)}."
            }

        # Now, find the corresponding "original" name from the live menu data
        protein_groups = [g for g in order["dish_details"].get("optionGroups", []) if self._is_protein_group(self._get_display_name(g.get("name", {})))]
        all_protein_options_full = []
        for group in protein_groups:
            all_protein_options_full.extend([self._get_display_name(opt.get("name", {})) for opt in group.get("options", [])])

        # Find the best match in the full list that contains the clean name
        best_match_full = None
        highest_score = 0
        for option_full in all_protein_options_full:
            # We check if the clean name is a substring of the full name.
            # This is more robust than direct equality.
            if best_match_clean.lower() in option_full.lower():
                # Use fuzzy matching to find the best fit among potential candidates
                current_score = process.extractOne(best_match_clean, [option_full])[1]
                if current_score > highest_score:
                    highest_score = current_score
                    best_match_full = option_full
        
        if not best_match_full:
            logger.warning(f"Could not map clean protein '{best_match_clean}' to a full option name for call {call_sid}.")
            # Fallback to the clean name if no match is found, though this is unlikely.
            best_match_full = best_match_clean


        order["selections"]["proteins"].append(best_match_full)
        
        num_selected_proteins = len(order["selections"]["proteins"])
        required_proteins = order.get("required_protein_count", 3) # Default to 3 if not found

        if num_selected_proteins < required_proteins:
            order["state"] = "AWAITING_PROTEIN_CATEGORY"
            protein_categories = list(self.FAMILY_COMBO_PROTEIN_CATEGORIES.keys())
            return {
                "status": "PROMPT_FOR_PROTEIN_CATEGORY",
                "message_for_agent": f"Great, I've added {best_match_clean}. You still have {required_proteins - num_selected_proteins} protein choices left. What would you like for your next one? You can choose from {', '.join(protein_categories)}.",
                "tool_to_use": "handle_combo_item_selection"
            }
        else:
            # All proteins selected, move to the crab selection step.
            order["state"] = "AWAITING_CRAB_CHOICE"
            
            # Find the crab group to create the prompt
            crab_group = next((g for g in order["dish_details"].get("optionGroups", []) if "crab" in self._get_display_name(g.get("name", {})).lower()), None)
            
            if not crab_group:
                # Fallback if crab group isn't found, though it should be.
                logger.error(f"Could not find 'CHOOSE YOUR CRAB' group for Family Combo in call {call_sid}")
                order["state"] = "AWAITING_OPTION_CHOICE"
                return self.get_next_question(call_sid)

            options = [self._clean_option_name_for_tts(self._get_display_name(opt.get("name", {}))) for opt in crab_group.get("options", [])]
            options_str = ", ".join(opt for opt in options if opt)
            
            return {
                "status": "PROMPT_FOR_CRAB_CHOICE",
                "message_for_agent": f"Great, you've selected all your proteins. Now, please choose your crab. Your options are: {options_str}."
            }

    def _handle_crab_choice(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles the user's choice for the 'CHOOSE YOUR CRAB' option."""
        crab_group = next((g for g in order["dish_details"].get("optionGroups", []) if "crab" in self._get_display_name(g.get("name", {})).lower()), None)
        if not crab_group:
            logger.error(f"Logic error: Reached _handle_crab_choice but couldn't find crab group for call {call_sid}")
            order["state"] = "AWAITING_OPTION_CHOICE"
            return self.get_next_question(call_sid)

        options = [self._get_display_name(opt.get("name", {})) for opt in crab_group.get("options", [])]
        valid_options = [opt for opt in options if opt]
        
        best_match, score = process.extractOne(user_input, valid_options)

        if score < 75:
            options_str = ", ".join(valid_options)
            return {
                "action": "reprompt",
                "message_for_agent": f"I'm sorry, I didn't catch that. For the crab, please choose from: {options_str}."
            }
        
        # Store the crab choice separately for clarity, as it's a distinct choice.
        order["selections"]["crab"] = best_match
        
        # Now that crab is selected, move on to the free item selection.
        order["state"] = "AWAITING_FREE_ITEM_CHOICE"
        
        # Dynamically count free items and create the prompt.
        option_groups = order["dish_details"].get("optionGroups", [])
        free_item_groups = [g for g in option_groups if self._is_free_item_group(self._get_display_name(g.get("name", {})))]
        num_free_items = len(free_item_groups)
        order["required_free_items"] = num_free_items
        order["selected_free_items_count"] = 0

        if num_free_items > 0 and free_item_groups:
            options = [self._clean_option_name_for_tts(self._get_display_name(opt.get("name", {}))) for opt in free_item_groups[0].get("options", [])]
            options_str = ", ".join(opt for opt in options if opt)
            message = f"You also get {num_free_items} free items. For your first one, would you like {options_str}?"
            return {
                "status": "PROMPT_FOR_FREE_ITEM",
                "message_for_agent": message
            }
        else:
            # If no free items, skip to the next step.
            return self.get_next_question(call_sid)
    def _handle_free_item_choice(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles the user's choice for a free item."""
        option_groups = order["dish_details"].get("optionGroups", [])
        free_item_group = next((g for g in option_groups if self._is_free_item_group(self._get_display_name(g.get("name", {})))), None)

        if not free_item_group:
            logger.error(f"Logic error: Could not find free item group for call {call_sid}")
            order["state"] = "AWAITING_OPTION_CHOICE"
            return self.get_next_question(call_sid)

        options = [self._get_display_name(opt.get("name", {})) for opt in free_item_group.get("options", [])]
        valid_options = [opt for opt in options if opt]
        
        best_match, score = process.extractOne(user_input, valid_options)

        if score < 75:
            options_str = ", ".join(valid_options)
            return {
                "action": "reprompt",
                "message_for_agent": f"I'm sorry, I didn't catch that. For the free item, please choose from: {options_str}."
            }

        order["selections"]["free_items"].append(best_match)
        order["selected_free_items_count"] += 1

        if order["selected_free_items_count"] < order.get("required_free_items", 0):
            options_str = ", ".join(valid_options)
            return {
                "status": "PROMPT_FOR_FREE_ITEM",
                "message_for_agent": f"I've added the {best_match}. You have {order['required_free_items'] - order['selected_free_items_count']} free items left. What would you like for your next one? Your options are {options_str}."
            }
        else:
            # All free items selected. Find the next non-protein, non-free item step.
            option_groups = order["dish_details"].get("optionGroups", [])
            
            # Find the index of the last free item group to start searching from there.
            last_free_item_group_index = -1
            for i, group in enumerate(option_groups):
                group_name = self._get_display_name(group.get("name", {}))
                if self._is_free_item_group(group_name):
                    last_free_item_group_index = i

            first_other_option_index = -1
            if last_free_item_group_index != -1:
                for i in range(last_free_item_group_index + 1, len(option_groups)):
                    group = option_groups[i]
                    group_name = self._get_display_name(group.get("name", {}))
                    if not self._is_protein_group(group_name) and not self._is_free_item_group(group_name):
                        first_other_option_index = i
                        break
            
            if first_other_option_index != -1:
                order["current_step"] = first_other_option_index
            else:
                # If no other options are found, the combo is complete.
                order["current_step"] = len(option_groups)

            order["state"] = "AWAITING_OPTION_CHOICE"
            return self.get_next_question(call_sid)

    def _handle_protein_selection(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        if self.user_requests_options(user_input):
            order["state"] = "AWAITING_INFO_DELIVERY_CHOICE"
            return {
                "action": "PROMPT_FOR_INFO_DELIVERY",
                "message_for_agent": "I can send the full list of protein options to your phone via SMS, or I can read them out to you. What would you prefer?"
            }

        protein_selection = user_input
        matches = self._get_protein_availability(protein_selection, order["dish_details"])
        
        if not matches:
            return {"action": "INVALID_SELECTION", "message_for_agent": f"My apologies, it looks like {protein_selection} isn't an option for this combo. Would you like me to list the available choices?"}

        # If the user's input was a general category (like "crawfish") and we have multiple variations.
        is_general_category = self._get_protein_availability(protein_selection, order["dish_details"], check_category=True)
        if len(matches) > 1 and is_general_category:
            order["state"] = "AWAITING_PROTEIN_CLARIFICATION"
            order["ambiguous_selections"] = matches
            # The 'name' field now correctly represents the variation, e.g., "fresh crawfish"
            options_text = ", ".join([match["name"] for match in matches])
            return {"action": "PROMPT_FOR_CLARIFICATION", "message_for_agent": f"We have a few options for {protein_selection}: {options_text}. Which one would you like?"}

        # If only one match, or if the user was specific enough to narrow it down to one.
        matched_protein = matches[0]
        order["selections"]["proteins"].append({"name": matched_protein["name"]})
        
        # Always prompt for size after protein selection.
        order["state"] = "AWAITING_SIZE_CHOICE"
        
        can_be_half_pound = matched_protein.get("half_pound", False)
        if can_be_half_pound:
            message = f"Great choice! And how many pounds of {matched_protein['name']} would you like? You can order in half-pound increments."
        else:
            message = f"Great choice! And how many pounds of {matched_protein['name']} would you like? It's available in whole pound increments."

        return {"action": "PROMPT_FOR_SIZE", "message_for_agent": message}

    def _handle_protein_clarification(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles user's choice from a list of ambiguous protein options."""
        ambiguous_options = order.get("ambiguous_selections", [])
        if not ambiguous_options:
            return {"action": "ERROR", "message_for_agent": "Sorry, something went wrong. Let's try that again."}

        option_names = [opt["name"] for opt in ambiguous_options]
        best_match, score = process.extractOne(user_input, option_names)

        if score < 70:
            options_text = ", ".join(option_names)
            return {"action": "CLARIFICATION_FAILED", "message_for_agent": f"I'm sorry, I didn't catch that. Please choose from: {options_text}."}

        # Find the full details of the chosen option
        chosen_protein = next((opt for opt in ambiguous_options if opt["name"] == best_match), None)
        
        if not chosen_protein:
             return {"action": "ERROR", "message_for_agent": "Sorry, an unexpected error occurred. Let's restart the combo."}

        order["selections"]["proteins"].append({"name": chosen_protein["name"]})
        order.pop("ambiguous_selections", None) # Clean up

        # Always prompt for size after protein clarification.
        order["state"] = "AWAITING_SIZE_CHOICE"
        
        can_be_half_pound = chosen_protein.get("half_pound", False)
        if can_be_half_pound:
            message = f"Great choice! And how many pounds of {chosen_protein['name']} would you like? You can order in half-pound increments."
        else:
            message = f"Great choice! And how many pounds of {chosen_protein['name']} would you like? It's available in whole pound increments."
            
        return {"action": "PROMPT_FOR_SIZE", "message_for_agent": message}

    def _parse_numeric_input(self, user_input: str) -> Optional[float]:
        """
        Parses numeric values from spoken language, handling digits, decimals, and a range of number words.
        Returns a float if a valid number is found, otherwise None.
        """
        user_input_lower = user_input.lower().strip()
        
        number_words = {
            'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10
        }

        def parse_token(token: str) -> Optional[float]:
            token = token.strip()
            if token in number_words:
                return float(number_words[token])
            try:
                return float(token)
            except ValueError:
                return None

        # Case 1: "X point Y"
        if 'point' in user_input_lower:
            parts = user_input_lower.split('point')
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                integer_part = parse_token(parts[0])
                # Take only the first word after "point" as the decimal part
                decimal_token = parts[1].strip().split()[0]
                decimal_part = parse_token(decimal_token)
                
                if integer_part is not None and decimal_part is not None:
                    return integer_part + (decimal_part / 10.0)

        # Case 2: "X and a half"
        if 'and a half' in user_input_lower:
            parts = user_input_lower.split('and a half')
            if parts[0].strip():
                integer_part = parse_token(parts[0])
                if integer_part is not None:
                    return integer_part + 0.5
        
        # Case 3: Standalone "half"
        if user_input_lower == "half" or user_input_lower == "half a pound":
            return 0.5

        # Case 4: Simple number words (e.g., "seven", "seven pounds")
        words = user_input_lower.split()
        if words and words[0] in number_words:
            return float(number_words[words[0]])

        # Case 5: Fallback to regex for digits
        match = re.search(r'(\d+\.?\d*)', user_input_lower)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
                
        return None

    def _handle_size_selection(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        quantity = self._parse_numeric_input(user_input)

        if quantity is None:
            return {
                "action": "reprompt",
                "message_for_agent": "I'm sorry, I didn't quite catch that. How many pounds would you like?"
            }

        last_protein = order["selections"]["proteins"][-1]
        protein_details = self._get_protein_availability(last_protein['name'], order['dish_details'])[0]

        # Validation based on protein availability
        can_be_half_pound = protein_details.get("half_pound", False)
        if not can_be_half_pound and quantity % 1 != 0:
            return {
                "action": "reprompt",
                "message_for_agent": f"My apologies, {last_protein['name']} is only available in whole pound increments. How many pounds would you like?"
            }
        
        # If an item can be a half pound, it can be any multiple of 0.5.
        # This logic is now correct.
        if can_be_half_pound and (quantity * 2) % 1 != 0:
             return {
                "action": "reprompt",
                "message_for_agent": f"My apologies, {last_protein['name']} can only be ordered in half-pound or whole-pound increments. Please provide a valid weight."
            }

        # Format the size string for the summary
        if quantity == int(quantity):
            quantity = int(quantity)
        size_str = f"{quantity} lbs" if quantity != 1 else "1 lb"
        
        last_protein["size"] = size_str
        
        order["state"] = "AWAITING_MORE_PROTEINS"
        return {
            "action": "PROMPT_FOR_MORE_PROTEINS",
            "message_for_agent": f"I've added {size_str} of {last_protein['name']}. Would you like to add another protein? or you can say 'no' to proceed to the next step."
        }

    def _handle_option_selection(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles selection for generic options like flavor, spice, extras."""
        option_groups = order["dish_details"].get("optionGroups", [])
        
        current_group_index = order["current_step"] - 1
        if current_group_index < 0 or current_group_index >= len(option_groups):
            return {"status": "ERROR", "message_for_agent": "Something went wrong with the order steps."}
        
        current_group = option_groups[current_group_index]
        group_name = self._get_display_name(current_group.get("name", {}))
        
        # If the group is optional, check for negative phrases to skip the step.
        is_optional = not current_group.get("isRequired", True) or "(optional)" in group_name.lower()
        
        # A more robust list of phrases to indicate skipping.
        negative_phrases = [
            "no", "no thanks", "no thank you", "none", "nothing", 
            "skip", "don't want any", "i'm good", "that's it", "no extras"
        ]
        
        # Use fuzzy matching to see if the user's input is very close to a negative phrase.
        if is_optional:
            # Find the best match from our negative phrases list.
            best_match, score = process.extractOne(user_input.lower(), negative_phrases)
            
            # If the match is strong enough (e.g., 90% similar), skip the step.
            if score >= 90:
                logger.info(f"Detected user wants to skip optional item (input: '{user_input}', matched: '{best_match}'). Advancing to next step.")
                order["current_step"] += 1
                return self.get_next_question(call_sid)

        # Custom logic to handle menu inconsistencies
        cleaned_input = user_input

        # Get the raw option names from the menu
        raw_options = [self._get_display_name(opt.get("name", {})) for opt in current_group.get("options", [])]
        
        # Create a list of cleaned names for matching and TTS
        cleaned_options = [self._clean_option_name_for_tts(opt) for opt in raw_options if opt]
        
        # Create a mapping from cleaned name back to the original raw name for saving the order
        cleaned_to_raw_map = {self._clean_option_name_for_tts(raw_opt): raw_opt for raw_opt in raw_options if raw_opt}

        # Perform fuzzy matching against the CLEANED list for better accuracy
        best_match_cleaned, score = process.extractOne(cleaned_input, cleaned_options)
        
        if score < 75:
            # Use the CLEANED list for a more natural-sounding reprompt
            options_str = ", ".join(cleaned_options)
            return {
                "action": "reprompt",
                "message_for_agent": f"My apologies, {cleaned_input} is not an available option. For {group_name}, your choices are: {options_str}. Which would you like?"
            }
        
        # Find the original, raw option name from the cleaned match to save to the order
        best_match = cleaned_to_raw_map[best_match_cleaned]
        
        # Check if the current order is the "FAMILY COMBO"
        is_family_combo = "family combo" in order["dish_name"].lower()
        
        if not is_family_combo and "customized combo" not in order["dish_name"].lower():
            # Original logic for other fixed-price combos
            is_protein_selection = "choose one" in group_name.lower() and "free" not in group_name.lower()
            if is_protein_selection:
                protein_keywords = ["shrimp", "crawfish", "mussels", "clams", "crab", "lobster", "scallop"]
                if any(keyword in best_match.lower() for keyword in protein_keywords):
                    order["selections"]["fixed_combo_selections"].append(best_match)
                else:
                    order["selections"][group_name] = best_match
            else:
                order["selections"][group_name] = best_match
        elif is_family_combo:
            # Targeted logic for the Family Combo
            is_protein_group = self._is_protein_group(group_name)
            is_free_group = self._is_free_item_group(group_name)
            
            if is_protein_group or is_free_group:
                # Always append choices from protein or free groups to the list
                order["selections"]["fixed_combo_selections"].append(best_match)
            else:
                # Handle other selections like flavor and spice normally
                order["selections"][group_name] = best_match
        else:
            # Logic for customized combos
            order["selections"][group_name] = best_match

        return self.get_next_question(call_sid)

    def _handle_optional_choice_confirmation(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles the yes/no response to an optional prompt."""
        if any(word in user_input.lower() for word in ["no", "skip", "none", "don't"]):
            # If user says no, just advance the step and ask the next question
            order["current_step"] += 1
            return self.get_next_question(call_sid)
        else:
            # If user says yes or anything else, transition to selecting the option
            order["state"] = "AWAITING_OPTION_CHOICE"
            return self.get_next_question(call_sid)

    def _handle_info_delivery_choice(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """Handles user's choice for how to receive the protein options list."""
        if "sms" in user_input.lower() or "text" in user_input.lower():
            order["state"] = "AWAITING_PROTEIN_CHOICE" 
            return {
                "action": "send_sms_menu",
                "message_for_agent": "I'm sending that to you now. What would you like to choose?"
            }
        else:
            dish_name = order.get("dish_name", "your combo")
            if "customized combo" in dish_name.lower():
                popular_options = self._get_popular_protein_options()
                options_text = ", ".join(popular_options)
                message = f"No problem. Some popular choices are: {options_text}. Would you like one of those, or would you like to hear the full list?"
            else:
                # Generic message for fixed-price combos if this state is ever reached.
                message = "Of course. I can read out the choices for each step. Which part of the combo would you like to hear the options for?"

            order["state"] = "AWAITING_PROTEIN_CHOICE" # Remain in this state to handle the user's next response
            return {"status": "PROVIDING_OPTIONS", "message_for_agent": message}

    def _get_popular_protein_options(self) -> List[str]:
        """Returns a hardcoded list of popular protein options."""
        return ["Shrimp", "Mussels", "Crab Legs", "Clams", "Crawfish"]

    def _summarize_protein_options(self, options: List[str]) -> str:
        """Generates a concise summary of protein options."""
        # This function now uses the same categorization logic as _get_protein_availability
        # to avoid code duplication.
        
        # We need a temporary way to access the category function.
        # In a larger refactoring, this might become a static method or a helper function.
        temp_instance = ComboOrderManager()
        get_protein_category = temp_instance._get_protein_availability.__closure__[0].cell_contents

        summary = {"shrimp": 0, "crab": 0, "mussels": 0, "clams": 0, "crawfish": 0, "lobster": 0, "scallop": 0, "other": []}
        
        processed_options = set()

        for opt in options:
            cleaned_opt = self._clean_protein_name(opt)
            if cleaned_opt in processed_options:
                continue
            processed_options.add(cleaned_opt)
            
            category = get_protein_category(cleaned_opt)
            if category and category in summary:
                summary[category] += 1
            else:
                summary["other"].append(cleaned_opt)

        summary_parts = []
        if summary["shrimp"] > 1:
            summary_parts.append("several types of shrimp")
        elif summary["shrimp"] == 1:
            summary_parts.append("shrimp")

        if summary["crab"] > 1:
            summary_parts.append(f"{summary['crab']} kinds of crab")
        elif summary["crab"] == 1:
            summary_parts.append("crab")

        for category in ["mussels", "clams", "crawfish", "lobster", "scallop"]:
            if summary[category] > 0:
                summary_parts.append(category)
        
        if summary["other"]:
            summary_parts.extend(summary["other"])

        return f"We have {', '.join(summary_parts)}."

    def _get_protein_availability(self, protein_name: str, dish_details: Dict[str, Any], check_category: bool = False) -> List[Dict[str, Any]]:
        dish_name_lower = dish_details.get("name", {}).get("en", "").lower()

        if "customized combo" in dish_name_lower:
            return self._get_custom_combo_protein_availability(protein_name, check_category)
        else:
            return self._get_fixed_combo_option_availability(protein_name, dish_details, check_category)

    def _get_fixed_combo_option_availability(self, option_name: str, dish_details: Dict[str, Any], check_category: bool = False) -> List[Dict[str, Any]]:
        """Dynamically builds catalogs and finds matching options for fixed-price combos."""
        option_catalog = {}
        option_variants = {}  # e.g., "shrimp" -> ["1 lb.Shrimp head on", "1 lb. Hesdless Shrimp"]

        for group in dish_details.get("optionGroups", []):
            for option in group.get("options", []):
                name = option.get("name", {}).get("en", "")
                if not name:
                    continue
                
                option_catalog[name.lower()] = option
                
                # Simple categorization for variants
                if "shrimp" in name.lower():
                    if "shrimp" not in option_variants: option_variants["shrimp"] = []
                    option_variants["shrimp"].append(name.lower())
                elif "crawfish" in name.lower():
                    if "crawfish" not in option_variants: option_variants["crawfish"] = []
                    option_variants["crawfish"].append(name.lower())
                elif "mussels" in name.lower():
                    if "mussels" not in option_variants: option_variants["mussels"] = []
                    option_variants["mussels"].append(name.lower())
                elif "crab" in name.lower():
                    if "crab" not in option_variants: option_variants["crab"] = []
                    option_variants["crab"].append(name.lower())
                elif "clams" in name.lower():
                    if "clams" not in option_variants: option_variants["clams"] = []
                    option_variants["clams"].append(name.lower())
                elif "lobster" in name.lower():
                    if "lobster" not in option_variants: option_variants["lobster"] = []
                    option_variants["lobster"].append(name.lower())
                elif "scallop" in name.lower():
                    if "scallop" not in option_variants: option_variants["scallop"] = []
                    option_variants["scallop"].append(name.lower())

        option_name_lower = option_name.lower()

        if check_category:
            return option_name_lower in option_variants

        if option_name_lower in option_variants:
            variants = option_variants[option_name_lower]
            return [option_catalog[variant] for variant in variants if variant in option_catalog]

        all_option_names = list(option_catalog.keys()) + list(option_variants.keys())
        if not all_option_names:
            return []
            
        best_match, score = process.extractOne(option_name_lower, all_option_names)
        
        if score >= 80:
            if best_match in option_variants:
                variants = option_variants[best_match]
                return [option_catalog[variant] for variant in variants if variant in option_catalog]
            if best_match in option_catalog:
                return [option_catalog[best_match]]
            
        return []

    def _get_custom_combo_protein_availability(self, protein_name: str, check_category: bool = False) -> List[Dict[str, Any]]:
        """
        Handles protein availability specifically for the 'Customized Combo'.
        This uses a hardcoded catalog because the "Customized Combo" is a special case
        where the user can choose from a wide range of proteins that are not explicitly
        listed as options in the menu data for that item.
        """
        protein_catalog = {
            "shrimp head on": {"1lb": True, "half_pound": True, "name": "shrimp head on"},
            "headless shrimp": {"1lb": True, "half_pound": True, "name": "headless shrimp"},
            "peeled tail on": {"1lb": True, "half_pound": True, "name": "peeled tail on"},
            "fresh crawfish": {"1lb": True, "half_pound": True, "name": "fresh crawfish"},
            "frozen crawfish": {"1lb": True, "half_pound": True, "name": "frozen crawfish"},
            "crawfish": {"1lb": True, "half_pound": False, "name": "crawfish"},
            "lobster tail": {"1lb": True, "half_pound": False, "name": "lobster tail"},
            "king crab legs": {"1lb": True, "half_pound": False, "name": "king crab legs"},
            "snow crab legs": {"1lb": True, "half_pound": False, "name": "snow crab legs"},
            "dungeness legs": {"1lb": True, "half_pound": False, "name": "dungeness legs"},
            "black mussels": {"1lb": True, "half_pound": True, "name": "black mussels"},
            "green mussels": {"1lb": True, "half_pound": True, "name": "green mussels"},
            "clams": {"1lb": True, "half_pound": True, "name": "clams"},
            "scallop": {"1lb": True, "half_pound": True, "name": "scallop"}
        }

        protein_variants = {
            "crawfish": ["fresh crawfish", "frozen crawfish", "crawfish"],
            "shrimp": ["shrimp head on", "headless shrimp", "peeled tail on"],
            "mussels": ["black mussels", "green mussels"],
            "crab": ["king crab legs", "snow crab legs", "dungeness legs"],
            "clams": ["clams"],
            "lobster": ["lobster tail"],
            "scallop": ["scallop"]
        }

        protein_name_lower = protein_name.lower()

        if check_category:
            return protein_name_lower in protein_variants

        if protein_name_lower in protein_variants:
            variants = protein_variants[protein_name_lower]
            return [protein_catalog[variant] for variant in variants if variant in protein_catalog]

        all_protein_names = list(protein_catalog.keys()) + list(protein_variants.keys())
        best_match, score = process.extractOne(protein_name_lower, all_protein_names)
        
        if score >= 80:
            if best_match in protein_variants:
                variants = protein_variants[best_match]
                return [protein_catalog[variant] for variant in variants if variant in protein_catalog]
            return [protein_catalog[best_match]]
            
        return []

    def _find_option_group_for_input(self, user_input: str, order: Dict[str, Any]):
        """Finds the option group, matched option, and its index for a given user input."""
        option_groups = order["dish_details"].get("optionGroups", [])
        
        best_group_info = {
            "name": None,
            "option": None,
            "index": -1,
            "score": 0
        }

        for i, group in enumerate(option_groups):
            group_name = self._get_display_name(group.get("name", {}))
            # Skip the protein selection group as it's handled differently
            if not group_name or "choose one" in group_name.lower() or "half a pound" in group_name.lower():
                continue

            options = [self._get_display_name(opt.get("name", {})) for opt in group.get("options", [])]
            valid_options = [opt for opt in options if opt]
            if not valid_options:
                continue
                
            best_match, score = process.extractOne(user_input, valid_options)
            
            if score > best_group_info["score"]:
                best_group_info["score"] = score
                best_group_info["name"] = group_name
                best_group_info["option"] = best_match
                best_group_info["index"] = i

        if best_group_info["score"] >= 80:
            return best_group_info["name"], best_group_info["option"], best_group_info["index"]
            
        return None, None, -1

    def _find_protein_to_update(self, user_input: str, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
    Finds a protein in the current order that matches the user's input.
    Returns the protein dictionary if a match is found, otherwise None.
    """
        if "proteins" not in order["selections"]:
            return None

        protein_names_in_order = [p["name"] for p in order["selections"]["proteins"]]
        if not protein_names_in_order:
            return None

    # Use fuzzy matching to find the best match for the user's input among the proteins already in the order.
        best_match, score = process.extractOne(user_input, protein_names_in_order)

        if score >= 80:  # Threshold for a confident match
        # Find the corresponding protein dictionary
            for protein in order["selections"]["proteins"]:
                if protein["name"] == best_match:
                    return protein
        return None

    def _handle_more_proteins(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        user_input_lower = user_input.lower().strip()
        # Normalize the input by removing punctuation
        normalized_input = re.sub(r'[^\w\s]', '', user_input_lower)
        
        # More robust check for negative/finished responses.
        negative_phrases = [
            "no", "nope", "done", "no thats it", "that is all", "no thats all", 
            "im good", "im good", "no more", "no thanks", "no thank you", "thats it"
        ]

        is_negative = any(phrase in normalized_input for phrase in negative_phrases)

        if is_negative:
            # User is done adding proteins. Move directly to the next step.
            order["state"] = "AWAITING_OPTION_CHOICE"
            return self.get_next_question(call_sid)
        
        # If the user says a simple "yes", prompt them for the next item.
        affirmative_responses = ["yes", "sure", "yeah", "another"]
        if any(phrase in user_input_lower for phrase in affirmative_responses):
            order["state"] = "AWAITING_PROTEIN_CHOICE"
            return {
                "action": "PROMPT_FOR_PROTEIN",
                "message_for_agent": "Great! What other protein would you like to add?"
            }

        # Otherwise, assume the user is naming the next protein directly.
        order["state"] = "AWAITING_PROTEIN_CHOICE"
        return self._handle_protein_selection(user_input, order, call_sid)

    def _finalize_combo_and_prepare_for_cart(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """
        Handles the final confirmation of the combo. Assumes confirmation unless the user
        explicitly requests a change.
        """
        user_input_lower = user_input.lower() if user_input else ""
        
        # Keywords that indicate the user wants to change something.
        change_keywords = ["no", "change", "wrong", "instead", "actually"]
        
        # Check if the user's input explicitly signals a desire to change the order.
        if any(keyword in user_input_lower for keyword in change_keywords):
            order["state"] = "AWAITING_UPDATE"
            return self.update_combo_selection(user_input, order, call_sid)

        # If no change keywords are detected, treat it as a confirmation.
        # This handles "yes", "correct", "yep", and also cases where input is None or empty.
        self.add_completed_combo(call_sid, order)
        
        selections = order.get("selections", {})
        product_options = {key: value for key, value in selections.items()}
        if 'proteins' in selections:
            product_options['proteins'] = selections['proteins']

        completed_item = {
            "name": order["dish_name"],
            "quantity": 1,
            "options": product_options,
        }

        order["state"] = "AWAITING_MORE_ITEMS_PROMPT"
        self.clear_order(call_sid)
        
        return {
            "status": "ITEM_READY_FOR_CART",
            "action": "PROMPT_FOR_MORE_ITEMS",
            "message_for_agent": f"Great, I've added the {order['dish_name']} to your order. You can add more items, or say 'finish order' to complete your order.",
            "item_to_add": completed_item
        }

    def get_next_question(self, call_sid: str) -> Dict[str, Any]:
        if call_sid not in self.active_orders:
            logger.error(f"get_next_question: No active order found for call_sid {call_sid}")
            return {"message_for_agent": "Sorry, I can't find your order."}

        order = self.active_orders[call_sid]
        dish_name_lower = order["dish_name"].lower()
        is_customized_combo = "customized combo" in dish_name_lower
        is_fixed_combo = not is_customized_combo

        logger.info(f"get_next_question for {dish_name_lower}: is_fixed_combo={is_fixed_combo}, current_step={order['current_step']}")

        option_groups = order["dish_details"].get("optionGroups", [])

        while order["current_step"] < len(option_groups):
            current_group = option_groups[order["current_step"]]
            group_display_name = self._get_display_name(current_group.get("name", {}))
            
            logger.info(f"Processing group: '{group_display_name}' at step {order['current_step']}")

            # For Family Combo, skip all protein and free item groups as they are handled by the new flow.
            if "family combo" in dish_name_lower and (self._is_protein_group(group_display_name) or self._is_free_item_group(group_display_name)):
                order["current_step"] += 1
                continue

            # Skip groups irrelevant to customized combos
            if is_customized_combo and self._is_protein_group(group_display_name):
                order["current_step"] += 1
                continue

            order["state"] = "AWAITING_OPTION_CHOICE"
            message = ""

            # --- Start of New Contextual Prompting Logic ---
            if is_fixed_combo:
                if self._is_protein_group(group_display_name):
                    order["protein_choice_count"] += 1
                    count = order["protein_choice_count"]
                    
                    ordinal_map = {1: "first", 2: "second", 3: "third"}
                    ordinal = ordinal_map.get(count, f"{count}th")
                    
                    if count == 1:
                        message = f"For your {order['dish_name']}, what would you like for your {ordinal} protein choice?"
                    else:
                        message = f"And for your {ordinal} protein choice?"
                    logger.info(f"Generated protein prompt for count {count}: '{message}'")

                elif self._is_free_item_group(group_display_name):
                    # Only ask for a free item if we haven't already collected enough
                    if order.get("free_item_count", 0) < order.get("num_free_items", 0):
                        order["free_item_count"] += 1
                        if order["free_item_count"] == 1:
                            message = "You also get a free item with your combo. Would you like corn, potatoes, or sausage?"
                        else:
                            message = "You get another free item. What would you like?"
                        logger.info("Generated free item prompt.")
                    else:
                        # If we have enough free items, skip this group and move to the next
                        order["current_step"] += 1
                        continue

            # Fallback for non-protein/non-free groups or customized combos
            if not message:
                cleaned_group_name = self._clean_prompt_text(group_display_name)
                message = f"For your {order['dish_name']}, what would you like for the {cleaned_group_name}?"
                logger.info(f"Generated default prompt for group '{cleaned_group_name}'")
            # --- End of New Logic ---

            options = [self._clean_option_name_for_tts(self._get_display_name(opt.get("name", {}))) for opt in current_group.get("options", [])]
            cleaned_options = [self._clean_prompt_text(opt) for opt in options if opt]
            options_str = ", ".join(cleaned_options)
            
            message += f" Your options are: {options_str}."

            # Check if the group is optional to add a helpful hint
            is_optional = not current_group.get("isRequired", True) or "(optional)" in group_display_name.lower()
            if is_optional:
                message += " You can also say 'no extras' to skip."

            order["current_step"] += 1
            logger.info(f"Returning prompt: '{message}'")
            return {
                "action": "get_selection",
                "message_for_agent": message
            }

        order["state"] = "AWAITING_FINAL_CONFIRMATION"
        summary = self.get_order_summary(call_sid)
        logger.info(f"All groups processed. Moving to final confirmation.")
        return {
            "action": "confirm_order",
            "message_for_agent": f"I have your {order['dish_name']} with {summary}. Is that correct?",
            "options": order["selections"],
            "tool_to_use": "handle_combo_item_selection"
        }

    def get_order_summary(self, call_sid: str) -> str:
        if call_sid not in self.active_orders:
            return "No order found."
        order = self.active_orders[call_sid]
        
        summary_parts = []
        selections = order.get("selections", {})

        # Handle customized combo proteins first
        if "proteins" in selections and selections["proteins"] and "family combo" not in order["dish_name"].lower():
            protein_summary = ", ".join([f"{p.get('size', '1 lb')} of {p['name']}" for p in selections["proteins"]])
            summary_parts.append(f"\n- Proteins: {protein_summary}")

        # --- START: FAMILY COMBO SUMMARY LOGIC ---
        if "family combo" in order["dish_name"].lower():
            if "proteins" in selections and selections["proteins"]:
                summary_parts.append(f"\n- Proteins: {', '.join(selections['proteins'])}")
            if "crab" in selections and selections["crab"]:
                summary_parts.append(f"\n- Crab: {selections['crab']}")
            if "free_items" in selections and selections["free_items"]:
                summary_parts.append(f"\n- Free Items: {', '.join(selections['free_items'])}")
        # --- END: FAMILY COMBO SUMMARY LOGIC ---
        else:
            # Handle fixed combo selections for other combos
            if "fixed_combo_selections" in selections and selections["fixed_combo_selections"]:
                fixed_summary = ", ".join(selections["fixed_combo_selections"])
                summary_parts.append(f"\n- Selections: {fixed_summary}")

        # Handle all other selections (flavor, spice, etc.) for all combo types
        for key, value in selections.items():
            if key not in ["proteins", "crab", "free_items", "fixed_combo_selections"]:
                formatted_key = key.replace("_", " ").title()
                summary_parts.append(f"\n- {formatted_key}: {value}")
                
        return "".join(summary_parts)

    def update_combo_selection(self, user_input: str, order: Dict[str, Any], call_sid: str) -> Dict[str, Any]:
        """
    Handles user requests to update a combo selection during final confirmation.
    """
    # Attempt to find a matching option group (e.g., flavor, spice level)
        group_name, matched_option, _ = self._find_option_group_for_input(user_input, order)
    
        if group_name and matched_option:
        # Update the selection for the found group
            order["selections"][group_name] = matched_option
            logger.info(f"Updated combo option '{group_name}' to '{matched_option}' for call_sid: {call_sid}")
        
        # Re-confirm the order
            order["state"] = "AWAITING_FINAL_CONFIRMATION"
            summary = self.get_order_summary(call_sid)
            return {
            "action": "confirm_order",
            "message_for_agent": f"Okay, I've updated the {group_name} to {matched_option}. Now I have your combo with {summary}. Is that correct?"
        }

    # If no option group was found, check if the user wants to change a protein
        protein_to_update = self._find_protein_to_update(user_input, order)
        if protein_to_update:
        # For now, we'll just acknowledge and re-prompt.
        # A more advanced implementation would guide the user through changing the protein.
            order["state"] = "AWAITING_FINAL_CONFIRMATION"
            summary = self.get_order_summary(call_sid)
            return {
            "action": "reprompt",
            "message_for_agent": f"It looks like you want to change something about the {protein_to_update['name']}. Could you please specify what you'd like to change?"
        }

    # If no specific change can be identified, reprompt for clarification
        summary = self.get_order_summary(call_sid)
        return {
        "action": "reprompt",
        "message_for_agent": f"I'm sorry, I didn't catch that. Your order is currently {summary}. What would you like to change?"
    }

    def get_completed_order(self, call_sid: str) -> Optional[Dict[str, Any]]:
        return self.active_orders.get(call_sid)

    def clear_order(self, call_sid: str):
        if call_sid in self.active_orders:
            del self.active_orders[call_sid]

    def is_awaiting_final_confirmation(self, call_sid: str) -> bool:
        """Checks if a combo order is awaiting final confirmation."""
        return self.is_active(call_sid) and self.active_orders[call_sid].get("state") == "AWAITING_FINAL_CONFIRMATION"

    def finalize_and_get_combo(self, call_sid: str) -> Optional[Dict[str, Any]]:
        """
        Programmatically finalizes a combo that is awaiting confirmation and returns it.
        This is used for the intelligent checkout consolidation.
        """
        if not self.is_awaiting_final_confirmation(call_sid):
            return None

        order = self.active_orders[call_sid]
        
        # This logic is borrowed from _finalize_combo_and_prepare_for_cart
        self.add_completed_combo(call_sid, order)
        
        selections = order.get("selections", {})
        product_options = {key: value for key, value in selections.items()}
        if 'proteins' in selections:
            product_options['proteins'] = selections['proteins']

        completed_item = {
            "name": order["dish_name"],
            "quantity": 1,
            "options": product_options,
        }

        # Clean up the active order
        self.clear_order(call_sid)
        
        return completed_item

combo_order_manager = ComboOrderManager()
