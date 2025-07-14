import asyncio
import os
import sys
from datetime import datetime, timedelta
import json

# Add the project root to the Python path to allow imports from 'app'
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.utils.thirty_nine_miles import (
    add_order as add_thirty_nine_miles_order,
    get_extracted_dishes,
    ApiOrderDto,
    ApiOrderItem,
    TextStore,
    LangCode
)

async def test_single_dish(dish_identifier: str, schedule_time_str: str = None):
    """
    Tests a single dish by its name or ID, with an optional schedule time.
    """
    portal_id = "06850184"
    print(f"--- TESTING SINGLE DISH: {dish_identifier} ---")

    menu_response = await get_extracted_dishes(portal_id)
    if not menu_response.get("success"):
        print("Failed to retrieve the menu.")
        return

    all_dishes = menu_response.get("data", [])
    target_dish = None
    for dish in all_dishes:
        if dish.get("dish_name_zh") == dish_identifier or dish.get("dish_id") == dish_identifier:
            target_dish = dish
            break

    if not target_dish:
        print(f"Dish '{dish_identifier}' not found in the menu.")
        return

    dish_details = target_dish.get("dish_details", {})
    dish_name_zh = target_dish.get("dish_name_zh", "Unknown Dish")
    dish_id = target_dish.get("dish_id")
    category_id = target_dish.get("category_id")
    price = dish_details.get("price", 0.0)

    if not all([dish_id, category_id, price is not None]):
        print(f"Skipping dish {dish_name_zh} due to missing critical info.")
        return

    tax_rate = 0.05
    tax = round(price * tax_rate, 2)
    final_payment = round(price + tax, 2)

    # Use provided schedule time or calculate a default
    if schedule_time_str:
        try:
            schedule_time_formatted = datetime.strptime(schedule_time_str, "%Y/%m/%d %H:%M").strftime("%Y/%m/%d %H:%M")
        except ValueError:
            print(f"Error: Invalid schedule time format '{schedule_time_str}'. Please use the format 'YYYY/MM/DD HH:MM'.")
            return
    else:
        # Default: 15 minutes from now to ensure enough prep time
        schedule_time_formatted = "2025/06/27 18:00"

    payload = ApiOrderDto(
        portal_id=portal_id,
        customer_phone="6262928507",
        user_name="6262928507",
        schedule_time=schedule_time_formatted,
        item_subtotal=price,
        tax=tax,
        tips=0.0,
        bag_fee=0.0,
        final_payment=final_payment,
        lang_code=LangCode.ZH,
        order_items=[
            ApiOrderItem(
                name=TextStore(en=target_dish.get("dish_name_en"), zh=dish_name_zh),
                category_id=str(category_id),
                product_id=str(dish_id),
                quantity=1,
                final_price=price,
                product_options=None
            )
        ]
    )

    try:
        # Log the request details
        api_url = "https://sandbox-api-39milespos.azurewebsites.net/Order/Add"
        print(f"--- Posting to: {api_url} ---")
        # Pretty print the JSON payload
        print(f"--- Payload ---")
        print(json.dumps(payload.model_dump(by_alias=True), indent=2, ensure_ascii=False))
        print("---------------")
        print("--- Sending Request ---")

        response = await add_thirty_nine_miles_order(payload)
        print(f"API Response for {dish_name_zh}: {json.dumps(response, ensure_ascii=False)}")
        if response.get("success"):
            print(f"--> SUCCESS: Ordered {dish_name_zh}")
        else:
            print(f"--> FAILED: {dish_name_zh} - {response.get('message')}")
    except Exception as e:
        print(f"--> ERROR ordering {dish_name_zh}: {e}")


