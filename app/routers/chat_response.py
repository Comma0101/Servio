from fastapi import APIRouter, Request, Response, Form, Query, Cookie, Depends
from fastapi.responses import PlainTextResponse
from datetime import datetime
import json
import traceback
from typing import Optional, List, Dict
from urllib.parse import urlencode

from app.utils.openai import create_chat_completion
from app.utils.square import test_create_order_endpoint, test_payment_processing
from app.utils.twilio import gather_voice_message, hang_up
from app.constants import CONSTANTS
from app.middleware.session import get_session, set_session
from app.utils.redis_store import store_chat_history, get_chat_history

router = APIRouter()


async def retry_gather(message, params):
    param_string = urlencode(params)
    action_url = "/api/v1/calls/chat/response"
    response = gather_voice_message(
        params["client_id"], message, action_url, param_string
    )
    return PlainTextResponse(response, media_type="application/xml")


@router.post("/calls/chat/response", response_class=PlainTextResponse)
async def post_chat_response(
    request: Request,
    SpeechResult: Optional[str] = Form(None),
    thread_id: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None),
    timeSent: Optional[str] = Cookie(None),
    CallSid: Optional[str] = Form(None),  # Add CallSid from Twilio request
):
    try:
        start_time = datetime.now()

        if timeSent:
            print(
                "[POST:/calls/chat/response] Twilio gather time: ",
                (start_time - datetime.fromisoformat(timeSent)).total_seconds(),
                "seconds",
            )

        speech_result = SpeechResult
        print("[POST:/calls/chat/response] SpeechResult:", speech_result, ".")
        print("[POST:/calls/chat/response] CallSid:", CallSid)

        if speech_result:
            print(
                "[POST:/calls/chat/response] SpeechResult:", speech_result.strip(), "."
            )
            print("[POST:/calls/chat/response] SpeechResult:", speech_result.isspace())

        params = {"client_id": client_id, "thread_id": thread_id}

        if client_id is None:
            return PlainTextResponse(
                "client_id is required and not found in request", status_code=400
            )

        if client_id not in CONSTANTS:
            return PlainTextResponse("client_id is invalid", status_code=400)

        if speech_result is None or speech_result.strip() == "":
            # Check if this is the first interaction with no speech
            # This is likely when Twilio first connects the call
            is_first_interaction = False
            
            # Try to get chat history from Redis
            redis_history = None
            if CallSid:
                try:
                    redis_history = get_chat_history(CallSid)
                except Exception as e:
                    print(f"[POST:/calls/chat/response] Redis error checking history: {e}")
            
            # Check session history
            session = get_session(request)
            session_history = session.get("chat_history", [])
            
            # If no history in either place, this is likely the first interaction
            if not redis_history and not session_history:
                is_first_interaction = True
                print("[POST:/calls/chat/response] First interaction detected - playing welcome message")
            
            # Use the right message based on whether this is first interaction
            if is_first_interaction:
                # Use initial greeting
                message = CONSTANTS[client_id]["INITIAL_ASSISTANT_MESSAGE"]
            else:
                # Use standard retry message
                message = "Sorry, I didn't get that. Please say that again?"
                
            return await retry_gather(message, params)

        # Try to get chat history from Redis first using the CallSid
        chat_history = None
        if CallSid:
            chat_history = get_chat_history(CallSid)
            print(f"[POST:/calls/chat/response] Redis chat history for {CallSid}: {chat_history is not None}")
            
        # Fallback to session if Redis lookup fails
        if chat_history is None:
            session = get_session(request)
            chat_history = session.get("chat_history", [])
            print("[POST:/calls/chat/response] Session chat history: ", chat_history)

        # If chat history is empty, initialize it with system message and menu
        if not chat_history:
            print("[POST:/calls/chat/response] Initializing chat history with system message")
            # Initialize chat history with system message and menu
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
            
            # Initialize the chat history with the system message
            chat_history = [
                {"role": "system", "content": system_message},
                {"role": "assistant", "content": CONSTANTS[client_id]["INITIAL_ASSISTANT_MESSAGE"]}
            ]
            print("[POST:/calls/chat/response] Initialized system message:", system_message[:100] + "...")

        chat_history.append({"role": "user", "content": speech_result})
        functions = CONSTANTS[client_id].get("OPENAI_CHAT_TOOLS")

        ai_response = await create_chat_completion(
            chat_history,
            model=CONSTANTS[client_id]["OPENAI_CHAT_MODEL"],
            max_tokens=CONSTANTS[client_id]["OPENAI_CHAT_MAX_TOKENS"],
            temperature=CONSTANTS[client_id]["OPENAI_CHAT_TEMPERATURE"],
            top_p=CONSTANTS[client_id]["OPENAI_CHAT_TOP_P"],
            functions=functions,
        )

        if ai_response is None:
            message = "Sorry, I didn't get that. Please say that again?"
            return await retry_gather(message, params)

        chat_history.append({"role": "assistant", "content": ai_response.content})
        param_string = urlencode(params)
        action_url = "/api/v1/calls/chat/response"

        if hasattr(ai_response, "tool_calls") and ai_response.tool_calls:
            for tool_call in ai_response.tool_calls:
                try:
                    function_name = tool_call.function.name
                    arguments = tool_call.function.arguments
                except Exception as e:
                    print(f"ERROR: Failed to parse tool call: {e}")
                    continue

                # Handle the function call
                function_response = await execute_function_call(function_name, arguments)

                print("im function response", function_response)

                function_message = function_response["message"]
                order_complete = function_response["order_complete"]

                # Add the function's response to the chat history
                chat_history.append(
                    {
                        "role": "function",
                        "name": function_name,
                        "content": function_response,
                    }
                )

                if order_complete:
                    response = hang_up(client_id, function_message)
                    resp = PlainTextResponse(response, media_type="application/xml")
                    set_session(request, "chat_history", chat_history)
                    return resp
        elif hasattr(ai_response, "function_call") and ai_response.function_call:
            # For backward compatibility with the older API format
            function_call = ai_response.function_call
            function_name = function_call.name
            arguments = function_call.arguments if hasattr(function_call, "arguments") else None

            # Handle the function call
            function_response = await execute_function_call(function_name, arguments)

            print("im function response", function_response)

            function_message = function_response["message"]
            order_complete = function_response["order_complete"]

            # Add the function's response to the chat history
            chat_history.append(
                {
                    "role": "function",
                    "name": function_name,
                    "content": json.dumps(function_response),
                }
            )

            if order_complete:
                response = hang_up(client_id, function_message)
                resp = PlainTextResponse(response, media_type="application/xml")
                set_session(request, "chat_history", chat_history)
                return resp
            else:
                # Continue the conversation
                response = gather_voice_message(
                    client_id, function_message, action_url, param_string
                )
                # Save the updated chat history in Redis and session
                if CallSid:
                    store_chat_history(CallSid, chat_history)
                set_session(request, "chat_history", chat_history)

                # Send the response
                resp = PlainTextResponse(response, media_type="application/xml")
                return resp
        else:
            # Check if the content contains a function call in JSON format
            order_processed = False
            try:
                # Look for JSON-like structure in the content
                content = ai_response.content
                if content and '"summary": "DONE"' in content:
                    # Extract the JSON part
                    json_start = content.find('{')
                    json_end = content.rfind('}') + 1
                    if json_start >= 0 and json_end > json_start:
                        json_str = content[json_start:json_end]
                        try:
                            arguments = json.loads(json_str)
                            function_name = "order_summary"  # Default function for order completion
                            
                            # Handle the function call
                            function_response = await execute_function_call(function_name, arguments)
                            
                            print("Extracted function response:", function_response)
                            
                            function_message = function_response["message"]
                            order_complete = function_response["order_complete"]
                            
                            # Add the function's response to the chat history
                            chat_history.append(
                                {
                                    "role": "function",
                                    "name": function_name,
                                    "content": json.dumps(function_response),
                                }
                            )
                            
                            if order_complete:
                                order_processed = True
                                response = hang_up(client_id, function_message)
                                resp = PlainTextResponse(response, media_type="application/xml")
                                set_session(request, "chat_history", chat_history)
                                return resp
                        except json.JSONDecodeError as e:
                            print(f"ERROR: Failed to parse potential function call in content: {e}")
            except Exception as e:
                print(f"ERROR: Exception while trying to extract function call from content: {e}")
                import traceback
                traceback.print_exc()
                
            # If we're here, it's a regular response or we couldn't extract a function call
            if not order_processed:
                # Clean up any JSON that might be in the content
                content = ai_response.content
                if '"summary": "DONE"' in content and '{' in content and '}' in content:
                    # Replace the JSON with a cleaner message
                    client_id = "LIMF"  # Default client ID
                    success_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["SUCCESS"]
                    
                    # Format a nice summary without the raw JSON
                    try:
                        json_start = content.find('{')
                        json_end = content.rfind('}') + 1
                        json_str = content[json_start:json_end]
                        order_data = json.loads(json_str)
                        
                        # Create a formatted order summary
                        items = order_data.get("items", [])
                        items_text = ", ".join([f"{item.get('quantity', 1)} {item.get('variation', '')} {item.get('name', '')}" for item in items])
                        total_price = order_data.get("total_price", 0)
                        
                        # Clean up the message by replacing the JSON part with formatted text
                        formatted_message = f"Thank you for your order of {items_text}. Total: ${total_price:.2f}. {success_message}"
                        message = formatted_message
                    except:
                        # If there's any error processing the JSON, use the success message directly
                        message = success_message
                else:
                    message = content
                
                response = gather_voice_message(client_id, message, action_url, param_string)
                # Store chat history in both Redis and session
                if CallSid:
                    store_chat_history(CallSid, chat_history)
                set_session(request, "chat_history", chat_history)

                end_time = datetime.now()
                elapsed_time = (end_time - start_time).total_seconds()
                print(f"[POST:/calls/chat/response] execution time: {elapsed_time} seconds")

                resp = PlainTextResponse(response, media_type="application/xml")
                resp.set_cookie(key="timeSent", value=end_time.isoformat())

                return resp

    except Exception as error:
        print("[POST:/calls/chat/response]", error)
        traceback.print_exc()
        return PlainTextResponse("Internal Error", status_code=500)

