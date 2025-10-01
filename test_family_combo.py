import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.handlers.combo_order_manager import ComboOrderManager
from app.handlers.order_manager import OrderManager
from app.handlers.english_tool_logic import handle_combo_item_selection, handle_order_summary_thirty_nine_miles_en

@pytest.fixture
def combo_order_manager():
    """Fixture for a clean ComboOrderManager instance."""
    return ComboOrderManager()

@pytest.fixture
def order_manager():
    """Fixture for a clean OrderManager instance."""
    return OrderManager()

@pytest.fixture
def mock_deepgram_service():
    """Fixture for a mock Deepgram service."""
    return AsyncMock()

@pytest.mark.asyncio
async def test_family_combo_order_flow(combo_order_manager, order_manager, mock_deepgram_service):
    """
    Tests the entire Family Combo order flow, from initiation to finalization,
    ensuring the crab selection is correctly handled and validated.
    """
    call_sid = "test_call_sid"
    live_menu_data = [
        {
            "dish_name_en": "FAMILY COMBO",
            "dish_details": {
                "name": {"en": "FAMILY COMBO"},
                "optionGroups": [
                    {"name": {"en": "CHOOSE ONE"}, "options": [{"name": {"en": "Shrimp head on"}}]},
                    {"name": {"en": "CHOOSE ONE"}, "options": [{"name": {"en": "Fresh crawfish"}}]},
                    {"name": {"en": "CHOOSE ONE"}, "options": [{"name": {"en": "Clams"}}]},
                    {"name": {"en": "CHOOSE YOUR CRAB"}, "options": [{"name": {"en": "3 Lobster Tails"}}]},
                    {"name": {"en": "PICK YOUR FLAVOR"}, "options": [{"name": {"en": "No Flavor"}}]},
                    {"name": {"en": "HOW SPICY"}, "options": [{"name": {"en": "Hot"}}]}
                ]
            }
        }
    ]

    # Start the combo order
    combo_order_manager.start_combo_order("FAMILY COMBO", call_sid, live_menu_data)

    # Simulate user selections
    await handle_combo_item_selection("call_id_1", "handle_combo_item_selection", {"user_input": "Shrimp"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_2", "handle_combo_item_selection", {"user_input": "Shrimp head on"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_3", "handle_combo_item_selection", {"user_input": "Crawfish"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_4", "handle_combo_item_selection", {"user_input": "Fresh crawfish"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_5", "handle_combo_item_selection", {"user_input": "Clams"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_6", "handle_combo_item_selection", {"user_input": "Clams"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_7", "handle_combo_item_selection", {"user_input": "3 Lobster Tails"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_8", "handle_combo_item_selection", {"user_input": "No Flavor"}, mock_deepgram_service, call_sid)
    await handle_combo_item_selection("call_id_9", "handle_combo_item_selection", {"user_input": "Hot"}, mock_deepgram_service, call_sid)

    # Confirm the order
    await handle_combo_item_selection("call_id_10", "handle_combo_item_selection", {"user_input": "yes"}, mock_deepgram_service, call_sid)

    # Verify that the combo is in the cart
    cart = order_manager.get_cart(call_sid)
    assert len(cart) == 1
    assert cart[0]["name"] == "FAMILY COMBO"
    assert cart[0]["options"]["crab"] == "3 Lobster Tails"

    # Verify that the order is not placed
    assert not mock_deepgram_service.send_json.call_args.args[0].get("content", {}).get("order_placed")
