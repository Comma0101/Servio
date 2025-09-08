import unittest
from unittest.mock import patch, MagicMock
from app.handlers.english_tool_logic import _restructure_combo_items
from app.handlers.combo_order_manager import combo_order_manager

class TestComboRestructuring(unittest.TestCase):

    def setUp(self):
        # Ensure a clean state for each test
        self.call_sid = "test_sid_123"
        combo_order_manager.clear_order(self.call_sid)

    def test_restructures_customized_combo(self):
        """
        Confirms that a flat list of proteins is correctly grouped into a
        single 'Customized Combo' item when a customized combo is active.
        """
        # Simulate starting a customized combo order
        combo_order_manager.start_combo_order("Customized Combo", self.call_sid)
        # Simulate the order being completed for enrichment lookup
        active_order = combo_order_manager.get_completed_order(self.call_sid)
        combo_order_manager.add_completed_combo(self.call_sid, active_order)

        flat_items_from_ai = [
            {"name": "green mussels", "quantity": 3.5},
            {"name": "king crab legs", "quantity": 3},
            {"name": "Potatoes", "quantity": 3}
        ]

        restructured_items = _restructure_combo_items(flat_items_from_ai, self.call_sid)

        self.assertEqual(len(restructured_items), 2)
        self.assertEqual(restructured_items[0]["name"], "Customized Combo")
        self.assertEqual(restructured_items[0]["quantity"], 1)
        self.assertIn("Potatoes", [item["name"] for item in restructured_items])
        
        proteins_in_options = restructured_items[0]["options"]["proteins"]
        self.assertEqual(len(proteins_in_options), 2)
        self.assertIn({"name": "green mussels", "size": "3.5 lbs"}, proteins_in_options)
        self.assertIn({"name": "king crab legs", "size": "3 lbs"}, proteins_in_options)

    def test_does_not_affect_fixed_combos(self):
        """
        Ensures that the restructuring logic does not alter items for a
        fixed combo order.
        """
        # Simulate starting a fixed combo order
        combo_order_manager.start_combo_order("COMBO #1", self.call_sid)
        active_order = combo_order_manager.get_completed_order(self.call_sid)
        combo_order_manager.add_completed_combo(self.call_sid, active_order)

        original_items = [
            {"name": "COMBO #1", "quantity": 1, "options": {"flavor": "Cajun"}}
        ]

        processed_items = _restructure_combo_items(original_items, self.call_sid)

        self.assertEqual(original_items, processed_items)

    def test_does_not_affect_regular_items(self):
        """
        Ensures that regular items (even those with protein names) are not
        mistaken for combo proteins.
        """
        # No active combo order for this test
        original_items = [
            {"name": "Fried Shrimp Basket", "quantity": 1},
            {"name": "Coke", "quantity": 2}
        ]

        processed_items = _restructure_combo_items(original_items, self.call_sid)

        self.assertEqual(original_items, processed_items)

if __name__ == "__main__":
    unittest.main()
