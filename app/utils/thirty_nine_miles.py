import os
import json
import logging
import time
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, Field
from enum import Enum

from app.constants import (
    THIRTY_NINE_MILES_BASE_URL,
    THIRTY_NINE_MILES_CLIENT_ID,
    THIRTY_NINE_MILES_CLIENT_SEC,
    THIRTY_NINE_MILES_TOKEN_PREFIX
)

# --- Module-level Configuration ---
logger = logging.getLogger(__name__)
CACHE_FILE_TEMPLATE = "app/utils/extracted_dishes_cache_{portal_id}.json"
CACHE_DURATION_HOURS = 24

# --- Pydantic Models for Order Creation ---

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
    class Config:
        populate_by_name = True

class MenuProductOptionGroup(BaseModel):
    name: Optional[TextStore] = None
    is_required: Optional[bool] = Field(None, alias="isRequired")
    is_allow_multiple: Optional[bool] = Field(None, alias="isAllowMultiple")
    description: Optional[str] = None
    options: Optional[List[MenuProductOption]] = None
    min_count: Optional[int] = Field(None, alias="MinCount")
    max_count: Optional[int] = Field(None, alias="MaxCount")
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
        if (language == 'en' and dish_name.lower() == search_term_lower) or (language == 'zh' and dish_name == name_to_find):
            logger.info(f"Found exact {language} match for '{name_to_find}'.")
            return [dish]

    # 2. "Contains" match (case-insensitive for English)
    contains_matches = [
        dish for dish in all_dishes
        if (language == 'en' and search_term_lower in dish.get(name_key, "").lower()) or \
           (language == 'zh' and name_to_find in dish.get(name_key, ""))
    ]

    if not contains_matches:
        logger.info(f"No {language} match found for '{name_to_find}'.")
        return []

    logger.info(f"Found {len(contains_matches)} 'contains' {language} match(es) for '{name_to_find}'.")
    return contains_matches[:4] # Return up to 4 "contains" matches

# --- Public Functions ---

async def get_extracted_dishes(portal_id: str) -> Dict[str, Any]:
    """
    Fetches a flattened list of all dishes for a portal, using a cache.
    This is the primary function for retrieving menu data for tool logic.
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

    extracted_dishes = []
    for category in raw_menu:
        for dish in category.get("items", []):
            extracted_dishes.append({
                "dish_id": dish.get("id"),
                "dish_name_en": dish.get("name", {}).get("en", ""),
                "dish_name_zh": dish.get("name", {}).get("zh", ""),
                "price": dish.get("price"),
                "options": dish.get("options"),
                "category_id": category.get("id"),
                "category_name_en": category.get("name", {}).get("en", ""),
                "category_name_zh": category.get("name", {}).get("zh", ""),
                "dish_details": dish # Keep full details for search results
            })

    # Save the processed list to cache
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "dishes": extracted_dishes}, f, ensure_ascii=False)
        logger.info(f"Saved fresh extracted dishes for portal {portal_id} to cache.")
    except IOError as e:
        logger.error(f"Failed to write to cache file {cache_file}: {e}")

    return {"success": True, "message": "Data from API", "data": extracted_dishes}

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
