import os, requests
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from app.models.schemas import OrderItem  # if you have such imports

ACCESS_TOKEN = "EAAAlxqe8a_RXLtv0DOqN9ANgLPVMG9oO_bfzgUG35xrNVl9aw6FXJ6b7i-lp9n0"

# Headers for Square API requests
headers = {
    "Square-Version": "2022-04-20",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

current_order_id = None
current_order_total = None


def extract_menu_data(menu):
    # List to store extracted menu items
    menu_items = []

    # Loop through objects in the dictionary
    for item in menu.get("objects", []):
        if item.get("type") == "ITEM":
            # Extract item name and description
            item_data = item.get("item_data", {})
            name = item_data.get("name", "Unnamed Item")

            # Extract variations and prices
            variations = []
            for variation in item_data.get("variations", []):
                variation_data = variation.get("item_variation_data", {})
                variation_name = variation_data.get("name", "No Name")
                price = (
                    variation_data.get("price_money", {}).get("amount", 0) / 100
                )  # Convert to dollars
                variations.append({"name": variation_name, "price": price})

            # Store the item with its variations
            menu_items.append({"name": name, "variations": variations})

    return menu_items


async def retrieve_square_order(order_id):
    url = f"https://connect.squareupsandbox.com/v2/orders/{order_id}"

    response = await asyncio.to_thread(requests.get, url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        return {
            "error": "Failed to retrieve order",
            "status_code": response.status_code,
            "response_text": response.text,
        }


async def test_payment_processing(order_id, amount, payment_method_id):
    # URL for the /process-payment route of your FastAPI app
    url = "http://127.0.0.1:8000/api/v1/process-payment"

    # Payload structure as expected by your /process-payment route
    payload = {
        "order_id": order_id,
        "amount": amount,  # Amount in the smallest currency unit (e.g., cents for USD)
        "payment_method_id": payment_method_id,
    }

    response = await asyncio.to_thread(requests.post, url, json=payload)

    # Check if the response is successful and has JSON content
    if response.status_code == 200:
        return response.json()
    else:
        return {
            "error": "Failed to test payment processing",
            "status_code": response.status_code,
            "response_text": response.text,
        }


async def test_create_order_endpoint(order_data):
    items = []
    for item in order_data:
        item_name = item["name"]
        quantity = item["quantity"]
        variation_name = item.get("variation", None)  # Get variation name if provided

        variation_id = await find_item_variation_id_by_name(item_name, variation_name)
        if variation_id:
            items.append({"item_variation_id": variation_id, "quantity": quantity})
        else:
            print(
                f"Variation ID not found for item: {item_name} with variation: {variation_name}"
            )

    if not items:
        return {"error": "No valid items or quantities found"}

    url = "http://127.0.0.1:8000/api/v1/create-order"
    data = {"items": items}

    response = await asyncio.to_thread(requests.post, url, json=data)
    return response.json()


async def get_square_location_id():
    square_api_url = "https://connect.squareupsandbox.com/v2/locations"
    response = await asyncio.to_thread(requests.get, square_api_url, headers=headers)

    if response.status_code == 200:
        locations = response.json().get("locations", [])
        if locations:
            return locations[0]["id"]
        else:
            print("No locations found.")
            return None
    else:
        print("Failed to fetch locations.")
        return None


async def list_catalog_items():
    url = "https://connect.squareupsandbox.com/v2/catalog/list"
    response = await asyncio.to_thread(requests.get, url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}, {response.text}")
        return None


async def find_item_variation_id_by_name(item_name, variation_name=None):
    catalog_data = await list_catalog_items()
    if catalog_data:
        items = catalog_data.get("objects", [])
        for item in items:
            if item["type"] == "ITEM" and item["item_data"].get("name") == item_name:
                variations = item["item_data"].get("variations", [])
                for variation in variations:
                    # Check if the variation name matches, if provided
                    if (
                        not variation_name
                        or variation["item_variation_data"]["name"] == variation_name
                    ):
                        return variation["id"]
    return None


async def process_square_payment(order_id, amount, payment_method_id):
    url = "https://connect.squareupsandbox.com/v2/payments"

    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "amount_money": {
            "amount": amount,  # Amount in the smallest currency unit (e.g., cents for USD)
            "currency": "USD",
        },
        "source_id": payment_method_id,
        "order_id": order_id,
        "accept_partial_authorization": False,  # Set to True if you want to accept partial authorizations
    }

    response = await asyncio.to_thread(
        requests.post, url, json=payload, headers=headers
    )

    if response.status_code == 200:
        return response.json()
    else:
        return {
            "error": "Failed to process payment",
            "status_code": response.status_code,
            "response_text": response.text,
        }


async def create_square_order(items, location_id):
    line_items = []
    for item in items:
        line_item = {
            "catalog_object_id": item["item_variation_id"],
            "quantity": str(item["quantity"]),
        }
        line_items.append(line_item)

    # Enhanced fulfillment data
    fulfillment_data = {
        "type": "PICKUP",
        "state": "PROPOSED",
        "pickup_details": {
            "recipient": {
                "display_name": "Test Customer",
                "phone_number": "+12025550142",  # Test phone number
                "email": "test@example.com",
            },
            "schedule_type": "ASAP",
            "prep_time_duration": "PT30M",
            "note": "Test order from FastAPI application",
        },
    }

    # Current timestamp for the order
    import datetime

    current_time = datetime.datetime.utcnow().isoformat() + "Z"

    body = {
        "idempotency_key": str(uuid.uuid4()),
        "order": {
            "location_id": location_id,
            "line_items": line_items,
            "fulfillments": [fulfillment_data],
            "state": "OPEN",  # Explicitly set the state to OPEN
            "reference_id": f"test-order-{uuid.uuid4().hex[:8]}",  # Add a reference ID
            "source": {"name": "FastAPI Test"},
            "customer_id": "JDKYHBWT1D4F8MFH63DBMEN8Y4",  # Optional: Add if you have a customer ID
            "created_at": current_time,
            "metadata": {"test_order": "true", "created_by": "FastAPI Test"},
        },
    }

    url = "https://connect.squareupsandbox.com/v2/orders"
    response = await asyncio.to_thread(requests.post, url, headers=headers, json=body)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}, {response.text}")
        return None
