"""
Constants utility for managing app constants
"""
import os
import json
import logging
from app.constants import CONSTANTS

# Configure logging
logger = logging.getLogger(__name__)

def get_restaurant_config(restaurant_id: str = None):
    """Get restaurant configuration from constants"""
    if restaurant_id is None:
        restaurant_id = os.getenv("RESTAURANT_ID", "LIMF")
    
    # Get the configuration from CONSTANTS
    config = CONSTANTS.get(restaurant_id, {})
    
    logger.info(f"Retrieved restaurant configuration for {restaurant_id}")
    return config

def get_restaurant_menu(restaurant_id: str = None):
    """Get restaurant menu from constants"""
    config = get_restaurant_config(restaurant_id)
    menu_json = config.get("MENU", "[]")
    
    # Handle both string and list format
    if isinstance(menu_json, str):
        try:
            menu_items = json.loads(menu_json)
        except json.JSONDecodeError:
            logger.error("Error parsing menu JSON")
            menu_items = []
    else:
        menu_items = menu_json
    
    return menu_items

def get_keywords(restaurant_id: str = None):
    """Get keywords for speech recognition from constants"""
    config = get_restaurant_config(restaurant_id)
    keywords = config.get("KEYWORDS", [])
    
    # Handle both string and list format
    if isinstance(keywords, str):
        try:
            keywords_list = json.loads(keywords)
        except json.JSONDecodeError:
            logger.error("Error parsing keywords JSON")
            keywords_list = []
    else:
        keywords_list = keywords
    
    logger.info(f"Retrieved {len(keywords_list)} keywords for speech recognition")
    return keywords_list
