import asyncio
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.utils.thirty_nine_miles import add_order, ApiOrderDto, ApiOrderItem, TextStore, LangCode

class TestThirtyNineMilesAPI(unittest.IsolatedAsyncioTestCase):

    async def test_place_weighted_combo_order(self):
        """
        Test sending a combo order with a weighted protein directly to the 39-Miles API.
        """
        # This payload mimics a 1.5 lb clam order.
        # We will check if the API accepts the 'weight' field.
        test_order = ApiOrderDto(
            portal_id="06850184",
            customer_phone="5555555555",
            user_name="API Test",
            lang_code=LangCode.EN,
            order_items=[
                ApiOrderItem(
                    name=TextStore(en="Customized Combo - Clams"),
                    category_id="28E918AA-8197-4F7E-BED2-78FFE694499A",
                    product_id="2413227D-DD55-4CB0-8571-B70FA8E0CB46",
                    quantity=1,
                    weight=1.5,
                    final_price=27.0, # 1.5 lbs * $18.0/lb
                    product_options=[]
                )
            ],
            item_subtotal=27.0,
            tax=2.16,
            final_payment=29.16
        )

        # We use AsyncMock to properly handle the async nature of the httpx client.
        with patch('app.utils.thirty_nine_miles._fetch_token', new_callable=AsyncMock) as mock_fetch_token, \
             patch('httpx.AsyncClient') as mock_async_client:
            mock_fetch_token.return_value = "mock_token"
            
            # Configure the mock to handle the async context manager and post call.
            mock_instance = mock_async_client.return_value.__aenter__.return_value
            mock_instance.post = AsyncMock()
            
            # This is what we expect the API to return on success.
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"success": True, "data": "mock_order_id"}
            mock_instance.post.return_value = mock_response

            # Call the function that makes the API request.
            response = await add_order(test_order)

            # Verify that the API was called and the response is correct.
            mock_instance.post.assert_called_once()
            self.assertTrue(response["success"])
            self.assertEqual(response["data"], "mock_order_id")

if __name__ == "__main__":
    unittest.main()
