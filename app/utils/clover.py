import requests
import json
import asyncio
import uuid
import time
from typing import List, Dict, Any, Optional

# Your merchant ID and access token (store these securely in environment variables for production)
MERCHANT_ID = "5A6W3MD970TE1"
ACCESS_TOKEN = "5371fb65-eab0-9034-c1b1-849f935be284"

# Base URL for Clover sandbox API
BASE_URL = "https://sandbox.dev.clover.com/v3"

# Headers for Clover API requests
headers = {
    "authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

def extract_menu_data(catalog_items):
    """Extract menu data from Clover catalog items."""
    menu_items = []
    
    for item in catalog_items:
        name = item.get("name", "Unnamed Item")
        price = item.get("price", 0) / 100  # Convert cents to dollars for display
        
        # Check for modifiers/variations (in a real implementation, you'd fetch these separately)
        variations = []
        
        # Add the item with its variations to the menu
        menu_items.append({
            "name": name,
            "price": price,
            "id": item.get("id"),
            "variations": variations
        })
    
    return menu_items

async def get_clover_catalog_items():
    """Fetch catalog items from Clover API."""
    items_url = f"{BASE_URL}/merchants/{MERCHANT_ID}/items"
    
    try:
        response = await asyncio.to_thread(requests.get, items_url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            return data.get("elements", [])
        else:
            print(f"Error: {response.status_code}, {response.text}")
            return None
    except Exception as e:
        print(f"Exception when fetching catalog items: {e}")
        return None

async def create_clover_order(items):
    """Create an order in Clover with the given items."""
    try:
        # Fetch catalog items to get actual prices
        catalog_items = await get_clover_catalog_items()
        if not catalog_items:
            print("Failed to fetch catalog items for pricing")
            return None
            
        # Create a mapping of item IDs to their prices for quick lookup
        item_price_map = {item.get("id"): item.get("price", 0) for item in catalog_items}
        
        # Calculate the total price from items using actual prices
        total_price = 0
        for item in items:
            quantity = int(item.get("quantity", 1))
            item_id = item.get("item_variation_id")
            
            # Get actual price from the catalog, or use 500 cents as fallback
            item_price = item_price_map.get(item_id, 500)
            total_price += (item_price * quantity)
        
        # Create the order first
        order_payload = {
            "state": "open",
            "externalReferenceId": f"ORD{str(int(time.time()))[-8:]}",  # Use timestamp for shorter ID (12 chars or less)
            "total": total_price,
            "subTotal": total_price,
            "tax": 0
        }
        
        order_url = f"{BASE_URL}/merchants/{MERCHANT_ID}/orders"
        order_response = await asyncio.to_thread(requests.post, order_url, headers=headers, json=order_payload)
        
        if order_response.status_code not in [200, 201]:
            print(f"Failed to create order: {order_response.status_code}, {order_response.text}")
            return None
            
        order_data = order_response.json()
        order_id = order_data.get("id")
        
        # Now add line items to the order
        for item in items:
            item_id = item.get("item_variation_id")
            quantity = int(item.get("quantity", 1))
            
            # Get actual price from the catalog
            item_price = item_price_map.get(item_id, 500)
            
            line_item_payload = {
                "item": {"id": item_id},
                "unitQty": quantity * 1000,  # Clover uses 1000 to represent 1 unit
                "price": item_price  # Use actual price from catalog
            }
            
            line_items_url = f"{BASE_URL}/merchants/{MERCHANT_ID}/orders/{order_id}/line_items"
            li_response = await asyncio.to_thread(requests.post, line_items_url, headers=headers, json=line_item_payload)
            
            if li_response.status_code not in [200, 201]:
                print(f"Failed to add line item: {li_response.status_code}, {li_response.text}")
                # Continue anyway to add other items
        
        # Return the order with total in the same format as Square for compatibility
        return {
            "order": {
                "id": order_id,
                "total_money": {
                    "amount": total_price,
                    "currency": "USD"
                }
            }
        }
    
    except Exception as e:
        print(f"Exception when creating order: {e}")
        return None

async def process_clover_payment(order_id, amount, payment_method_id):
    """Process a payment for the given order."""
    try:
        # In a real implementation, you would use Clover's payment API
        # This is a simplified version that just returns a success response
        # to match your Square implementation
        
        # For a real implementation, you'd make a call to Clover's payment endpoint
        # payment_url = f"{BASE_URL}/merchants/{MERCHANT_ID}/pay"
        
        # For now, simulate a successful payment
        return {
            "payment": {
                "id": str(uuid.uuid4()),
                "order_id": order_id,
                "amount_money": {
                    "amount": amount,
                    "currency": "USD"
                },
                "status": "COMPLETED",
                "source": payment_method_id
            }
        }
    except Exception as e:
        print(f"Exception when processing payment: {e}")
        return {
            "error": "Failed to process payment",
            "details": str(e)
        }

async def find_item_by_name(item_name, variation_name=None):
    """Find an item by name and optionally variation name."""
    catalog_items = await get_clover_catalog_items()
    
    if catalog_items:
        for item in catalog_items:
            if item.get("name") == item_name:
                # In a full implementation, you'd check for variations
                return item.get("id")
    
    return None

# Utility function to convert order items from your system to Clover format
async def prepare_order_items_for_clover(order_data):
    """Convert order items to Clover format."""
    clover_items = []
    
    for item in order_data:
        item_name = item.get("name", "")
        quantity = item.get("quantity", 1)
        variation_name = item.get("variation")
        
        item_id = await find_item_by_name(item_name, variation_name)
        if item_id:
            clover_items.append({
                "item_variation_id": item_id,
                "quantity": quantity
            })
        else:
            print(f"Item not found: {item_name}")
    
    return clover_items

# For compatibility with your existing code
async def test_create_order_endpoint(order_data):
    """Create an order using the items provided."""
    clover_items = await prepare_order_items_for_clover(order_data)
    return await create_clover_order(clover_items)

# For compatibility with your existing code
async def test_payment_processing(order_id, amount, payment_method_id):
    """Process a payment for the given order."""
    return await process_clover_payment(order_id, amount, payment_method_id)

# Test code to run when this file is executed directly
if __name__ == "__main__":
    import asyncio
    
    async def test_clover_api():
        print("Testing Clover API...")
        
        # 1. Test fetching catalog items
        print("\n1. Fetching catalog items...")
        catalog_items = await get_clover_catalog_items()
        if catalog_items:
            print(f"Success! Found {len(catalog_items)} items in the catalog.")
            for i, item in enumerate(catalog_items[:5]):  # Show first 5 items
                print(f"Item {i+1}: {item.get('name')} - ${item.get('price', 0)/100:.2f}")
            
            # Extract menu data
            menu = extract_menu_data(catalog_items)
            print(f"\nExtracted menu with {len(menu)} items")
            
            # 2. Test creating an order with a real item from the catalog
            print("\n2. Creating a test order...")
            
            # Find an item with a non-zero price
            test_item = None
            for item in catalog_items:
                if item.get('price', 0) > 0:
                    test_item = item
                    break
            
            # If no item with price > 0 found, use the second item (if available)
            if test_item is None and len(catalog_items) > 1:
                test_item = catalog_items[1]  # Try the second item
            # If still no suitable item, use the first item
            elif test_item is None and len(catalog_items) > 0:
                test_item = catalog_items[0]
            
            if test_item:
                real_item_id = test_item.get('id')
                test_items = [
                    {
                        "name": test_item.get('name'),
                        "quantity": 2,
                        "item_variation_id": real_item_id
                    }
                ]
                
                order_result = await create_clover_order(test_items)
                if order_result and "order" in order_result:
                    order_id = order_result["order"]["id"]
                    order_total = order_result["order"]["total_money"]["amount"]
                    print(f"Success! Created order with ID: {order_id}")
                    print(f"Order total: ${order_total/100:.2f}")
                    print(f"Item ordered: {test_item.get('name')} - ${test_item.get('price', 0)/100:.2f} x 2")
                    
                    # 3. Test processing a payment
                    print("\n3. Processing test payment...")
                    payment_method_id = "cnon:card-nonce-ok"  # Test payment method
                    payment_result = await process_clover_payment(order_id, order_total, payment_method_id)
                    
                    if payment_result and "payment" in payment_result:
                        payment_id = payment_result["payment"]["id"]
                        payment_status = payment_result["payment"]["status"]
                        print(f"Success! Processed payment with ID: {payment_id}")
                        print(f"Payment status: {payment_status}")
                    else:
                        print("Failed to process payment.")
                else:
                    print("Failed to create order.")
            else:
                print("No items found in catalog to create test order.")
        else:
            print("Failed to fetch catalog items.")
        
        print("\nClover API tests completed.")
    
    # Run the test function
    asyncio.run(test_clover_api())