import unittest
import json
from unittest.mock import patch
from app.handlers.combo_order_manager import ComboOrderManager

class TestComboOrderFlow(unittest.TestCase):

    def setUp(self):
        """Set up a fresh ComboOrderManager for each test."""
        self.manager = ComboOrderManager()
        # Mock the menu data to ensure tests are repeatable
        with open('app/utils/testing_menu.json', 'r') as f:
            self.manager.menu_data = json.load(f)
        self.call_sid = "test_call_123"

    def test_update_selection_during_confirmation(self):
        """
        Tests that a user can successfully update an option during the final
        confirmation prompt without restarting the order flow.
        """
        # 1. Start a customized combo order
        self.manager.start_combo_order("Customized Combo", self.call_sid)
        
        # 2. Add a protein and size
        self.manager.process_selection("clams", self.call_sid)
        self.manager.process_selection("1 lb", self.call_sid)
        
        # 3. Say no to more proteins to move to next options
        self.manager.process_selection("no", self.call_sid)
        
        # 4. Make selections for flavor and spice
        self.manager.process_selection("garlic butter", self.call_sid) # Flavor
        self.manager.process_selection("medium", self.call_sid)      # Spice
        
        # 5. Skip the optional extras to get to the confirmation
        print("\nStep 5: Skipping optional extras...")
        response = self.manager.process_selection("no thanks", self.call_sid) # Extras
        print(f"Response after skipping extras: {response}")
        print(f"State after skipping extras: {self.manager.active_orders.get(self.call_sid, {}).get('state')}")
        print(f"Current step after skipping extras: {self.manager.active_orders.get(self.call_sid, {}).get('current_step')}")

        # 6. At confirmation, request a change
        print("\nStep 6: Checking for confirmation prompt...")
        self.assertEqual(response['action'], 'confirm_order')
        self.assertIn("Is that correct?", response['message_for_agent'])
        
        # 7. User says "change to mild"
        update_response = self.manager.process_selection("actually, can I get mild spice", self.call_sid)
        
        # 8. Verify the system updated correctly and is re-confirming
        self.assertEqual(self.manager.active_orders[self.call_sid]['state'], 'AWAITING_FINAL_CONFIRMATION')
        self.assertEqual(update_response['action'], 'confirm_order')
        self.assertIn("Okay, I've updated the Spice Level to Mild.", update_response['message_for_agent'])
        self.assertIn("Is that correct?", update_response['message_for_agent'])
        
        # 9. Check that the internal state reflects the change
        self.assertEqual(self.manager.active_orders[self.call_sid]['selections']['Spice Level'], 'Mild')

if __name__ == "__main__":
    unittest.main()
