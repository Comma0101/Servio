import unittest
import json
from unittest.mock import patch, MagicMock
from app.utils.thirty_nine_miles import ThirtyNineMilesService
from app.utils.thirty_nine_miles import ApiOrderItem, OrderCreate

class TestQuantityValidation(unittest.TestCase):
    def setUp(self):
        self.service = ThirtyNineMilesService()
        with open('app/utils/testing_menu.json', 'r') as f:
            self.menu_data = json.load(f)

    @patch('app.utils.thirty_nine_miles.get_menu_from_square')
    def test_fractional_quantity_is_rounded_up(self, mock_get_menu):
        """
        Test that fractional quantities are rounded up to the nearest integer
        before being passed to the ApiOrderItem model.
        """
        mock_get_menu.return_value = self.menu_data
        
        order_data = {
            "items": [
                {"name": "green mussels", "quantity": 3.5},
                {"name": "king crab legs", "quantity": 3.0},
                {"name": "Potatoes", "quantity": 2}
            ]
        }

        # This call will raise a ValidationError if quantities are not handled correctly
        try:
            enriched_items = self.service._enrich_order_items(order_data["items"], "Customized Combo")
            
            # Verify that the fractional quantity was rounded up
            self.assertEqual(enriched_items[0].quantity, 4)
            self.assertEqual(enriched_items[1].quantity, 3)
            self.assertEqual(enriched_items[2].quantity, 2)
            
        except Exception as e:
            self.fail(f"Order processing failed with unexpected exception: {e}")

if __name__ == "__main__":
    unittest.main()
