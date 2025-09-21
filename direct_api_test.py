import asyncio
import json
from app.utils.thirty_nine_miles import add_order, ApiOrderDto, ApiOrderItem, MenuProductOptionGroup, MenuProductOption, TextStore, LangCode

async def main():
    """
    A simple, direct test to confirm if a correctly formatted payload
    can be sent to the 39-Miles API without a 500 error.
    """
    print("--- Starting Direct API Payload Test ---")

    # Manually construct the exact payload that we believe is correct,
    # including all required fields as defined in the Pydantic models.
    
    # This represents the selected "Hot" option.
    selected_option = MenuProductOption(
        name=TextStore(en=None, zh="Hot"),
        adjustPrice=0.0,
        linkedProductId=None,
        isAllowAdjustCount=False,
        isOutOfStock=False,
        choosedCount=None,
        weight=None
    )

    # This represents the "PICK YOUR FLAVOR" group containing the "Hot" option.
    option_group = MenuProductOptionGroup(
        name=TextStore(en=None, zh="PICK YOUR FLAVOR"),
        isRequired=True,
        isAllowMultiple=False,
        description=None,
        options=[selected_option],
        MinCount=None,
        MaxCount=None
    )

    # This represents the "French Fries" item itself.
    order_item = ApiOrderItem(
        name=TextStore(en=None, zh="French Fries"),
        categoryId="D44B34E6-AF8E-4D2F-9139-F82A1BE97D73", # From real_menu.json
        productId="11EC3CDB-EAC1-4D51-B414-B3CF4231D69F", # From real_menu.json
        quantity=1,
        weight=None,
        finalPrice=5.99, # Base price from menu
        specialRequests=[],
        productOptions=[option_group]
    )

    # This is the final order payload.
    order_payload = ApiOrderDto(
        portalId="09530785",
        customerPhone="5551234567", # Using a test phone number
        userName="Direct API Test",
        langCode=LangCode.ZH,
        orderItems=[order_item],
        itemSubtotal=5.99,
        tax=0.48, # Example tax
        tips=0.0,
        bagFee=0.0,
        finalPayment=6.47 # Subtotal + tax
    )

    print("\n--- Attempting to send the following payload to the API: ---")
    print(order_payload.model_dump_json(indent=2))
    
    # Call the API directly
    response = await add_order(order_payload)

    print("\n--- API Response ---")
    print(json.dumps(response, indent=2))
    print("--- Test Finished ---")

if __name__ == "__main__":
    # Since this is a standalone script, we need to load environment variables
    # to get the API credentials from the .env file.
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
