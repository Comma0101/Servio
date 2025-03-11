from app.utils.square import extract_menu_data, list_catalog_items
import json


menu_data = list_catalog_items()
menu = extract_menu_data(menu_data)

CONSTANTS = {
    "LIMF": {
        "SYSTEM_MESSAGE": (
            f"You are an assistant at KK restaurant. "
            "During the conversation, collect items, quantities, and variations. "
            "Ask for missing variations. Use 'IN PROGRESS' for partial orders and 'DONE' for completed orders. "
            "When the you think that the order is complete, use the 'order_summary' function to provide a structured summary of the order for backend processing. "
            "At the end, summarize the order in this format: "
            '{"items": [{"name": "<string>", "quantity": <int>, "variation": "<string | null>"}], '
            '"total_price": <float>, "summary": "<IN PROGRESS | DONE>"}'
        ),
        "INITIAL_ASSISTANT_MESSAGE": "Welcome to KK restaurant, what would you like to order today?",
        # "Welcome to Love Is My Form restaurant. Would you like to place an order for pickup?",
        "INITIAL_USER_MESSAGE": "Hello, If I am ordering, you should tell me if I order something that is not in the menu. after summarize the order",
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
        "OPENAI_CHAT_MODEL": "gpt-4o-mini",
        "OPENAI_CHAT_MAX_TOKENS": 150,
        "OPENAI_CHAT_TEMPERATURE": 0.5,
        "OPENAI_CHAT_TOP_P": 1,
        "OPENAI_CHAT_SEED": 1,
        "MENU": json.dumps(menu),
        "TAX": 0.18,
        "OPENAI_CHAT_TOOLS_RESPONSES": {
            "place_restaurant_order": {
                "SUCCESS": "Thank you for your order. We will send you the order summary via SMS shortly. Goodbye.",
                "FAILURE": "Sorry, we could not place your order at this time. Please try again later. Goodbye.",
            },
        },
        "OPENAI_CHAT_TOOLS": [
            {
                "name": "order_summary",
                "description": "Summarize the order at the end of the conversation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "quantity": {"type": "integer"},
                                    "variation": {"type": ["string", "null"]},
                                },
                                "required": ["name", "quantity"],
                            },
                        },
                        "total_price": {"type": "number"},
                        "summary": {"type": "string", "enum": ["IN PROGRESS", "DONE"]},
                    },
                    "required": ["items", "total_price", "summary"],
                },
            }
        ],
    }
}
