import os
import re
import json
import logging
import time
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, Field
from enum import Enum
from thefuzz import process

from app.constants import (
    THIRTY_NINE_MILES_BASE_URL,
    THIRTY_NINE_MILES_CLIENT_ID,
    THIRTY_NINE_MILES_CLIENT_SEC,
    THIRTY_NINE_MILES_TOKEN_PREFIX
)

# --- Module-level Configuration ---
logger = logging.getLogger(__name__)
CACHE_FILE_TEMPLATE = "app/utils/extracted_dishes_cache_v2_{portal_id}.json" # Cache bust
CACHE_DURATION_HOURS = 24

# --- Pydantic Models for Order Creation ---

def _normalize_combo_query(query: str) -> str:
    """
    Normalizes different ways of saying a numbered item into a standard format.
    e.g., "combo number two", "lunch special one" -> "COMBO #2", "LUNCH SPECIAL #1"
    """
    # This regex looks for any phrase, an optional "number" or "#", and a number word/digit at the end.
    match = re.search(r'(.+?)\s*(?:number|#)?\s*(\w+)$', query, re.IGNORECASE)
    if not match:
        return query

    phrase = match.group(1).strip()
    number_str = match.group(2)
    
    number_map = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"
    }
    
    # Verify the last word is a number before normalizing.
    if number_str.lower() not in number_map and not number_str.isdigit():
        return query

    digit = number_map.get(number_str.lower(), number_str)
    
    normalized_query = f"{phrase.upper()} #{digit}"
    logger.info(f"Normalized combo query '{query}' to '{normalized_query}'.")
    return normalized_query

class TextStore(BaseModel):
    en: Optional[str] = None
    zh: Optional[str] = None

class MenuProductOption(BaseModel):
    name: Optional[TextStore] = None
    adjust_price: Optional[float] = Field(None, alias="adjustPrice")
    linked_product_id: Optional[str] = Field(None, alias="linkedProductId")
    is_allow_adjust_count: Optional[bool] = Field(None, alias="isAllowAdjustCount")
    is_out_of_stock: Optional[bool] = Field(None, alias="isOutOfStock")
    choosed_count: Optional[int] = Field(None, alias="choosedCount")
    weight: Optional[float] = None
    class Config:
        populate_by_name = True

class MenuProductOptionGroup(BaseModel):
    name: Optional[TextStore] = None
    is_required: Optional[bool] = Field(None, alias="isRequired")
    is_allow_multiple: Optional[bool] = Field(None, alias="isAllowMultiple")
    description: Optional[str] = None
    options: Optional[List[MenuProductOption]] = None
    min_count: Optional[int] = Field(None, alias="minCount")
    max_count: Optional[int] = Field(None, alias="maxCount")
    class Config:
        populate_by_name = True

class ApiOrderItem(BaseModel):
    name: Optional[TextStore] = None
    category_id: Optional[str] = Field(None, alias="categoryId")
    product_id: Optional[str] = Field(None, alias="productId")
    quantity: Optional[int] = None
    weight: Optional[float] = None
    final_price: Optional[float] = Field(None, alias="finalPrice")
    special_requests: Optional[List[TextStore]] = Field(None, alias="specialRequests")
    product_options: Optional[List[MenuProductOptionGroup]] = Field(None, alias="productOptions")
    class Config:
        populate_by_name = True

class LangCode(str, Enum):
    EN = "EN"
    ZH = "ZH"

class ApiOrderDto(BaseModel):
    order_id: Optional[str] = Field(None, alias="orderId")
    portal_id: str = Field(..., alias="portalId")
    table_tag: Optional[str] = Field(None, alias="tableTag")
    customer_phone: str = Field(..., alias="customerPhone")
    user_name: Optional[str] = Field(None, alias="userName")
    schedule_time: Optional[str] = Field(None, alias="scheduleTime")
    item_subtotal: Optional[float] = Field(None, alias="itemSubtotal")
    discount: Optional[float] = None
    tax: Optional[float] = None
    tips: Optional[float] = None
    bag_fee: Optional[float] = Field(None, alias="bagFee")
    fee: Optional[float] = None
    platform_fee: Optional[float] = Field(None, alias="platformFee")
    final_payment: Optional[float] = Field(None, alias="finalPayment")
    lang_code: LangCode = Field(..., alias="langCode")
    order_items: List[ApiOrderItem] = Field(..., alias="orderItems")
    class Config:
        populate_by_name = True
        use_enum_values = True

# --- Private Helper Functions for API Interaction ---

