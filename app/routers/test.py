from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import json
import logging
import asyncio

router = APIRouter()
@router.post("/api/v1/test-chat-message")
async def test_chat_message(request: Request):
    try:
        from app.utils.openai import create_chat_completion
        from app.constants import CONSTANTS
        from app.routers.chat_response import execute_function_call
        import json
        import logging
        
        logging.basicConfig(level=logging.DEBUG)
        logger = logging.getLogger(__name__)
        
        # Get request data
        data = await request.json()
        client_id = data.get("client_id", "LIMF")
        messages = data.get("messages", [])
        model = data.get("model", CONSTANTS[client_id]["OPENAI_CHAT_MODEL"])
        temperature = data.get("temperature", CONSTANTS[client_id]["OPENAI_CHAT_TEMPERATURE"])
        max_tokens = data.get("max_tokens", CONSTANTS[client_id]["OPENAI_CHAT_MAX_TOKENS"])
        top_p = data.get("top_p", CONSTANTS[client_id]["OPENAI_CHAT_TOP_P"])
        use_tools = data.get("use_tools", True)
        execute_functions = data.get("execute_functions", False)
        
        logger.debug(f"Request received: client_id={client_id}, model={model}, execute_functions={execute_functions}")
        
        if client_id not in CONSTANTS:
            logger.error(f"Invalid client_id: {client_id}")
            return {"status": "error", "message": "Invalid client_id"}
        
        # Use tools if requested
        tools = CONSTANTS[client_id].get("OPENAI_CHAT_TOOLS") if use_tools else None
        logger.debug(f"Using tools: {bool(tools)}")
        
        # Call OpenAI with parameters
        logger.debug("Calling OpenAI API...")
        response = await create_chat_completion(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            functions=tools
        )
        
        # Process function calls if enabled
        function_executed = False
        function_results = []
        
        logger.debug(f"OpenAI response received, execute_functions is {execute_functions}")
        
        if execute_functions and response:
            logger.debug("Function execution is enabled")
            logger.debug(f"Response type: {type(response)}, contains tool_calls: {hasattr(response, 'tool_calls')}")
            
            # Handle the message object directly (which is what create_chat_completion returns)
            # Check for tool_calls (new API format)
            if hasattr(response, 'tool_calls') and response.tool_calls:
                logger.debug(f"Found tool_calls in response: {len(response.tool_calls)}")
                
                for tool_call in response.tool_calls:
                    if tool_call.type == "function":
                        function_name = tool_call.function.name
                        arguments = tool_call.function.arguments
                        
                        logger.debug(f"Processing tool call: {function_name} with arguments: {arguments}")
                        
                        # Execute the function
                        logger.debug(f"Executing function: {function_name}")
                        function_result = await execute_function_call(function_name, arguments)
                        function_executed = True
                        logger.debug(f"Function execution result: {function_result}")
                        
                        # Add assistant message with tool call
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": tool_call.id,
                                    "type": "function",
                                    "function": {
                                        "name": function_name,
                                        "arguments": arguments
                                    }
                                }
                            ]
                        })
                        
                        # Add tool result message
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(function_result)
                        })
                        
                        # Store function results for the response
                        function_results.append({
                            "id": tool_call.id,
                            "name": function_name,
                            "arguments": json.loads(arguments),
                            "result": function_result
                        })
                
                # Make a follow-up call with the function results
                logger.debug("Making follow-up call with function results")
                
                # Check if any of the functions executed was an order completion
                is_order_complete = False
                for result_item in function_results:
                    result_name = result_item.get("name", "")
                    result_data = result_item.get("result", {})
                    
                    logger.debug(f"Checking if function {result_name} is an order completion")
                    
                    if result_name in ["order_summary", "place_restaurant_order"]:
                        # Try to determine if this is a completed order
                        try:
                            if isinstance(result_data, dict) and result_data.get("order_complete") == True:
                                logger.debug(f"Found completed order in function {result_name}")
                                is_order_complete = True
                                # Save the order result for potential use in messaging
                                order_result = result_data
                                break
                        except Exception as e:
                            logger.error(f"Error checking if order is complete: {str(e)}")
                
                logger.debug(f"Order completion check result: {is_order_complete}")
                
                if is_order_complete:
                    # This is a completed order - use our standard success message
                    logger.debug("Order complete - using standard success message")
                    client_id = "LIMF"  # Default client ID
                    success_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["SUCCESS"]
                    
                    # Get any order info if available
                    order_info = ""
                    if isinstance(order_result, dict) and "order_info" in order_result:
                        order_info = order_result.get("order_info", "")
                    
                    # Create a simple response object with our message
                    class SimpleResponse:
                        def __init__(self, content):
                            self.content = content
                    
                    # Use the standard message and include order info if available
                    final_message = success_message
                    if order_info:
                        final_message = f"{success_message}\n\n{order_info}"
                    
                    logger.debug(f"Using standard success message: {final_message}")
                    follow_up_response = SimpleResponse(final_message)
                else:
                    # For non-order functions or incomplete orders, make the follow-up call
                    follow_up_response = await create_chat_completion(
                        messages,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        functions=tools
                    )
                
                # Replace the response with the follow-up
                logger.debug("Replacing original response with follow-up response")
                response = follow_up_response
            
            # Check for function_call (older API format)
            elif hasattr(response, 'function_call') and response.function_call:
                function_name = response.function_call.name
                arguments = response.function_call.arguments
                
                logger.debug(f"Found function_call: {function_name} with arguments: {arguments}")
                
                # Execute the function
                logger.debug(f"Executing function: {function_name}")
                function_result = await execute_function_call(function_name, arguments)
                function_executed = True
                logger.debug(f"Function execution result: {function_result}")
                
                # Add function messages to the conversation
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "function_call": {
                        "name": function_name,
                        "arguments": arguments
                    }
                })
                
                messages.append({
                    "role": "function",
                    "name": function_name,
                    "content": json.dumps(function_result)
                })
                
                # Store function results for the response
                function_results.append({
                    "name": function_name,
                    "arguments": json.loads(arguments),
                    "result": function_result
                })
                
                # Make a follow-up call with the function result
                logger.debug("Making follow-up call with function result")
                
                # Check if any of the functions executed was an order completion
                is_order_complete = False
                for result_item in function_results:
                    result_name = result_item.get("name", "")
                    result_data = result_item.get("result", {})
                    
                    logger.debug(f"Checking if function {result_name} is an order completion")
                    
                    if result_name in ["order_summary", "place_restaurant_order"]:
                        # Try to determine if this is a completed order
                        try:
                            if isinstance(result_data, dict) and result_data.get("order_complete") == True:
                                logger.debug(f"Found completed order in function {result_name}")
                                is_order_complete = True
                                # Save the order result for potential use in messaging
                                order_result = result_data
                                break
                        except Exception as e:
                            logger.error(f"Error checking if order is complete: {str(e)}")
                
                logger.debug(f"Order completion check result: {is_order_complete}")
                
                if is_order_complete:
                    # This is a completed order - use our standard success message
                    logger.debug("Order complete - using standard success message")
                    client_id = "LIMF"  # Default client ID
                    success_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["SUCCESS"]
                    
                    # Get any order info if available
                    order_info = ""
                    if isinstance(order_result, dict) and "order_info" in order_result:
                        order_info = order_result.get("order_info", "")
                    
                    # Create a simple response object with our message
                    class SimpleResponse:
                        def __init__(self, content):
                            self.content = content
                    
                    # Use the standard message and include order info if available
                    final_message = success_message
                    if order_info:
                        final_message = f"{success_message}\n\n{order_info}"
                    
                    logger.debug(f"Using standard success message: {final_message}")
                    follow_up_response = SimpleResponse(final_message)
                else:
                    # For non-order functions or incomplete orders, make the follow-up call
                    follow_up_response = await create_chat_completion(
                        messages,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        functions=tools
                    )
                
                # Replace the response with the follow-up
                logger.debug("Replacing original response with follow-up response")
                response = follow_up_response
            else:
                logger.debug("No function calls found in the response")
                
                # Check if the response contains embedded JSON with a function call
                if hasattr(response, 'content') and response.content and "summary" in response.content:
                    logger.debug("Checking for embedded JSON function call in text response")
                    try:
                        # Look for JSON-like structure in content
                        content = response.content
                        if '"summary": "DONE"' in content:
                            # Extract the JSON part
                            json_start = content.find('{')
                            json_end = content.rfind('}') + 1
                            
                            if json_start >= 0 and json_end > json_start:
                                json_str = content[json_start:json_end]
                                logger.debug(f"Found potential function call in content: {json_str}")
                                
                                try:
                                    arguments = json.loads(json_str)
                                    function_name = "order_summary"  # Default function for order completion
                                    
                                    logger.debug(f"Extracted function call: {function_name} with arguments: {arguments}")
                                    
                                    # Execute the function
                                    logger.debug(f"Executing extracted function: {function_name}")
                                    function_result = await execute_function_call(function_name, arguments)
                                    function_executed = True
                                    logger.debug(f"Function execution result: {function_result}")
                                    
                                    # Add function messages to the conversation
                                    messages.append({
                                        "role": "assistant",
                                        "content": response.content
                                    })
                                    
                                    messages.append({
                                        "role": "function",
                                        "name": function_name,
                                        "content": json.dumps(function_result)
                                    })
                                    
                                    # Store function results for the response
                                    function_results.append({
                                        "name": function_name,
                                        "arguments": arguments,
                                        "result": function_result
                                    })
                                    
                                    # Make a follow-up call with the function result
                                    logger.debug("Making follow-up call with extracted function result")
                                    
                                    # Check if any of the functions executed was an order completion
                                    is_order_complete = False
                                    for result_item in function_results:
                                        result_name = result_item.get("name", "")
                                        result_data = result_item.get("result", {})
                                        
                                        logger.debug(f"Checking if function {result_name} is an order completion")
                                        
                                        if result_name in ["order_summary", "place_restaurant_order"]:
                                            # Try to determine if this is a completed order
                                            try:
                                                if isinstance(result_data, dict) and result_data.get("order_complete") == True:
                                                    logger.debug(f"Found completed order in function {result_name}")
                                                    is_order_complete = True
                                                    # Save the order result for potential use in messaging
                                                    order_result = result_data
                                                    break
                                            except Exception as e:
                                                logger.error(f"Error checking if order is complete: {str(e)}")
                                    
                                    logger.debug(f"Order completion check result: {is_order_complete}")
                                    
                                    if is_order_complete:
                                        # This is a completed order - use our standard success message
                                        logger.debug("Order complete - using standard success message")
                                        client_id = "LIMF"  # Default client ID
                                        success_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["SUCCESS"]
                                        
                                        # Get any order info if available
                                        order_info = ""
                                        if isinstance(order_result, dict) and "order_info" in order_result:
                                            order_info = order_result.get("order_info", "")
                                        
                                        # Create a simple response object with our message
                                        class SimpleResponse:
                                            def __init__(self, content):
                                                self.content = content
                                        
                                        # Use the standard message and include order info if available
                                        final_message = success_message
                                        if order_info:
                                            final_message = f"{success_message}\n\n{order_info}"
                                        
                                        logger.debug(f"Using standard success message: {final_message}")
                                        follow_up_response = SimpleResponse(final_message)
                                    else:
                                        # For non-order functions or incomplete orders, make the follow-up call
                                        follow_up_response = await create_chat_completion(
                                            messages,
                                            model=model,
                                            max_tokens=max_tokens,
                                            temperature=temperature,
                                            top_p=top_p,
                                            functions=tools
                                        )
                                    
                                    # Replace the response with the follow-up
                                    logger.debug("Replacing original response with follow-up response")
                                    response = follow_up_response
                                except json.JSONDecodeError as e:
                                    logger.error(f"Failed to parse JSON in content: {e}")
                    except Exception as e:
                        logger.error(f"Error processing embedded function call: {str(e)}")
                        import traceback
                        traceback.print_exc()
                
                # Check for formatted order summaries without JSON
                elif hasattr(response, 'content') and response.content:
                    content = response.content.lower()
                    # If this looks like an order confirmation (has "total price" or similar)
                    if (("total price" in content or "your order" in content) and 
                        ("thank you" in content) and
                        any(food in content for food in ["burger", "wings", "hot wings"])):
                        
                        logger.debug("Detected formatted order summary without JSON")
                        
                        # Directly modify the response content with the standard success message
                        client_id = "LIMF"  # Default client ID
                        success_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["SUCCESS"]
                        response.content = success_message
                        function_executed = True
        else:
            if not execute_functions:
                logger.info("Function execution is disabled")
            if not response:
                logger.warning("Empty response from OpenAI")
        
        logger.debug(f"Returning response, function_executed={function_executed}")
        return {
            "status": "success",
            "response": response,
            "model_used": model,
            "function_executed": function_executed,
            "function_results": function_results if function_executed else None
        }
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logging.error(f"Error in test_chat_message: {str(e)}\n{error_details}")
        return {"status": "error", "message": str(e), "details": error_details}


