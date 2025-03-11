from fastapi import APIRouter, Request, Response, Form, Cookie, Depends
from fastapi.responses import PlainTextResponse
import uuid
from datetime import datetime
from typing import Optional
import traceback
from urllib.parse import urlencode

from utils.twilio import gather_voice_message
from constants import CONSTANTS
from middleware.session import get_session, set_session

router = APIRouter()


@router.post("/calls/chat", response_class=PlainTextResponse)
async def post_chat(
    request: Request,
    From: str = Form(None),
    client_id: Optional[str] = None,
    timeSent: Optional[str] = Cookie(None),
):
    try:
        print("[POST:/calls/chat/] ")
        start_time = datetime.now()

        phone_number = From
        print("[POST:/calls/chat/] phone_number: ", phone_number)

        if client_id is None:
            return PlainTextResponse(
                "client_id is required and not found in request", status_code=400
            )

        if client_id not in CONSTANTS:
            return PlainTextResponse("client_id is invalid", status_code=400)

        assistant_message = CONSTANTS[client_id]["INITIAL_ASSISTANT_MESSAGE"]
        thread_id = str(uuid.uuid4())
        params = {"client_id": client_id, "thread_id": thread_id}
        param_string = urlencode(params)
        action_url = "/api/v1/calls/chat/response"

        # stringify menu
        menu_string = ""
        for item in CONSTANTS[client_id]["MENU"]:
            menu_string += "\n" + item
        print("[POST:/calls/chat/] menu_string: ", menu_string)
        tax_string = f"The tax percentage is: {CONSTANTS[client_id]['TAX'] * 100}%"

        system_message = (
            CONSTANTS[client_id]["SYSTEM_MESSAGE"]
            + " Here is the menu:"
            + menu_string
            + "\n"
            + tax_string
        )
        user_message = CONSTANTS[client_id]["INITIAL_USER_MESSAGE"]

        chat_history = [
            {"role": "system", "content": system_message},
            {
                "role": "user",
                "content": f"My phone number is {phone_number}. {user_message}",
            },
            {"role": "assistant", "content": assistant_message},
        ]

        response = gather_voice_message(
            client_id, assistant_message, action_url, param_string
        )

        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        print(f"[POST:/calls/chat/] execution time: {elapsed_time} seconds")

        # Store chat history in session
        session = get_session(request)
        session["chat_history"] = chat_history
        set_session(request, "chat_history", chat_history)

        # Create response with XML content
        resp = PlainTextResponse(response, media_type="application/xml")
        resp.set_cookie(key="timeSent", value=end_time.isoformat())

        return resp

    except Exception as error:
        print("[POST:/calls/chat/]", error)
        traceback.print_exc()
        return PlainTextResponse("Internal Error", status_code=500)