async def _fetch_token() -> str:
    """Fetches a new authentication token from the 39-Miles API."""
    url = f"{THIRTY_NINE_MILES_BASE_URL}/Auth"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(
                url,
                json={"client_id": THIRTY_NINE_MILES_CLIENT_ID, "client_secret": THIRTY_NINE_MILES_CLIENT_SEC},
                headers={"Content-Type": "application/json-patch+json"}
            )
            r.raise_for_status()
            token = r.json().get("token")
            if not token:
                raise ValueError("Authentication response missing token.")
            return token
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
            logger.error(f"Failed to fetch 39-Miles token: {e}")
            raise

async def _call_api_with_existing_token(path: str, token: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Makes a GET request to the 39-Miles API using a provided token and client."""
    url = f"{THIRTY_NINE_MILES_BASE_URL}{path}"
    try:
        r = await client.get(url, headers={"Authorization": f"{THIRTY_NINE_MILES_TOKEN_PREFIX} {token}"})
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"API call to {path} failed: {e}")
        return {"success": False, "message": str(e), "data": None}

async def _get_raw_detailed_menu(portal_id: str) -> List[Dict[str, Any]]:
    """
    Fetches the full, raw menu data from the API, including all categories and their products.
    This function does not use caching; it's intended to be called by a caching function.
    """
    logger.info(f"Fetching fresh detailed menu data from API for portal {portal_id}.")
    raw_menu_data = []
    try:
        token = await _fetch_token()
        async with httpx.AsyncClient(timeout=30) as client:
            categories_response = await _call_api_with_existing_token(f"/menu/{portal_id}/categories", token, client)
            if not (categories_response and categories_response.get("success")):
                logger.error(f"Failed to fetch categories for portal {portal_id}: {categories_response.get('message')}")
                return []

            category_list = categories_response.get("data", {}).get("items", [])
            for category in category_list:
                category_id = category.get("id")
                if not category_id:
                    continue
                
                products_response = await _call_api_with_existing_token(f"/menu/category/{category_id}/products", token, client)
                if products_response and products_response.get("success"):
                    product_data = products_response.get("data", [])
                    category["items"] = product_data.get("items", []) if isinstance(product_data, dict) else product_data
                else:
                    category["items"] = []
                raw_menu_data.append(category)
        return raw_menu_data
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching the raw detailed menu for portal {portal_id}: {e}", exc_info=True)
        return []

async def _find_dish(portal_id: str, name_to_find: str, language: Literal['en', 'zh']) -> List[Dict[str, Any]]:
    """
    Generic internal function to find a dish by its name in the specified language.
    Performs an exact match first, then a 'contains' match.
    """
    # --- START: NEW NORMALIZATION AND SEARCH LOGIC ---
    original_query = name_to_find
    if language == 'en':
        name_to_find = _normalize_combo_query(name_to_find)

    menu_response = await get_extracted_dishes(portal_id)
    if not (menu_response and menu_response.get("success")):
        logger.error(f"Cannot find dish '{name_to_find}'; failed to retrieve menu for portal {portal_id}.")
        return []

    all_dishes = menu_response.get("data", [])
    search_term_lower = name_to_find.lower()
    name_key = f"dish_name_{language}"

    # 1. Exact match (case-insensitive for English)
    for dish in all_dishes:
        dish_name = dish.get(name_key, "")
        # Normalize whitespace to handle data entry errors like double spaces
        normalized_dish_name = " ".join(dish_name.lower().split())
        normalized_search_term = " ".join(search_term_lower.split())

        if (language == 'en' and normalized_dish_name == normalized_search_term) or \
           (language == 'zh' and dish_name == name_to_find):
            logger.info(f"Found exact {language} match for '{name_to_find}'.")
            return [dish]

    # 2. If no exact match, fall back to a general fuzzy match for English queries.
    if language == 'en':
        all_dish_names = [dish.get(name_key, "") for dish in all_dishes]
        # Use extract() to get ALL matches above threshold, not just the best one
        matches = process.extract(original_query, all_dish_names, limit=None)
        good_matches = [(name, score) for name, score in matches if score > 80]
        
        if good_matches:
            # Get all dishes that match well
            matched_dishes = [dish for dish in all_dishes 
                            if dish.get(name_key, "") in [m[0] for m in good_matches]]
            if matched_dishes:
                # Log all matches found
                match_names = [dish.get(name_key, "") for dish in matched_dishes]
                scores_str = ", ".join([f"'{m[0]}' (score: {m[1]})" for m in good_matches])
                logger.info(f"Found {len(matched_dishes)} general fuzzy match(es) for '{original_query}': {scores_str}")
                return matched_dishes  # Return all matches for ambiguity handling

    # 3. If still no match, fall back to fuzzy matching specifically for combos
    if "combo" in search_term_lower and language == 'en':
        combo_dishes = [dish for dish in all_dishes if dish.get("is_combo")]
        if combo_dishes:
            combo_names = [dish.get(name_key, "") for dish in combo_dishes]
            # Use the original, un-normalized query for fuzzy matching
            best_match_name, score = process.extractOne(original_query, combo_names)
            if score > 80: # Confidence threshold
                best_match_dish = next((dish for dish in combo_dishes if dish.get(name_key, "") == best_match_name), None)
                if best_match_dish:
                    logger.info(f"Found combo match for '{original_query}' with score {score}. Best match: '{best_match_name}'")
                    return [best_match_dish]

    # 4. "Contains" match (case-insensitive for English)
    contains_matches = [
        dish for dish in all_dishes
        if (language == 'en' and search_term_lower in dish.get(name_key, "").lower()) or \
           (language == 'zh' and name_to_find in dish.get(name_key, ""))
    ]

    # 4. Search within combo options if no direct matches are found
    # if not contains_matches and language == 'en':
    #     for dish in all_dishes:
    #         if dish.get("is_combo"):
    #             for group in dish.get("dish_details", {}).get("optionGroups", []):
    #                 for option in group.get("options", []):
    #                     name_info = option.get("name") or {}
    #                     option_name = (name_info.get("en") or name_info.get("zh") or "").lower()
    #                     if search_term_lower in option_name:
    #                         logger.info(f"Found '{name_to_find}' as an option in combo '{dish.get(name_key)}'.")
    #                         # Return the parent combo dish
    #                         return [dish]

    if not contains_matches:
        logger.info(f"No {language} match found for '{name_to_find}'.")
        return []

    logger.info(f"Found {len(contains_matches)} 'contains' {language} match(es) for '{name_to_find}'.")
    return contains_matches[:4]

def _patch_menu_with_local_overrides(
    live_menu: List[Dict[str, Any]], portal_id: str
) -> List[Dict[str, Any]]:
    """
    DEPRECATED: This function previously overrode live menu items with a local JSON file.
    This is no longer needed as data correction is handled in _preprocess_menu_data.
    The function is kept to avoid breaking the call chain but now does nothing.
    """
    logger.info("Skipping local menu override; data correction is now automated in preprocessing.")
    return live_menu

def _is_primarily_english(text: str) -> bool:
    """
    Checks if the text is primarily English characters.
    """
    if not text:
        return True
    # This regex allows basic Latin alphabet, digits, and common punctuation.
    # It will not match most characters from other languages like Chinese.
    return bool(re.fullmatch(r"^[a-zA-Z0-9\s!\"#$%&'()*+,-./:;<=>?@[\\\]^_`{|}~]*$", text))

def _preprocess_menu_data(raw_menu: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Preprocesses menu data to add helpful flags (like is_combo) and correct
    known data issues, such as English names misplaced in Chinese fields.
    """
    for category in raw_menu:
        for item in category.get("items", []):
            name_obj = item.get("name")
            if not isinstance(name_obj, dict):
                # If name is not a dict, it's malformed; skip it.
                continue

            name_en = name_obj.get("en")
            name_zh = name_obj.get("zh")

            # Data correction: If 'en' is missing but 'zh' contains English text, copy it over.
            if not name_en and name_zh and _is_primarily_english(name_zh):
                logger.warning(f"Correcting missing English name for item '{item.get('id')}'. Found English text in 'zh' field: '{name_zh}'")
                name_obj["en"] = name_zh
                # Optional: Clear the 'zh' field if it's now redundant, or leave it.
                # name_obj["zh"] = None 

            # Safely get the English name for combo check after potential correction
            item_name_en_lower = (name_obj.get("en") or "").lower()
            
            if "combo" in item_name_en_lower:
                item["is_combo"] = True
                # If it's a customized combo, enforce isRequired and a specific order.
                if "customized combo" in item_name_en_lower:
                    option_groups = item.get("optionGroups", [])
                    if option_groups:
                        # Filter out null/None entries from the list to prevent crashes
                        valid_option_groups = [g for g in option_groups if g is not None]

                        logger.info("Applying specific ordering and isRequired patch for 'Customized Combo'.")
                        
                        # Define the desired order of options
                        desired_order = [
                            "choose one",
                            "half a pound",
                            "pick your flavor",
                            "pick your spicy level",
                            "add on"
                        ]
                        
                        def get_order_index(group):
                            # This is now safe because we filtered out None values
                            group_name = ((group.get("name") or {}).get("en") or "").lower()
                            for i, term in enumerate(desired_order):
                                if term in group_name:
                                    return i
                            return len(desired_order) # Place unknown groups at the end

                        # Sort the valid groups according to the desired order
                        valid_option_groups.sort(key=get_order_index)
                        
                        # Mark all of them as required
                        for group in valid_option_groups:
                            group["isRequired"] = True
                        
                        # Assign the cleaned and sorted list back to the item
                        item["optionGroups"] = valid_option_groups
    return raw_menu

# --- Public Functions ---

async def get_extracted_dishes(portal_id: str) -> Dict[str, Any]:
    """
    Fetches a flattened list of all dishes for a portal, using a cache.
    It fetches the live menu, then applies local overrides for known API issues
    before caching and returning the result.
    """
    cache_file = CACHE_FILE_TEMPLATE.format(portal_id=portal_id)

    # Check cache first
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            cache_timestamp = datetime.fromisoformat(cached_data.get("timestamp", ""))
            if datetime.now() - cache_timestamp < timedelta(hours=CACHE_DURATION_HOURS):
                logger.info(f"Serving extracted dishes for portal {portal_id} from cache.")
                return {"success": True, "message": "Data from cache", "data": cached_data.get("dishes", [])}
        except (json.JSONDecodeError, IOError, TypeError, ValueError) as e:
            logger.warning(f"Cache read error for portal {portal_id}: {e}. Fetching fresh data.")

    # Fetch fresh data if cache is invalid or stale
    raw_menu = await _get_raw_detailed_menu(portal_id)
    if not raw_menu:
        return {"success": False, "message": "Failed to fetch raw menu data from API.", "data": []}

    # Patch the raw menu with local overrides for known issues
    patched_menu = _patch_menu_with_local_overrides(raw_menu, portal_id)
    
    # Preprocess data to add helpful flags (e.g., is_combo)
    processed_menu = _preprocess_menu_data(patched_menu)

    extracted_dishes = []
    for category in processed_menu:
        for dish in category.get("items", []):
            extracted_dishes.append({
                "dish_id": dish.get("id"),
                "dish_name_en": dish.get("name", {}).get("en", ""),
                "dish_name_zh": dish.get("name", {}).get("zh", ""),
                "price": dish.get("price"),
                "options": dish.get("optionGroups"),
                "category_id": category.get("id"),
                "category_name_en": category.get("name", {}).get("en", ""),
                "category_name_zh": category.get("name", {}).get("zh", ""),
                "is_combo": dish.get("is_combo", False),
                "dish_details": dish
            })

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "dishes": extracted_dishes}, f, ensure_ascii=False)
        logger.info(f"Saved fresh extracted dishes for portal {portal_id} to cache.")
    except IOError as e:
        logger.error(f"Failed to write to cache file {cache_file}: {e}")

    return {"success": True, "message": "Data from API with local patches", "data": extracted_dishes}

