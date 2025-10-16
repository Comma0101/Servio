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

def _normalize_search_query(query: str) -> str:
    """
    Normalizes a search query for more robust matching.
    - Converts number words to digits.
    - Standardizes units (e.g., "piece" to "pc").
    - Removes special characters.
    - Normalizes combo-related phrasing.
    """
    query = query.lower()

    # Normalize combo-related phrasing first
    combo_match = re.search(r'(.+?)\s*(?:number|#)?\s*(\w+)$', query, re.IGNORECASE)
    if combo_match:
        phrase = combo_match.group(1).strip()
        number_str = combo_match.group(2)
        number_map = {
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"
        }
        if number_str.lower() in number_map or number_str.isdigit():
            digit = number_map.get(number_str.lower(), number_str)
            query = f"{phrase} #{digit}"

    # General number word to digit conversion
    number_words = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "twelve": "12"
    }
    for word, digit in number_words.items():
        query = re.sub(r'\b' + word + r'\b', digit, query)

    # Standardize units
    unit_map = {
        r'pieces?': 'pc',
        r'pcs': 'pc'
    }
    for pattern, replacement in unit_map.items():
        query = re.sub(pattern, replacement, query)

    # Remove special characters and extra whitespace
    query = re.sub(r'[^\w\s#]', '', query)
    query = ' '.join(query.split())
    
    logger.info(f"Normalized search query to '{query}'.")
    return query

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
    Finds a dish by its name using a multi-stage search process for accuracy.
    """
    original_query = name_to_find
    
    # Use the new comprehensive normalization for English queries
    if language == 'en':
        normalized_search_term = _normalize_search_query(name_to_find)
    else:
        normalized_search_term = name_to_find.lower()

    menu_response = await get_extracted_dishes(portal_id)
    if not (menu_response and menu_response.get("success")):
        logger.error(f"Cannot find dish '{name_to_find}'; failed to retrieve menu for portal {portal_id}.")
        return []

    all_dishes = menu_response.get("data", [])
    name_key = f"dish_name_{language}"

    # --- Step 1: Exact Match on Normalized Names ---
    exact_matches = []
    for dish in all_dishes:
        dish_name = dish.get(name_key, "")
        # Normalize the menu item name using the same logic as the query
        normalized_dish_name = _normalize_search_query(dish_name) if language == 'en' else dish_name.lower()
        
        if normalized_dish_name == normalized_search_term:
            exact_matches.append(dish)
    
    if exact_matches:
        logger.info(f"Found {len(exact_matches)} exact normalized {language} match(es) for '{name_to_find}'.")
        return exact_matches

    # --- Step 2: "All Words" Match on Normalized Names ---
    query_words = set(normalized_search_term.split())
    if len(query_words) > 1:
        all_words_matches = []
        for dish in all_dishes:
            dish_name = dish.get(name_key, "")
            normalized_dish_name = _normalize_search_query(dish_name) if language == 'en' else dish_name.lower()
            if all(word in normalized_dish_name for word in query_words):
                all_words_matches.append(dish)
        
        if all_words_matches:
            logger.info(f"Found {len(all_words_matches)} 'all words' normalized match(es) for '{name_to_find}'.")
            return all_words_matches

    # --- Step 3: Phrase "Contains" Match on Normalized Names ---
    contains_matches = []
    for dish in all_dishes:
        dish_name = dish.get(name_key, "")
        normalized_dish_name = _normalize_search_query(dish_name) if language == 'en' else dish_name.lower()
        if normalized_search_term in normalized_dish_name:
            contains_matches.append(dish)
            
    if contains_matches:
        logger.info(f"Found {len(contains_matches)} 'contains' phrase match(es) on normalized names for '{name_to_find}'.")
        return contains_matches[:4]

    # --- Step 4: General Fuzzy Match (as a fallback) ---
    if language == 'en':
        all_dish_names = [dish.get(name_key, "") for dish in all_dishes]
        # Use extract() to get ALL matches above a reasonable threshold
        matches = process.extract(original_query, all_dish_names, limit=5) # Limit to top 5 fuzzy
        good_matches = [(name, score) for name, score in matches if score > 80]
        
        if good_matches:
            # Get all dishes that match well
            matched_dishes = [dish for dish in all_dishes 
                            if dish.get(name_key, "") in [m[0] for m in good_matches]]
            if matched_dishes:
                scores_str = ", ".join([f"'{m[0]}' (score: {m[1]})" for m in good_matches])
                logger.info(f"Found {len(matched_dishes)} general fuzzy match(es) for '{original_query}': {scores_str}")
                return matched_dishes

    logger.info(f"No high-confidence {language} match found for '{name_to_find}'.")
    return []

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