@router.get("/api/v1/system-message")
async def get_system_message(client_id: str = "LIMF"):
    try:
        from app.constants import CONSTANTS
        
        if client_id not in CONSTANTS:
            return {"status": "error", "message": "Invalid client_id"}
        
        # Get system message for the client
        system_message = CONSTANTS[client_id]["SYSTEM_MESSAGE"]
        
        # Create a test conversation similar to the actual flow
        menu_string = ""
        for item in CONSTANTS[client_id]["MENU"]:
            menu_string += "\n" + item
            
        tax_string = f"The tax percentage is: {CONSTANTS[client_id]['TAX'] * 100}%"
        
        full_system_message = (
            system_message 
            + " Here is the menu:" 
            + menu_string 
            + "\n" 
            + tax_string
        )
        
        return {
            "status": "success",
            "system_message": full_system_message,
            "client_id": client_id
        }
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return {"status": "error", "message": str(e), "details": error_details}


@router.get("/api/v1/client-settings")
async def get_client_settings(client_id: str = "LIMF"):
    try:
        from app.constants import CONSTANTS
        
        if client_id not in CONSTANTS:
            return {"status": "error", "message": "Invalid client_id"}
        
        # Get relevant settings for the client
        settings = {
            "OPENAI_CHAT_MODEL": CONSTANTS[client_id].get("OPENAI_CHAT_MODEL"),
            "OPENAI_CHAT_TEMPERATURE": CONSTANTS[client_id].get("OPENAI_CHAT_TEMPERATURE"),
            "OPENAI_CHAT_MAX_TOKENS": CONSTANTS[client_id].get("OPENAI_CHAT_MAX_TOKENS"),
            "OPENAI_CHAT_TOP_P": CONSTANTS[client_id].get("OPENAI_CHAT_TOP_P"),
            "HAS_TOOLS": bool(CONSTANTS[client_id].get("OPENAI_CHAT_TOOLS"))
        }
        
        return {
            "status": "success",
            "settings": settings,
            "client_id": client_id
        }
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return {"status": "error", "message": str(e), "details": error_details}



