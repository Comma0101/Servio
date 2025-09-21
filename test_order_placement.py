import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

# Adjust the path to import from the app directory
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from app.handlers.english_tool_logic import handle_order_summary_thirty_nine_miles_en
from app.handlers.order_manager import order_manager
from app.utils.thirty_nine_miles import find_dish_by_english_name

async def main():
    """
    Test script to simulate placing an order for French Fries with a required option
    and inspect the final payload sent to the 39-Miles API.
    """
    # --- Test Configuration ---
    test_call_sid = "test_call_12345"
    test_client_id = "LIMF"
    test_portal_id = "14741041" # LIMF's portal ID
    item_to_order = "French Fries"
    selected_option = "Hot"
    
    print("--- Starting Test: Order Placement for Item with Required Options ---")

    # 1. Clear any previous state for this test call SID
    order_manager.clear_cart(test_call_sid)
    print(f"Cleared cart for call SID: {test_call_sid}")

    # 2. Find the item details from the menu data
    # We need to simulate the check_menu_item_english function's first step
    found_dishes = await find_dish_by_english_name(test_portal_id, item_to_order)
    if not found_dishes:
        print(f"TEST FAILED: Could not find '{item_to_order}' on the menu.")
        return
        
    # The name in the menu data might be in the 'zh' field
    dish_details = found_dishes[0].get("dish_details", {})
    if not dish_details:
        print(f"TEST FAILED: Dish details not found for '{item_to_order}'.")
        return

    # 3. Simulate starting the item order, which puts it in a pending state
    order_manager.start_item(dish_details, 1, test_call_sid)
    print(f"Started order for '{item_to_order}'. Awaiting option selection.")

    # 4. Simulate the user selecting the required option
    order_manager.process_selection(selected_option, test_call_sid)
    print(f"Processed selection: '{selected_option}'. Item should now be in the cart.")

    # 5. Prepare the payload for the order_summary function call
    # This simulates the data sent by the Deepgram agent
    summary_input_data = {
        "items": order_manager.get_cart(test_call_sid), # Use the actual cart content
        "summary": "DONE",
        "total_price": 0 # Price will be recalculated, so this can be 0
    }

    # 6. Create mock objects for services that we don't need to run
    mock_deepgram_service = MagicMock()
    mock_deepgram_service.send_json = AsyncMock()

    # 7. Call the function we want to test
    print("\n--- Calling handle_order_summary_thirty_nine_miles_en ---")
    await handle_order_summary_thirty_nine_miles_en(
        function_call_id="test_func_id_123",
        function_name="order_summary",
        input_data=summary_input_data,
        deepgram_service=mock_deepgram_service,
        websocket=None,
        stream_sid=None,
        caller_phone="+15551234567",
        call_sid=test_call_sid,
        client_id=test_client_id
    )
    
    print("--- Test Finished ---")
    # The actual payload sent to the 39-Miles API will be printed in the logs
    # by the `add_order` function in `thirty_nine_miles.py`.

if __name__ == "__main__":
    asyncio.run(main())
