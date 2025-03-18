"""
Standalone test for function call handling without dependencies
"""
import json
import asyncio
from typing import Dict, Any, List, Optional

# Mock version of the execute_function_call function
async def execute_function_call(function_name, arguments_json):
    """Mock version of execute_function_call for testing"""
    test_payment_method_id = "cnon:card-nonce-ok"
    try:
        # If arguments_json is already a dict, use it directly, otherwise parse it
        if isinstance(arguments_json, dict):
            arguments = arguments_json
        else:
            arguments = json.loads(arguments_json)
        print("Arguments:", arguments)
    except (json.JSONDecodeError, TypeError):
        print("Failed to parse function arguments")
        # Raise an exception to indicate parsing failure
        # This will change how we handle this in our process_ai_response function
        raise ValueError("Invalid function arguments")

    if function_name == "order_summary":
        # Process the order summary
        summary_status = arguments.get("summary")
        if summary_status == "DONE":
            # Order is complete
            return {
                "message": "Thank you for your order! Your payment has been processed.",
                "order_complete": True,
            }
        else:
            # Order is in progress
            return {
                "message": "Your order is in progress. What else would you like to add?",
                "order_complete": False,
            }
    else:
        return {
            "message": f"Function {function_name} not found.",
            "order_complete": False,
        }

# Mock classes for OpenAI responses
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

# Functions to create mock responses
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

# Mock function to process a response
async def process_ai_response(ai_response):
    """Process an AI response and handle function/tool calls"""
    if hasattr(ai_response, "tool_calls") and ai_response.tool_calls:
        print("\n=== Processing tool_calls response ===")
        # Handle the new tool_calls format from OpenAI
        tool_call = ai_response.tool_calls[0]  # Get the first tool call
        if tool_call.type == "function":  # Verify it's a function call
            function_name = tool_call.function.name
            arguments = tool_call.function.arguments
            print(f"Tool call detected - Function: {function_name}, Arguments: {arguments}")
            
            try:
                # Execute the function call
                function_response = await execute_function_call(function_name, arguments)
                print(f"Function response: {function_response}")
                
                # Add the function's response to the chat history (simulated)
                print("Adding function response to chat history")
                
                # Check if order is complete
                if function_response.get("order_complete"):
                    print("Order complete! Hanging up call...")
                else:
                    print("Order in progress, continuing conversation...")
                    
                return True
            except ValueError as e:
                # Handle the case where arguments couldn't be parsed
                print(f"ERROR: {str(e)}")
                print("Falling back to text response due to invalid function arguments")
                return False
    
    elif hasattr(ai_response, "function_call") and ai_response.function_call:
        print("\n=== Processing function_call response (old API) ===")
        # For backward compatibility with the older API format
        function_call = ai_response.function_call
        function_name = function_call.name
        arguments = function_call.arguments if hasattr(function_call, "arguments") else None
        
        print(f"Function call detected - Name: {function_name}, Arguments: {arguments}")
        
        try:
            # Execute the function call
            function_response = await execute_function_call(function_name, arguments)
            print(f"Function response: {function_response}")
            
            # Add the function's response to the chat history (simulated)
            print("Adding function response to chat history")
            
            # Check if order is complete
            if function_response.get("order_complete"):
                print("Order complete! Hanging up call...")
            else:
                print("Order in progress, continuing conversation...")
                
            return True
        except ValueError as e:
            # Handle the case where arguments couldn't be parsed
            print(f"ERROR: {str(e)}")
            print("Falling back to text response due to invalid function arguments")
            return False
    
    else:
        print("\n=== Processing text-only response ===")
        print(f"Text content: {ai_response.content}")
        return False

async def run_tests():
    """Run all tests"""
    print("\n----- TEST 1: New API format (tool_calls) -----")
    # Create a mock response with tool_calls (new API format)
    arguments = json.dumps({
        "items": [{"name": "Burger", "quantity": 2, "variation": "Regular"}],
        "total_price": 32.02,
        "summary": "DONE"
    })
    mock_response = create_mock_tool_call_message("order_summary", arguments)
    
    # Process the response
    called = await process_ai_response(mock_response)
    print(f"Function was called: {called}")
    
    print("\n----- TEST 2: Old API format (function_call) -----")
    # Create a mock response with function_call (old API format)
    arguments = json.dumps({
        "items": [{"name": "Burger", "quantity": 1, "variation": "Regular"}],
        "total_price": 13.99,
        "summary": "IN_PROGRESS"
    })
    mock_response = create_mock_function_call_message("order_summary", arguments)
    
    # Process the response
    called = await process_ai_response(mock_response)
    print(f"Function was called: {called}")
    
    print("\n----- TEST 3: Text-only response -----")
    # Create a mock text-only response
    mock_response = create_mock_text_message(
        "Here's a summary of your order so far:\n\n- 2 Regular Burgers"
    )
    
    # Process the response
    called = await process_ai_response(mock_response)
    print(f"Function was called: {called}")
    
    print("\n----- TEST 4: Dictionary arguments -----")
    # Test with dictionary arguments directly
    arguments_dict = {
        "items": [{"name": "Burger", "quantity": 2, "variation": "Regular"}],
        "total_price": 32.02,
        "summary": "DONE"
    }
    mock_response = create_mock_tool_call_message("order_summary", arguments_dict)
    
    # Process the response
    called = await process_ai_response(mock_response)
    print(f"Function was called: {called}")
    
    print("\n----- TEST 5: Invalid arguments -----")
    # Test with invalid arguments
    mock_response = create_mock_tool_call_message("order_summary", "invalid json")
    
    # Process the response
    called = await process_ai_response(mock_response)
    print(f"Function was called: {called}")

if __name__ == "__main__":
    asyncio.run(run_tests())
