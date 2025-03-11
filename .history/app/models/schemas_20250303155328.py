from pydantic import BaseModel, Field
from typing import List, Optional, Union, Dict, Any


class OrderItem(BaseModel):
    name: str
    quantity: int
    variation: Optional[str] = None


class OrderSummary(BaseModel):
    items: List[OrderItem]
    total_price: float
    summary: str = Field(
        ..., description="Order status", pattern="^(IN PROGRESS|DONE)$"
    )


class CreateOrderRequest(BaseModel):
    items: List[Dict[str, Any]]


class PaymentRequest(BaseModel):
    order_id: str
    amount: int
    payment_method_id: str
