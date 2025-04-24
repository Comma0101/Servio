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


# Models for call recording and transcription storage
class Utterance(BaseModel):
    """An individual speech segment with its transcription"""
    timestamp: str
    speaker: str  # 'customer' or 'system'
    transcript: str
    confidence: Optional[float] = None


class CallRecording(BaseModel):
    """Model for storing call recording metadata"""
    call_sid: str
    caller_number: Optional[str] = None
    start_time: str
    end_time: Optional[str] = None
    audio_url: Optional[str] = None  # S3 URL to the audio file
    utterances: List[Utterance] = []
    call_summary: Optional[str] = None
    status: str = "in_progress"  # 'in_progress', 'completed', 'abandoned', etc.
    order_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class TranscriptionSegment(BaseModel):
    """Real-time transcription segment received from Deepgram"""
    channel: int
    transcript: str
    confidence: float
    words: List[Dict[str, Any]] = []
    start_time: float
    end_time: float