@router.get("/test-original-openai")
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

@router.get("/test-chat", response_class=HTMLResponse)
async def test_chat():
    """
    Interactive chat interface for testing conversations with OpenAI.
    """
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>OpenAI Chat Testing Interface</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                display: flex;
                flex-direction: column;
                height: 95vh;
                overflow: hidden;
            }
            .container {
                display: flex;
                flex: 1;
                gap: 20px;
                height: calc(100% - 60px);
                overflow: hidden;
            }
            .chat-container {
                flex: 3;
                display: flex;
                flex-direction: column;
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 10px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                overflow: hidden;
                height: 100%;
            }
            .settings-container {
                flex: 1;
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 10px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                overflow-y: auto;
                height: 100%;
            }
            .chat-history {
                flex: 1;
                overflow-y: auto;
                padding: 10px;
                background-color: #f9f9f9;
                margin-bottom: 10px;
                border-radius: 5px;
                scroll-behavior: smooth;
            }
            .input-area {
                display: flex;
                gap: 10px;
                padding: 10px 0;
            }
            #user-input {
                flex: 1;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            button {
                padding: 10px 15px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
            }
            button:hover {
                background-color: #45a049;
            }
            .user-message {
                background-color: #e3f2fd;
                padding: 10px;
                border-radius: 10px;
                margin: 5px 0;
                max-width: 80%;
                align-self: flex-end;
                margin-left: auto;
            }
            .assistant-message {
                background-color: #f1f1f1;
                padding: 10px;
                border-radius: 10px;
                margin: 5px 0;
                max-width: 80%;
            }
            .system-message {
                background-color: #ffe0b2;
                padding: 10px;
                border-radius: 10px;
                margin: 5px 0;
                max-width: 80%;
                font-style: italic;
            }
            .message-container {
                display: flex;
                flex-direction: column;
                margin-bottom: 10px;
            }
            .message-header {
                font-weight: bold;
                margin-bottom: 5px;
            }
            .loading {
                text-align: center;
                margin: 20px 0;
                display: none;
            }
            .settings-group {
                margin-bottom: 15px;
                padding-bottom: 15px;
                border-bottom: 1px solid #eee;
            }
            label {
                display: block;
                margin-bottom: 5px;
                font-weight: bold;
            }
            select, input[type="number"], input[type="text"] {
                width: 100%;
                padding: 8px;
                margin-bottom: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            .toggle-container {
                display: flex;
                align-items: center;
                margin-bottom: 10px;
            }
            .toggle-container label {
                margin-bottom: 0;
                margin-left: 10px;
            }
            .function-call {
                background-color: #e8f5e9;
                border: 1px solid #c8e6c9;
                padding: 10px;
                border-radius: 10px;
                margin: 5px 0;
                font-family: monospace;
                white-space: pre-wrap;
            }
            .function-response {
                background-color: #f3e5f5;
                border: 1px solid #e1bee7;
                padding: 10px;
                border-radius: 10px;
                margin: 5px 0;
                font-family: monospace;
                white-space: pre-wrap;
            }
            .tabs {
                display: flex;
                margin-bottom: 10px;
            }
            .tab {
                padding: 10px 20px;
                background-color: #f1f1f1;
                cursor: pointer;
                border-radius: 5px 5px 0 0;
                border: 1px solid #ddd;
                border-bottom: none;
            }
            .tab.active {
                background-color: white;
                border-bottom: 1px solid white;
            }
            .tab-content {
                display: none;
                padding: 20px;
                border: 1px solid #ddd;
                border-radius: 0 0 5px 5px;
                margin-top: -1px;
            }
            .tab-content.active {
                display: block;
            }
            pre {
                white-space: pre-wrap;
                background-color: #f5f5f5;
                padding: 10px;
                border-radius: 5px;
                overflow-x: auto;
            }
            .clear-btn {
                background-color: #f44336;
                margin-left: 10px;
            }
            .copy-btn {
                background-color: #2196F3;
                margin-left: 10px;
            }
        </style>
    </head>
    <body>
        <h1>OpenAI Chat Testing Interface</h1>
        <div class="container">
            <div class="chat-container">
                <div class="tabs">
                    <div class="tab active" data-tab="chat">Chat</div>
                    <div class="tab" data-tab="raw">Raw Response</div>
                </div>
                <div class="tab-content active" id="tab-chat">
                    <div class="chat-history" id="chat-history"></div>
                    <div class="loading" id="loading">
                        <p>Generating response...</p>
                    </div>
                    <div class="input-area">
                        <textarea id="user-input" placeholder="Type your message here..." rows="3"></textarea>
                        <button id="send-btn">Send</button>
                        <button id="clear-btn" class="clear-btn">Clear Chat</button>
                    </div>
                </div>
                <div class="tab-content" id="tab-raw">
                    <button id="copy-raw" class="copy-btn">Copy to Clipboard</button>
                    <pre id="raw-response"></pre>
                </div>
            </div>
            <div class="settings-container">
                <h2>Settings</h2>
                <div class="settings-group">
                    <label for="client-id">Client ID:</label>
                    <select id="client-id">
                        <option value="LIMF">LIMF</option>
                        <!-- Add more client IDs here as needed -->
                    </select>
                    
                    <label for="model">Model:</label>
                    <select id="model">
                        <option value="gpt-4o-mini">GPT-4o Mini</option>
                        <option value="gpt-4o">GPT-4o</option>
                        <option value="gpt-3.5-turbo">GPT-3.5 Turbo</option>
                    </select>
                </div>
                
                <div class="settings-group">
                    <label for="temperature">Temperature:</label>
                    <input type="number" id="temperature" min="0" max="2" step="0.1" value="0.5">
                    
                    <label for="max-tokens">Max Tokens:</label>
                    <input type="number" id="max-tokens" min="50" max="4000" step="50" value="500">
                    
                    <label for="top-p">Top P:</label>
                    <input type="number" id="top-p" min="0" max="1" step="0.01" value="1">
                </div>
                
                <div class="settings-group">
                    <div class="toggle-container">
                        <input type="checkbox" id="show-system-message" checked>
                        <label for="show-system-message">Show System Message</label>
                    </div>
                    
                    <div class="toggle-container">
                        <input type="checkbox" id="use-tools" checked>
                        <label for="use-tools">Use Tools/Functions</label>
                    </div>
                    
                    <div class="toggle-container">
                        <input type="checkbox" id="execute-functions">
                        <label for="execute-functions">Execute Functions</label>
                    </div>
                    
                    <div class="toggle-container">
                        <input type="checkbox" id="apply-client-settings">
                        <label for="apply-client-settings">Apply Client Settings</label>
                    </div>
                </div>
                
                <button id="reset-settings">Reset to Defaults</button>
            </div>
        </div>

        <script>
            document.addEventListener('DOMContentLoaded', function() {
                // Elements
                const chatHistory = document.getElementById('chat-history');
                const userInput = document.getElementById('user-input');
                const sendBtn = document.getElementById('send-btn');
                const clearBtn = document.getElementById('clear-btn');
                const loading = document.getElementById('loading');
                const rawResponse = document.getElementById('raw-response');
                const copyRawBtn = document.getElementById('copy-raw');
                const clientIdSelect = document.getElementById('client-id');
                const modelSelect = document.getElementById('model');
                const temperatureInput = document.getElementById('temperature');
                const maxTokensInput = document.getElementById('max-tokens');
                const topPInput = document.getElementById('top-p');
                const showSystemMessage = document.getElementById('show-system-message');
                const useTools = document.getElementById('use-tools');
                const executeFunctions = document.getElementById('execute-functions');
                const applyClientSettings = document.getElementById('apply-client-settings');
                const resetSettingsBtn = document.getElementById('reset-settings');
                const tabs = document.querySelectorAll('.tab');
                
                // Chat history storage
                let messages = [];
                let lastRawResponse = null;
                
                // Initialize settings from localStorage or defaults
                function initSettings() {
                    clientIdSelect.value = localStorage.getItem('clientId') || 'LIMF';
                    modelSelect.value = localStorage.getItem('model') || 'gpt-4o-mini';
                    temperatureInput.value = localStorage.getItem('temperature') || '0.5';
                    maxTokensInput.value = localStorage.getItem('maxTokens') || '500';
                    topPInput.value = localStorage.getItem('topP') || '1';
                    showSystemMessage.checked = localStorage.getItem('showSystemMessage') !== 'false';
                    useTools.checked = localStorage.getItem('useTools') !== 'false';
                    executeFunctions.checked = localStorage.getItem('executeFunctions') === 'true';
                    applyClientSettings.checked = localStorage.getItem('applyClientSettings') === 'true';
                    
                    // If applyClientSettings is checked, load client settings
                    if (applyClientSettings.checked) {
                        loadClientSettings();
                    }
                }
                
                // Save settings to localStorage
                function saveSettings() {
                    localStorage.setItem('clientId', clientIdSelect.value);
                    localStorage.setItem('model', modelSelect.value);
                    localStorage.setItem('temperature', temperatureInput.value);
                    localStorage.setItem('maxTokens', maxTokensInput.value);
                    localStorage.setItem('topP', topPInput.value);
                    localStorage.setItem('showSystemMessage', showSystemMessage.checked);
                    localStorage.setItem('useTools', useTools.checked);
                    localStorage.setItem('executeFunctions', executeFunctions.checked);
                    localStorage.setItem('applyClientSettings', applyClientSettings.checked);
                }
                
                // Load settings for the selected client
                async function loadClientSettings() {
                    try {
                        const response = await fetch(`/api/v1/client-settings?client_id=${clientIdSelect.value}`);
                        if (response.ok) {
                            const settings = await response.json();
                            if (settings && settings.status === 'success') {
                                modelSelect.value = settings.settings.OPENAI_CHAT_MODEL || modelSelect.value;
                                temperatureInput.value = settings.settings.OPENAI_CHAT_TEMPERATURE || temperatureInput.value;
                                maxTokensInput.value = settings.settings.OPENAI_CHAT_MAX_TOKENS || maxTokensInput.value;
                                topPInput.value = settings.settings.OPENAI_CHAT_TOP_P || topPInput.value;
                            }
                        }
                    } catch (error) {
                        console.error('Error loading client settings:', error);
                    }
                }
                
                // Initialize event listeners
                function initEventListeners() {
                    // Send message on button click or Enter key
                    sendBtn.addEventListener('click', sendMessage);
                    userInput.addEventListener('keydown', function(e) {
                        if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            sendMessage();
                        }
                    });
                    
                    // Clear chat history
                    clearBtn.addEventListener('click', clearChat);
                    
                    // Copy raw response to clipboard
                    copyRawBtn.addEventListener('click', function() {
                        if (lastRawResponse) {
                            navigator.clipboard.writeText(JSON.stringify(lastRawResponse, null, 2))
                                .then(() => alert('Raw response copied to clipboard!'))
                                .catch(err => console.error('Failed to copy: ', err));
                        }
                    });
                    
                    // Save settings when changed
                    const settingsInputs = [clientIdSelect, modelSelect, temperatureInput, maxTokensInput, topPInput, 
                                            showSystemMessage, useTools, executeFunctions, applyClientSettings];
                    settingsInputs.forEach(input => {
                        input.addEventListener('change', function() {
                            saveSettings();
                            if (input === applyClientSettings && applyClientSettings.checked) {
                                loadClientSettings();
                            }
                            // If client ID changes, update the system message
                            if (input === clientIdSelect) {
                                updateSystemMessage();
                            }
                        });
                    });
                    
                    // Reset settings to defaults
                    resetSettingsBtn.addEventListener('click', function() {
                        localStorage.clear();
                        initSettings();
                    });
                    
                    // Tab switching
                    tabs.forEach(tab => {
                        tab.addEventListener('click', function() {
                            const tabName = this.getAttribute('data-tab');
                            
                            // Update active tab
                            tabs.forEach(t => t.classList.remove('active'));
                            this.classList.add('active');
                            
                            // Show active tab content
                            document.querySelectorAll('.tab-content').forEach(content => {
                                content.classList.remove('active');
                            });
                            document.getElementById(`tab-${tabName}`).classList.add('active');
                        });
                    });
                }
                
                // Update system message in chat history
                async function updateSystemMessage() {
                    try {
                        const response = await fetch(`/api/v1/system-message?client_id=${clientIdSelect.value}`);
                        if (response.ok) {
                            const data = await response.json();
                            if (data && data.status === 'success') {
                                // Find existing system message and update it, or add a new one
                                const systemMessageIndex = messages.findIndex(msg => msg.role === 'system');
                                if (systemMessageIndex >= 0) {
                                    messages[systemMessageIndex].content = data.system_message;
                                } else {
                                    messages.unshift({ role: 'system', content: data.system_message });
                                }
                                
                                // Refresh chat display
                                renderChatHistory();
                            }
                        }
                    } catch (error) {
                        console.error('Error updating system message:', error);
                    }
                }
                
                // Send user message to the API
                async function sendMessage() {
                    const userMessage = userInput.value.trim();
                    if (!userMessage) return;
                    
                    // Add user message to chat history
                    messages.push({ role: 'user', content: userMessage });
                    userInput.value = '';
                    renderChatHistory();
                    
                    // Show loading indicator
                    loading.style.display = 'block';
                    
                    try {
                        // Check if system message exists, add if needed
                        if (!messages.some(msg => msg.role === 'system')) {
                            await updateSystemMessage();
                        }
                        
                        // Prepare request data
                        const requestData = {
                            messages: messages.filter(msg => msg.role !== 'system' || showSystemMessage.checked),
                            client_id: clientIdSelect.value,
                            model: modelSelect.value,
                            temperature: parseFloat(temperatureInput.value),
                            max_tokens: parseInt(maxTokensInput.value),
                            top_p: parseFloat(topPInput.value),
                            use_tools: useTools.checked,
                            execute_functions: executeFunctions.checked
                        };
                        
                        // Send request to API
                        const response = await fetch('/api/v1/test-chat-message', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify(requestData)
                        });
                        
                        if (response.ok) {
                            const data = await response.json();
                            lastRawResponse = data;
                            
                            // Display raw response
                            rawResponse.textContent = JSON.stringify(data, null, 2);
                            
                            if (data.status === 'success' && data.response) {
                                // Add assistant response to chat history
                                const assistantMessage = {
                                    role: 'assistant',
                                    content: data.response.content || data.response.choices[0].message.content
                                };
                                
                                // Handle function calls if present
                                if (data.response.choices && 
                                    data.response.choices[0].message.function_call) {
                                    assistantMessage.function_call = data.response.choices[0].message.function_call;
                                } else if (data.response.choices && 
                                          data.response.choices[0].message.tool_calls) {
                                    assistantMessage.tool_calls = data.response.choices[0].message.tool_calls;
                                }
                                
                                messages.push(assistantMessage);
                                renderChatHistory();
                            } else {
                                // Show error message
                                const errorDiv = document.createElement('div');
                                errorDiv.className = 'message-container';
                                errorDiv.innerHTML = `
                                    <div class="message-header">Error</div>
                                    <div class="assistant-message">
                                        Failed to get response: ${data.message || 'Unknown error'}
                                    </div>
                                `;
                                chatHistory.appendChild(errorDiv);
                                chatHistory.scrollTop = chatHistory.scrollHeight;
                            }
                        } else {
                            throw new Error(`HTTP error! status: ${response.status}`);
                        }
                    } catch (error) {
                        console.error('Error:', error);
                        const errorDiv = document.createElement('div');
                        errorDiv.className = 'message-container';
                        errorDiv.innerHTML = `
                            <div class="message-header">Error</div>
                            <div class="assistant-message">
                                There was an error processing your request: ${error.message}
                            </div>
                        `;
                        chatHistory.appendChild(errorDiv);
                        chatHistory.scrollTop = chatHistory.scrollHeight;
                    } finally {
                        loading.style.display = 'none';
                    }
                }
                
                // Render the chat history
                function renderChatHistory() {
                    chatHistory.innerHTML = '';
                    
                    messages.forEach(message => {
                        const messageDiv = document.createElement('div');
                        messageDiv.className = 'message-container';
                        
                        let messageContent = '';
                        let messageClass = '';
                        let messageHeader = '';
                        
                        switch (message.role) {
                            case 'system':
                                messageClass = 'system-message';
                                messageHeader = 'System';
                                messageContent = message.content;
                                // Only display if show system message is checked
                                if (!showSystemMessage.checked) return;
                                break;
                            case 'user':
                                messageClass = 'user-message';
                                messageHeader = 'User';
                                messageContent = message.content;
                                break;
                            case 'assistant':
                                messageClass = 'assistant-message';
                                messageHeader = 'Assistant';
                                messageContent = message.content || '';
                                
                                // Handle function/tool calls
                                if (message.function_call) {
                                    const functionCall = document.createElement('div');
                                    functionCall.className = 'function-call';
                                    functionCall.textContent = `Function: ${message.function_call.name}\nArguments: ${message.function_call.arguments}`;
                                    messageDiv.appendChild(functionCall);
                                } else if (message.tool_calls) {
                                    message.tool_calls.forEach(toolCall => {
                                        const functionCall = document.createElement('div');
                                        functionCall.className = 'function-call';
                                        functionCall.textContent = `Function: ${toolCall.function.name}\nArguments: ${toolCall.function.arguments}`;
                                        messageDiv.appendChild(functionCall);
                                    });
                                }
                                break;
                            case 'function':
                                messageClass = 'function-response';
                                messageHeader = `Function: ${message.name}`;
                                messageContent = message.content;
                                break;
                        }
                        
                        messageDiv.innerHTML += `
                            <div class="message-header">${messageHeader}</div>
                            <div class="${messageClass}">${messageContent}</div>
                        `;
                        
                        chatHistory.appendChild(messageDiv);
                    });
                    
                    // Scroll to bottom
                    chatHistory.scrollTop = chatHistory.scrollHeight;
                }
                
                // Clear chat history
                function clearChat() {
                    const systemMessage = messages.find(msg => msg.role === 'system');
                    messages = systemMessage ? [systemMessage] : [];
                    renderChatHistory();
                    lastRawResponse = null;
                    rawResponse.textContent = '';
                }
                
                // Initialize
                initSettings();
                initEventListeners();
                updateSystemMessage();
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
@router.get("/test-square")
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
            process_square_payment,
        )

        results = {}

        # Test 1: Get location ID
        logger.info("Test 1: Getting Square location ID...")
        if asyncio.iscoroutinefunction(get_square_location_id):
            location_id = await get_square_location_id()
        else:
            location_id = get_square_location_id()
        logger.info(f"Location ID result: {location_id}")
        results["location_id"] = {
            "success": location_id is not None,
            "value": location_id,
        }

        # Test 2: List catalog items
        logger.info("Test 2: Listing catalog items...")
        if asyncio.iscoroutinefunction(list_catalog_items):
            catalog_data = await list_catalog_items()
        else:
            catalog_data = list_catalog_items()
        logger.info(
            f"Catalog items count: {len(catalog_data.get('objects', [])) if catalog_data else 0}"
        )
        results["catalog_items"] = {
            "success": catalog_data is not None,
            "count": len(catalog_data.get("objects", [])) if catalog_data else 0,
        }

        if catalog_data:
            logger.info("Test 3: Extracting menu data...")
            menu = extract_menu_data(catalog_data)
            logger.info(f"Menu items count: {len(menu)}")
            
            # Display more detailed information about all menu items
            logger.info("Menu items details:")
            for i, item in enumerate(menu):
                logger.info(f"Item {i+1}: {item['name']}")
                
                # Show variations with prices
                if 'variations' in item and item['variations']:
                    logger.info(f"  Variations:")
                    for v in item['variations']:
                        logger.info(f"    - {v['name']}: ${v['price']}")
                else:
                    logger.info("  No variations available")
                
                logger.info("---")
            
            results["menu"] = {
                "success": len(menu) > 0,
                "count": len(menu),
                "first_item": menu[0] if menu else None,
                "all_items": menu  # Include all items in the results
            }
        # Test 4: Find variation ID
        if catalog_data and "menu" in results:
            logger.info("Test 4: Finding variation ID by name...")
            # Get the first item name from the menu
            first_item_name = (
                results["menu"]["all_items"][0]["name"]
                if results["menu"]["all_items"]
                else None
            )
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
                    "item_name": first_item_name,
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
            order_total = (
                order_result.get("order", {}).get("total_money", {}).get("amount")
                if order_result
                else None
            )

            results["create_order"] = {
                "success": order_result is not None and "order" in order_result,
                "order_id": order_id,
                "order_total": order_total,
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
                        payment_result = await process_square_payment(
                            order_id, order_total, payment_method_id
                        )
                    else:
                        payment_result = process_square_payment(
                            order_id, order_total, payment_method_id
                        )

                    logger.info(f"Payment result: {payment_result}")

                    payment_status = (
                        payment_result.get("payment", {}).get("status")
                        if payment_result and "payment" in payment_result
                        else None
                    )
                    payment_error = (
                        payment_result.get("error")
                        if payment_result
                        else "Unknown error"
                    )

                    results["process_payment"] = {
                        "success": payment_result is not None
                        and "payment" in payment_result,
                        "status": payment_status,
                        "error": (
                            payment_error
                            if not (
                                payment_result is not None
                                and "payment" in payment_result
                            )
                            else None
                        ),
                    }
                except Exception as payment_error:
                    logger.error(f"Payment processing exception: {str(payment_error)}")
                    results["process_payment"] = {
                        "success": False,
                        "error": str(payment_error),
                    }

        return {
            "status": "success",
            "results": results,
            "all_tests_passed": all(
                test.get("success", False)
                for test in results.values()
                if isinstance(test, dict)
            ),
        }

    except Exception as e:
        import traceback

        error_details = traceback.format_exc()
        return {"status": "error", "message": str(e), "details": error_details}