async def find_dish_by_chinese_name(portal_id: str, chinese_name: str) -> List[Dict[str, Any]]:
    """Public-facing function to find a dish by its Chinese name."""
    return await _find_dish(portal_id, chinese_name, 'zh')

async def find_dish_by_english_name(portal_id: str, english_name: str) -> List[Dict[str, Any]]:
    """Public-facing function to find a dish by its English name."""
    return await _find_dish(portal_id, english_name, 'en')

async def add_order(order_data: ApiOrderDto) -> Dict[str, Any]:
    """Creates a new order in the 39-Miles system."""
    try:
        payload = order_data.model_dump(by_alias=True, exclude_none=True)
        logger.info(f"--- 39-Miles API Request Payload ---")
        logger.info(json.dumps(payload, indent=2))
        logger.info(f"------------------------------------")
        token = await _fetch_token()
        url = f"{THIRTY_NINE_MILES_BASE_URL}/Order/Add"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"{THIRTY_NINE_MILES_TOKEN_PREFIX} {token}",
                    "Content-Type": "application/json-patch+json"
                }
            )
            r.raise_for_status()
            response_json = r.json()
            logger.info(f"39Miles AddOrder raw response: {response_json}")
            return response_json
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
        logger.error(f"Failed to add order to 39-Miles: {e}", exc_info=True)
        return {"success": False, "message": str(e), "data": None}
    except Exception as e:
        logger.error(f"An unexpected error occurred during order placement: {e}", exc_info=True)
        return {"success": False, "message": "An unexpected error occurred.", "data": None}
