"""
Menu Formatter - Utilities for formatting restaurant menu data
"""
import json
import logging
import os
import datetime
from typing import List, Dict, Any
from app.utils.constants import get_restaurant_config

# Configure logging
logger = logging.getLogger(__name__)

# Note: Using get_restaurant_config from app.utils.constants instead of defining it here

def format_menu_for_sms(menu_items=None, client_id="LIMF"):
    """
    Format the restaurant menu for SMS
    
    Args:
        menu_items: List of menu items to format, if None will be retrieved from constants
        client_id: Client identifier for tracking
        
    Returns:
        str: Formatted menu text for SMS
    """
    try:
        # If menu_items not provided, get from restaurant configuration
        if menu_items is None:
            # Get restaurant configuration
            restaurant_config = get_restaurant_config(client_id)
            menu_json = restaurant_config.get("MENU", "[]")
            
            # Handle both string and list format
            if isinstance(menu_json, str):
                try:
                    menu_items = json.loads(menu_json)
                except json.JSONDecodeError:
                    logger.error("Error parsing menu JSON")
                    menu_items = []
            else:
                menu_items = menu_json
                
        logger.info(f"Formatting menu with {len(menu_items)} items for SMS for client {client_id}")
        
        # Format the menu text
        menu_text = f"KK Restaurant Menu:\n\n"
        
        for i, item in enumerate(menu_items, 1):
            item_name = item.get("name", "Unknown Item")
            menu_text += f"{i}. {item_name}\n"
            
            # Add variations if available
            variations = item.get("variations", [])
            for j, variation in enumerate(variations, 1):
                variation_name = variation.get("name", "Regular")
                price = variation.get("price", "$0.00")
                menu_text += f"   {chr(96+j)}. {variation_name} - {price}\n"
            
            # Add a space between items
            menu_text += "\n"
        
        # Add ordering instructions
        menu_text += "To order, simply say the item number and quantity.\n"
        menu_text += "Thank you for choosing KK Restaurant!"
        
        return menu_text.strip()
    except Exception as e:
        logger.error(f"Error formatting menu for SMS: {e}")
        return "Sorry, the menu is currently unavailable. Please try again later."

def format_summary_for_sms(items: List[Dict[str, Any]], total: float):
    """Format order summary for SMS"""
    try:
        summary_text = "Your Order:\n\n"
        
        # Format each ordered item
        for item in items:
            item_name = item.get("name", "Unknown Item")
            variation = item.get("variation", "Regular")
            quantity = item.get("quantity", 1)
            price = item.get("price", 0.0)
            
            # Format the price as a string with currency symbol
            price_str = f"${price:.2f}" if isinstance(price, (int, float)) else price
            
            summary_text += f"{quantity}x {item_name}"
            if variation:
                summary_text += f" ({variation})"
            summary_text += f" - {price_str}\n"
        
        # Add total
        total_str = f"${total:.2f}" if isinstance(total, (int, float)) else total
        summary_text += f"\nTotal: {total_str}"
        
        # Add estimated ready time
        ready_time = datetime.datetime.now() + datetime.timedelta(minutes=20)
        ready_time_str = ready_time.strftime("%I:%M %p")
        summary_text += f"\n\nYour order will be ready for pickup around {ready_time_str}."
        
        return summary_text
    except Exception as e:
        logger.error(f"Error formatting order summary for SMS: {e}")
        return "Order summary unavailable. Please call the restaurant for details."

def format_menu_for_voice():
    """Format the restaurant menu for voice response"""
    try:
        # Get restaurant configuration
        restaurant_config = get_restaurant_config()
        menu_json = restaurant_config.get("MENU", "[]")
        
        # Handle both string and list format
        if isinstance(menu_json, str):
            try:
                menu_items = json.loads(menu_json)
            except json.JSONDecodeError:
                logger.error("Error parsing menu JSON")
                menu_items = []
        else:
            menu_items = menu_json
            
        # Format the menu text with natural pauses for TTS
        menu_text = "Here are some popular items on our menu. "
        
        for i, item in enumerate(menu_items[:5], 1):  # Limit to first 5 items for voice
            item_name = item.get("name", "Unknown Item")
            
            # Get the first variation's price if available
            variations = item.get("variations", [])
            price_str = ""
            if variations:
                price = variations[0].get("price", "$0.00")
                price_str = f" for {price}"
            
            menu_text += f"{item_name}{price_str}. "
            
            # Add pause after every second item
            if i % 2 == 0:
                menu_text += "<break time='500ms'/> "
        
        menu_text += "You can ask me about any specific items or categories."
        return menu_text
    except Exception as e:
        logger.error(f"Error formatting menu for voice: {e}")
        return "Our menu includes a variety of delicious items. Please ask me about specific dishes."
