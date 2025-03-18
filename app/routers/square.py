from fastapi import APIRouter, Request, Response, Body
from fastapi.responses import JSONResponse
from typing import List, Dict, Any
import traceback

from app.utils.square import (
    process_square_payment,
    create_square_order,
    get_square_location_id,
)

router = APIRouter()


@router.post("/create-order", response_class=JSONResponse)
async def create_order(request: Request, data: Dict[str, Any] = Body(...)):
    try:
        items = data.get("items", [])
        if not items:
            return JSONResponse({"error": "No items provided"}, status_code=400)

        location_id = await get_square_location_id()
        if not location_id:
            return JSONResponse({"error": "Failed to get location ID"}, status_code=500)

        result = await create_square_order(items, location_id)
        if not result:
            return JSONResponse({"error": "Failed to create order"}, status_code=500)

        return JSONResponse({"order": result.get("order", {})})

    except Exception as error:
        print("[POST:/create-order]", error)
        traceback.print_exc()
        return JSONResponse({"error": "Internal Error"}, status_code=500)


@router.post("/process-payment", response_class=JSONResponse)
async def process_payment(request: Request, data: Dict[str, Any] = Body(...)):
    try:
        order_id = data.get("order_id")
        amount = data.get("amount")
        payment_method_id = data.get("payment_method_id")

        if not all([order_id, amount, payment_method_id]):
            return JSONResponse({"error": "Missing required fields"}, status_code=400)

        result = await process_square_payment(order_id, amount, payment_method_id)
        if "error" in result:
            return JSONResponse(result, status_code=500)

        return JSONResponse({"payment": result.get("payment", {})})

    except Exception as error:
        print("[POST:/process-payment]", error)
        traceback.print_exc()
        return JSONResponse({"error": "Internal Error"}, status_code=500)


#add get test endpoint
@router.get("/test", response_class=JSONResponse)
async def test(request: Request):
    return JSONResponse({"message": "Square API test endpoint"})