async def test_all_dishes():
    """
    Fetches all dishes from the menu, attempts to place an order for each one,
    and logs the results to a file.
    """
    portal_id = "06850184"
    results_file = "test_results.log"
    
    with open(results_file, "w", encoding="utf-8") as f:
        def log_and_print(message):
            print(message)
            f.write(message + "\n")

        log_and_print(f"Test started at: {datetime.now().isoformat()}")
        log_and_print(f"Fetching all dishes for portal: {portal_id}")
        
        menu_response = await get_extracted_dishes(portal_id)
        if not menu_response.get("success"):
            log_and_print("Failed to retrieve the menu. Aborting test.")
            return

        all_dishes = menu_response.get("data", [])
        if not all_dishes:
            log_and_print("No dishes found in the menu. Aborting test.")
            return

        log_and_print(f"Found {len(all_dishes)} dishes. Starting to test each one...")

        success_count = 0
        failed_count = 0

        for i, dish in enumerate(all_dishes):
            dish_details = dish.get("dish_details", {})
            dish_name_zh = dish.get("dish_name_zh", "Unknown Dish")
            dish_id = dish.get("dish_id")
            category_id = dish.get("category_id")
            price = dish_details.get("price", 0.0)

            if not all([dish_id, category_id, price is not None]):
                log_and_print(f"\n--- SKIPPING DISH ({i+1}/{len(all_dishes)}): {dish_name_zh} (Missing critical info: ID, category, or price) ---")
                continue

            log_and_print(f"\n--- TESTING DISH ({i+1}/{len(all_dishes)}): {dish_name_zh} (ID: {dish_id}) ---")

            tax_rate = 0.05
            tax = round(price * tax_rate, 2)
            final_payment = round(price + tax, 2)

            payload = ApiOrderDto(
                portal_id=portal_id,
                customer_phone="16262928507",
                user_name="16262928507",
                schedule_time="REALTIME",
                item_subtotal=price,
                tax=tax,
                tips=0.0,
                bag_fee=0.0,
                final_payment=final_payment,
                lang_code=LangCode.ZH,
                order_items=[
                    ApiOrderItem(
                        name=TextStore(en=dish.get("dish_name_en"), zh=dish_name_zh),
                        category_id=str(category_id),
                        product_id=str(dish_id),
                        quantity=1,
                        final_price=price,
                        product_options=None
                    )
                ]
            )
            
            try:
                # Log the request details
                api_url = "https://sandbox-api-39milespos.azurewebsites.net/Order/Add"
                log_and_print(f"--- Posting to: {api_url} ---")
                # Pretty print the JSON payload
                log_and_print(f"--- Payload ---")
                log_and_print(json.dumps(payload.model_dump(by_alias=True), indent=2, ensure_ascii=False))
                log_and_print("---------------")

                response = await add_thirty_nine_miles_order(payload)
                log_and_print(f"API Response for {dish_name_zh}: {json.dumps(response, ensure_ascii=False)}")
                
                # The actual success check from the API response itself
                if response.get("success"):
                    log_and_print(f"--> SUCCESS: Ordered {dish_name_zh}")
                    success_count += 1
                else:
                    log_and_print(f"--> FAILED: {dish_name_zh} - {response.get('message')}")
                    failed_count += 1

            except Exception as e:
                log_and_print(f"--> ERROR ordering {dish_name_zh}: {e}")
                failed_count += 1
            
            await asyncio.sleep(0.5) # Shorter delay

        log_and_print("\n--- TEST SUMMARY ---")
        log_and_print(f"Total dishes tested: {len(all_dishes)}")
        log_and_print(f"Successful orders: {success_count}")
        log_and_print(f"Failed orders: {failed_count}")
        log_and_print(f"Test finished at: {datetime.now().isoformat()}")
        log_and_print(f"Results saved to {results_file}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == '--all':
            print("Running full test suite...")
            asyncio.run(test_all_dishes())
        else:
            dish_to_test = sys.argv[1]
            # Check for an optional schedule time argument
            schedule_time_arg = sys.argv[2] if len(sys.argv) > 2 else None
            asyncio.run(test_single_dish(dish_to_test, schedule_time_arg))
    else:
        print("Usage:")
        print("  To test a single dish: python app/test_order_script.py \"<dish_name_or_id>\" [\"YYYY/MM/DD HH:MM\"]")
        print("  To test all dishes:    python app/test_order_script.py --all")
