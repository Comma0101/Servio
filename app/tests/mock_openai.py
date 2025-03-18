"""
Mock OpenAI responses for testing purposes
"""
from typing import List, Dict, Any, Optional
import json

class MockMessage:
    def __init__(self, content=None, tool_calls=None, function_call=None):
        self.content = content
        self.tool_calls = tool_calls
        self.function_call = function_call

class MockToolFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

class MockToolCall:
    def __init__(self, id, type, function):
        self.id = id
        self.type = type
        self.function = function

class MockFunctionCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

def create_mock_tool_call_message(function_name, arguments):
    """Create a message with tool_calls format (new API)"""
    tool_function = MockToolFunction(function_name, arguments)
    tool_call = MockToolCall("call_123", "function", tool_function)
    return MockMessage(content=None, tool_calls=[tool_call])

def create_mock_function_call_message(function_name, arguments):
    """Create a message with function_call format (old API)"""
    function_call = MockFunctionCall(function_name, arguments)
    return MockMessage(content=None, function_call=function_call)

def create_mock_text_message(content):
    """Create a message with text content only"""
    return MockMessage(content=content)

# Sample mock responses
def get_mock_order_summary_response():
    return create_mock_tool_call_message(
        "order_summary",
        json.dumps({
            "items": [{"name": "Burger", "quantity": 2, "variation": "Regular"}],
            "total_price": 32.02,
            "summary": "DONE"
        })
    )

def get_mock_order_in_progress_response():
    return create_mock_tool_call_message(
        "order_summary",
        json.dumps({
            "items": [{"name": "Burger", "quantity": 1, "variation": "Regular"}],
            "total_price": 13.99,
            "summary": "IN_PROGRESS"
        })
    )

def get_mock_text_response():
    return create_mock_text_message(
        "Here's a summary of your order so far:\n\n- 2 Regular Burgers\n\nI will now calculate the total price including tax."
    )

# You can add more mock responses as needed for different test scenarios
