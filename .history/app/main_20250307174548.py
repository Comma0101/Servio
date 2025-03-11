from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import os
from dotenv import load_dotenv
from app.routers import chat, chat_response, square
import asyncio

print("Starting application...")  # This should always print

# Load environment variables
load_dotenv()


# Create FastAPI app
app = FastAPI(
    title="Restaurant Voice Ordering System",
    description="A voice-based food ordering system using Twilio, OpenAI, and Square",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(chat_response.router, prefix="/api/v1", tags=["Chat Response"])
app.include_router(square.router, prefix="/api/v1", tags=["Square"])


# Root endpoint
@app.get("/", response_class=HTMLResponse)
async def root():
    return "Hello world!"


# Print available routes
@app.on_event("startup")
async def startup_event():
    print("Available routes:")
    for route in app.routes:
        print(f"Endpoint: {route.name}, Path: {route.path}")


@app.get("/test-original-openai")
async def test_original_openai():
    try:
        from app.utils.openai import create_chat_completion
        from app.constants import CONSTANTS

        # Get client configuration
        client_id = "LIMF"  # Use your default client ID

        if client_id not in CONSTANTS:
            return {"status": "error", "message": "Invalid client_id"}

        # Create a test conversation similar to your actual flow
        menu_string = ""
        for item in CONSTANTS[client_id]["MENU"]:
            menu_string += "\n" + item

        tax_string = f"The tax percentage is: {CONSTANTS[client_id]['TAX'] * 100}%"

        system_message = (
            CONSTANTS[client_id]["SYSTEM_MESSAGE"]
            + " Here is the menu:"
            + menu_string
            + "\n"
            + tax_string
        )
        user_message = "I'd like to order a cheeseburger"

        chat_history = [
            {"role": "system", "content": system_message},
            {
                "role": "user",
                "content": f"My phone number is +1234567890. {user_message}",
            },
        ]

        # Call OpenAI with your actual parameters
        response = await create_chat_completion(
            chat_history,
            model=CONSTANTS[client_id]["OPENAI_CHAT_MODEL"],
            max_tokens=CONSTANTS[client_id]["OPENAI_CHAT_MAX_TOKENS"],
            temperature=CONSTANTS[client_id]["OPENAI_CHAT_TEMPERATURE"],
            top_p=CONSTANTS[client_id]["OPENAI_CHAT_TOP_P"],
            functions=CONSTANTS[client_id].get("OPENAI_CHAT_TOOLS"),
        )

        return {
            "status": "success",
            "response": response,
            "model_used": CONSTANTS[client_id]["OPENAI_CHAT_MODEL"],
            "system_prompt_length": len(system_message),
        }

    except Exception as e:
        import traceback

        error_details = traceback.format_exc()
        return {"status": "error", "message": str(e), "details": error_details}

// ... existing code ...
@app.get("/test-square")
async def test_square():
    try:
        import logging
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger("test_square")
        
        from app.utils.square import (
            list_catalog_items,
            extract_menu_data,
            get_square_location_id,
            find_item_variation_id_by_name,
            create_square_order,
            process_square_payment
        )
        
        results = {}
        
        # Test 1: Get location ID
        logger.info("Test 1: Getting Square location ID...")
        if asyncio.iscoroutinefunction(get_square_location_id):
            location_id = await get_square_location_id()
        else:
            location_id = get_square_location_id()
        logger.info(f"Location ID result: {location_id}")
        results["location_id"] = {"success": location_id is not None, "value": location_id}
        
        # Test 2: List catalog items
        logger.info("Test 2: Listing catalog items...")
        if asyncio.iscoroutinefunction(list_catalog_items):
            catalog_data = await list_catalog_items()
        else:
            catalog_data = list_catalog_items()
        logger.info(f"Catalog items count: {len(catalog_data.get('objects', [])) if catalog_data else 0}")
        results["catalog_items"] = {
            "success": catalog_data is not None,
            "count": len(catalog_data.get("objects", [])) if catalog_data else 0
        }
        
        # Test 3: Extract menu data
        if catalog_data:
            logger.info("Test 3: Extracting menu data...")
            menu = extract_menu_data(catalog_data)
            logger.info(f"Menu items count: {len(menu)}")
            results["menu"] = {
                "success": len(menu) > 0,
                "count": len(menu),
                "first_item": menu[0] if menu else None
            }
        
        # Test 4: Find variation ID
        if catalog_data and "menu" in results:
            logger.info("Test 4: Finding variation ID by name...")
            # Get the first item name from the menu
            first_item_name = results["menu"]["first_item"]["name"] if results["menu"]["first_item"] else None
            logger.info(f"Looking for variation ID for item: {first_item_name}")
            if first_item_name:
                if asyncio.iscoroutinefunction(find_item_variation_id_by_name):
                    variation_id = await find_item_variation_id_by_name(first_item_name)
                else:
                    variation_id = find_item_variation_id_by_name(first_item_name)
                logger.info(f"Variation ID result: {variation_id}")
                results["variation_id"] = {
                    "success": variation_id is not None,
                    "value": variation_id,
                    "item_name": first_item_name
                }
        
        # Test 5: Create order (only if we have a variation ID)
        if location_id and results.get("variation_id", {}).get("success", False):
            logger.info("Test 5: Creating Square order...")
            variation_id = results["variation_id"]["value"]
            items = [{"item_variation_id": variation_id, "quantity": "1"}]
            logger.info(f"Creating order with items: {items}")
            if asyncio.iscoroutinefunction(create_square_order):
                order_result = await create_square_order(items, location_id)
            else:
                order_result = create_square_order(items, location_id)
            
            logger.info(f"Order creation result: {order_result}")
            order_id = order_result.get("order", {}).get("id") if order_result else None
            order_total = order_result.get("order", {}).get("total_money", {}).get("amount") if order_result else None
            
            results["create_order"] = {
                "success": order_result is not None and "order" in order_result,
                "order_id": order_id,
                "order_total": order_total
            }
            
            # Test 6: Process payment (only if order creation succeeded)
            if results["create_order"]["success"]:
                logger.info("Test 6: Processing Square payment...")
                order_id = results["create_order"]["order_id"]
                order_total = results["create_order"]["order_total"]
                # Use a test payment method ID for sandbox
                payment_method_id = "cnon:card-nonce-ok"
                
                logger.info(f"Processing payment for order ID: {order_id}")
                logger.info(f"Payment amount: {order_total}")
                logger.info(f"Payment method ID: {payment_method_id}")
                
                try:
                    if asyncio.iscoroutinefunction(process_square_payment):
                        payment_result = await process_square_payment(order_id, order_total, payment_method_id)
                    else:
                        payment_result = process_square_payment(order_id, order_total, payment_method_id)
                    
                    logger.info(f"Payment result: {payment_result}")
                    
                    payment_status = payment_result.get("payment", {}).get("status") if payment_result and "payment" in payment_result else None
                    payment_error = payment_result.get("error") if payment_result else "Unknown error"
                    
                    results["process_payment"] = {
                        "success": payment_result is not None and "payment" in payment_result,
                        "status": payment_status,
                        "error": payment_error if not results["process_payment"]["success"] else None
                    }
                except Exception as payment_error:
                    logger.error(f"Payment processing exception: {str(payment_error)}")
                    results["process_payment"] = {
                        "success": False,
                        "error": str(payment_error)
                    }
        
        return {
            "status": "success",
            "results": results,
            "all_tests_passed": all(test.get("success", False) for test in results.values() if isinstance(test, dict))
        }
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return {"status": "error", "message": str(e), "details": error_details}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("FASTAPI_PORT", 8000))
    # Add this to your main.py to debug

    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
