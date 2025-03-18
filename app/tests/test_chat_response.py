"""
Test script for chat_response functions without making real API calls
"""
import sys
import os
import asyncio
import json
from pathlib import Path

# Add parent directory to the Python path
sys.path.append(str(Path(__file__).parent.parent))


from routers.chat_response import execute_function_call, post_chat_response

from tests.mock_openai import (
    get_mock_order_summary_response,
    get_mock_order_in_progress_response,
    get_mock_text_response,
    create_mock_tool_call_message,
    create_mock_function_call_message
)

# Mock dependencies
class MockRequest:
    def __init__(self, session=None):
        self.session = session or {}
        self.cookies = {}

    def url_for(self, *args, **kwargs):
        return "http://mock-url.com"

async def test_execute_function_call():
    """Test the execute_function_call function with different argument formats"""
    print("\n--- Testing execute_function_call ---")
    
    # Test with string JSON arguments
    arguments_json = json.dumps({
        "items": [{"name": "Burger", "quantity": 2, "variation": "Regular"}],
        "total_price": 32.02,
        "summary": "DONE"
    })
    
    result = await execute_function_call("order_summary", arguments_json)
    print(f"Test 1 (string JSON): {result}")
    
    # Test with dictionary arguments
    arguments_dict = {
        "items": [{"name": "Burger", "quantity": 2, "variation": "Regular"}],
        "total_price": 32.02,
        "summary": "DONE"
    }
    
    result = await execute_function_call("order_summary", arguments_dict)
    print(f"Test 2 (dict arguments): {result}")
    
    # Test with invalid arguments
    result = await execute_function_call("order_summary", "invalid json")
    print(f"Test 3 (invalid arguments): {result}")

async def test_handle_tool_calls():
    """Test handling of tool_calls format"""
    print("\n--- Testing tool_calls format ---")
    
    # Create a mock tool call response
    mock_response = get_mock_order_summary_response()
    
    print(f"Has tool_calls: {hasattr(mock_response, 'tool_calls')}")
    if hasattr(mock_response, 'tool_calls') and mock_response.tool_calls:
        tool_call = mock_response.tool_calls[0]
        print(f"Tool call type: {tool_call.type}")
        print(f"Function name: {tool_call.function.name}")
        print(f"Function arguments: {tool_call.function.arguments}")
        
        # Test executing the function with these arguments
        result = await execute_function_call(
            tool_call.function.name, 
            tool_call.function.arguments
        )
        print(f"Function result: {result}")

async def test_handle_function_calls():
    """Test handling of function_call format (old API)"""
    print("\n--- Testing function_call format ---")
    
    # Create a mock function call response (old API format)
    arguments = json.dumps({
        "items": [{"name": "Burger", "quantity": 2, "variation": "Regular"}],
        "total_price": 32.02,
        "summary": "DONE"
    })
    mock_response = create_mock_function_call_message("order_summary", arguments)
    
    print(f"Has function_call: {hasattr(mock_response, 'function_call')}")
    if hasattr(mock_response, 'function_call') and mock_response.function_call:
        print(f"Function name: {mock_response.function_call.name}")
        print(f"Function arguments: {mock_response.function_call.arguments}")
        
        # Test executing the function with these arguments
        result = await execute_function_call(
            mock_response.function_call.name, 
            mock_response.function_call.arguments
        )
        print(f"Function result: {result}")

async def run_tests():
    """Run all tests"""
    await test_execute_function_call()
    await test_handle_tool_calls()
    await test_handle_function_calls()

if __name__ == "__main__":
    asyncio.run(run_tests())