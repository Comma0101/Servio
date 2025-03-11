from twilio.twiml.voice_response import Gather, VoiceResponse
import os
from app.constants import CONSTANTS

# Load environment variables
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

# Twilio client initialization
from twilio.rest import Client

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def gather_voice_message(client_id, message, action_url, param_string):
    try:
        response = VoiceResponse()
        gather = Gather(
            input="speech",
            action=action_url + "?" + param_string,
            speech_timeout=CONSTANTS[client_id]["TWILIO_SPEECH_TIMEOUT"],
            speech_model=CONSTANTS[client_id]["TWILIO_SPEECH_MODEL"],
            language=CONSTANTS[client_id]["TWILIO_LANGUAGE"],
            hints=CONSTANTS[client_id]["TWILIO_HINTS"],
        )
        if message:
            gather.say(
                message,
                voice=CONSTANTS[client_id]["TWILIO_VOICE"],
                language=CONSTANTS[client_id]["TWILIO_LANGUAGE"],
            )
        response.append(gather)
        response.redirect(action_url + "?" + param_string)

        return str(response)
    except Exception as error:
        print("[gatherVoiceMessage]", error)
        raise Exception("Internal Error")


def send_voice_message(
    client_id, message, action_url, param_string, gather=False, gatherMessage=""
):
    try:
        print("[sendVoiceMessage]")
        response = VoiceResponse()
        response.say(
            message,
            voice=CONSTANTS[client_id]["TWILIO_VOICE"],
            language=CONSTANTS[client_id]["TWILIO_LANGUAGE"],
        )
        if gather:
            gather_voice_message(client_id, gatherMessage, action_url, param_string)
        response.redirect(action_url + "?" + param_string)

        return str(response)
    except Exception as error:
        print("[sendVoiceMessage]", error)
        raise Exception("Internal Error")


def hang_up(client_id, message):
    try:
        response = VoiceResponse()
        response.say(
            message,
            voice=CONSTANTS[client_id]["TWILIO_VOICE"],
            language=CONSTANTS[client_id]["TWILIO_LANGUAGE"],
        )
        response.hangup()

        return str(response)
    except Exception as error:
        print("[hangUp]", error)
        raise Exception("Internal Error")
