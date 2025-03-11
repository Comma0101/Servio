from fastapi import APIRouter, Request, Response, Form, Query, Cookie, Depends
from fastapi.responses import PlainTextResponse
from datetime import datetime
import json
import traceback
from typing import Optional
from urllib.parse import urlencode

from utils.openai import create_chat_completion
from utils.square import test_create_order_endpoint, test_payment_processing
from utils.twilio import gather_voice_message, hang_up
from constants import CONSTANTS
from middleware.session import get_session, set_session

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
            message = "Sorry, I didn't get that. Please say that again?"
            return await retry_gather(message, params)

        # Get chat history from session
        session = get_session(request)
        chat_history = session.get("chat_history", [])
        print("[POST:/calls/chat/response] chat_history: ", chat_history)

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

        chat_history.append({"role": "assistant", "content": ai_response["content"]})
        param_string = urlencode(params)
        action_url = "/api/v1/calls/chat/response"

        if "function_call" in ai_response:
            function_call = ai_response["function_call"]
            function_name = function_call["name"]
            arguments = function_call.get("arguments")

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
            else:
                # Continue the conversation
                response = gather_voice_message(
                    client_id, function_message, action_url, param_string
                )
                # Save the updated chat history in the session
                set_session(request, "chat_history", chat_history)

                # Send the response
                resp = PlainTextResponse(response, media_type="application/xml")
                return resp
        else:
            chat_history.append({"role": "assistant", "content": ai_response.content})

            response = gather_voice_message(
                client_id, ai_response.content, action_url, param_string
            )
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


async def execute_function_call(function_name, arguments_json):
    test_payment_method_id = "cnon:card-nonce-ok"
    try:
        arguments = json.loads(arguments_json)
        print("im json arguments", arguments)
    except json.JSONDecodeError:
        print("Failed to parse function arguments")
        return {
            "message": "Failed to parse function arguments",
            "order_complete": False,
        }

    if function_name == "order_summary":
        # Process the order summary
        summary_status = arguments.get("summary")
        if summary_status == "DONE":
            # Order is complete
            # place order in via square
            order_data = arguments.get("items", [])

            result = await test_create_order_endpoint(order_data)
            current_order_id = result["order"]["id"]
            current_order_total = result["order"]["total_money"].get("amount")
            if current_order_id:
                payment_result = await test_payment_processing(
                    current_order_id, current_order_total, test_payment_method_id
                )
                if payment_result["payment"].get("status") == "COMPLETED":
                    return {
                        "message": "Thank you for your order! Your payment has been processed.",
                        "order_complete": True,
                    }
            else:
                return {"message": "Failed to create order.", "order_complete": False}
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
