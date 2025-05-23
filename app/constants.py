from app.utils.square import extract_menu_data
import json
import requests

# Define headers for Square API requests
headers = {
    "Square-Version": "2022-04-20",
    "Authorization": "Bearer EAAAl9eu_8NtFKUH0Tx1jzwCJ8nMHydO1KnW0S6caBXjJv7nqcpVM22ye_vTwObB",
    "Content-Type": "application/json",
}


# Synchronous version of list_catalog_items
def sync_list_catalog_items():
    url = "https://connect.squareupsandbox.com/v2/catalog/list"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}, {response.text}")
        return None


# Get menu data synchronously
menu_data = sync_list_catalog_items()
menu = extract_menu_data(menu_data) if menu_data else []

CONSTANTS = {
    "LIMF": {
        "SYSTEM_MESSAGE": (
            f"You are an assistant at KK restaurant. "
            "During the conversation, collect items, quantities, and variations. "
            "Ask for missing variations. Use 'IN PROGRESS' for partial orders and 'DONE' for completed orders. "
            "When the you think that the order is complete, use the 'order_summary' function to provide a structured summary of the order for backend processing. "
            # "Before you use 'order_summary you should say'Please wait while I process your order.'" 
            # "At the end, summarize the order in this format: "
            # '{"items": [{"name": "<string>", "quantity": <int>, "variation": "<string | null>"}], '
            # '"total_price": <float>, "summary": "<IN PROGRESS | DONE>"}'
        ),
        "INITIAL_ASSISTANT_MESSAGE": "Welcome to KK restaurant, what would you like to order today?",
        # "Welcome to Love Is My Form restaurant. Would you like to place an order for pickup?",
        "INITIAL_USER_MESSAGE": "Hello, If I am ordering, you should tell me if I order something that is not in the menu.  summarize the order",
        # "If I order something not in the menu, let me know and give me an alternative and when I am done, summarize the dishes list and let me know the total amount due in Rupees and say 'plus taxes'. Then ask me my name and if I want to pick up the order now or later. If later, ask me the date and time. If you feel the phone call is over, say 'DONE' only",
        "ASSISTANT_ID": "asst_OSWVXg4hN8GozhcKNLjZVxGk",
        "TWILIO_LANGUAGE": "en-US",
        "TWILIO_HINTS": "place an order for pickup, information about the restaurant",
        "TWILIO_SPEECH_TIMEOUT": "1",
        # "TWILIO_SPEECH_MODEL": "phone_call",
        "TWILIO_SPEECH_MODEL": "experimental_conversations",
        # "TWILIO_ENHANCED": "true",
        # "TWILIO_CONFIDENCE_THRESHOLD": 0.4,
        "TWILIO_VOICE": "Polly.Joanna-Neural",
        "MENU": json.dumps(menu),
        "TAX": 0.18,
 }
}