# Replace your execute_function_call function with this implementation
async def execute_function_call(function_name, arguments):
    test_payment_method_id = "cnon:card-nonce-ok"
    
    print(f"DEBUG: execute_function_call received: function={function_name}, arguments={arguments}, type={type(arguments)}")
    
    # Handle different argument formats
    arguments_json = {}
    try:
        # Case 1: Arguments is already a dictionary
        if isinstance(arguments, dict):
            print("DEBUG: Arguments is already a dict")
            arguments_json = arguments
        # Case 2: Arguments is None or empty
        elif arguments is None or arguments == "":
            print("DEBUG: Arguments is None or empty, using empty dict")
            arguments_json = {}
        # Case 3: Arguments is a string that needs to be parsed as JSON
        elif isinstance(arguments, str):
            print(f"DEBUG: Arguments is a string, attempting to parse as JSON: '{arguments}'")
            try:
                arguments_json = json.loads(arguments)
                print(f"DEBUG: Successfully parsed JSON arguments")
            except json.JSONDecodeError as e:
                print(f"ERROR: Failed to parse arguments as JSON: {e}")
                return {
                    "message": f"Failed to parse function arguments: {str(e)}",
                    "order_complete": False,
                }
        else:
            print(f"ERROR: Unsupported argument type: {type(arguments)}")
            return {
                "message": f"Unsupported argument type: {type(arguments)}",
                "order_complete": False,
            }
        
        print(f"DEBUG: Parsed arguments: {arguments_json}")
        
    except Exception as e:
        print(f"ERROR: Unexpected exception parsing arguments: {e}")
        import traceback
        traceback.print_exc()
        return {
            "message": f"Error processing function arguments: {str(e)}",
            "order_complete": False,
        }
    
    # Process the function call with the parsed arguments
    if function_name == "order_summary":
        # Process the order summary
        summary_status = arguments_json.get("summary")
        if summary_status == "DONE":
            # Order is complete
            # place order in via square
            order_data = arguments_json.get("items", [])

            result = await test_create_order_endpoint(order_data)
            current_order_id = result["order"]["id"]
            current_order_total = result["order"]["total_money"].get("amount")
            if current_order_id:
                payment_result = await test_payment_processing(
                    current_order_id, current_order_total, test_payment_method_id
                )
                if payment_result["payment"].get("status") == "COMPLETED":
                    # Use the predefined success message from constants
                    client_id = "LIMF"  # Default client ID - consider passing this as a parameter
                    success_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["SUCCESS"]
                    
                    # Include order summary for informational purposes
                    items_info = ", ".join([f"{item.get('quantity', 1)} {item.get('name', 'item')}" for item in order_data])
                    total_price = current_order_total / 100  # Convert cents to dollars
                    info_message = f"Order details: {items_info}. Total: ${total_price:.2f}"
                    
                    return {
                        "message": success_message,
                        "order_complete": True,
                        "order_info": info_message
                    }
                else:
                    # Use the predefined failure message from constants
                    client_id = "LIMF"  # Default client ID
                    failure_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["FAILURE"]
                    return {
                        "message": failure_message,
                        "order_complete": False
                    }
            else:
                # Use the predefined failure message from constants
                client_id = "LIMF"  # Default client ID
                failure_message = CONSTANTS[client_id]["OPENAI_CHAT_TOOLS_RESPONSES"]["place_restaurant_order"]["FAILURE"]
                return {
                    "message": failure_message, 
                    "order_complete": False
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