from twilio.twiml.voice_response import Gather, VoiceResponse
import os
import sys
import logging
from twilio.rest import Client
from dotenv import load_dotenv, dotenv_values

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from app.constants import CONSTANTS

# Configure logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(override=True)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

# Debug output for Twilio credentials
logger.info("TWILIO CONFIG: Checking Twilio credentials availability")
logger.info(f"TWILIO CONFIG: Account SID exists: {bool(TWILIO_ACCOUNT_SID)}")
logger.info(f"TWILIO CONFIG: Auth Token exists: {bool(TWILIO_AUTH_TOKEN)}")
logger.info(f"TWILIO CONFIG: Phone Number exists: {bool(TWILIO_PHONE_NUMBER)}")
if TWILIO_ACCOUNT_SID:
    logger.info(f"TWILIO CONFIG: Account SID first/last 4 chars: {TWILIO_ACCOUNT_SID[:4]}...{TWILIO_ACCOUNT_SID[-4:]}")

# Check credentials at module level
if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
    logger.warning("Twilio credentials missing or incomplete. Functions requiring API access will fail.")


def get_call_details(call_sid):
    """
    Retrieve details for a specific call using its SID.
    
    Args:
        call_sid (str): The SID of the call to fetch details for
        
    Returns:
        dict: A dictionary containing call details or an error message
    """
    try:
        # Initialize Twilio client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Fetch the call details from Twilio
        call = client.calls(call_sid).fetch()
        
        # Log the complete call details for debugging
        logger.info(f"TWILIO CALL DETAILS: {call.__dict__}")
        
        # Extract and return important call information
        return {
            "success": True,
            "call_sid": call.sid,
            "from_number": call._from,
            "to_number": call.to,
            "direction": call.direction,
            "status": call.status,
            "start_time": call.start_time,
            "end_time": call.end_time,
            "duration": call.duration,
            "price": call.price,
            "caller_name": getattr(call, 'caller_name', None)
        }
    except Exception as e:
        logger.error(f"Error fetching call details for SID {call_sid}: {str(e)}")
        return {"success": False, "error": str(e)}


def gather_voice_message(client_id, message, action_url, param_string):
    try:
        # Initialize Twilio client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
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
        # Initialize Twilio client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
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
        # Initialize Twilio client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
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


def end_call(call_sid):
    """
    End an active Twilio call using the REST API
    
    Args:
        call_sid (str): The SID of the call to end
        
    Returns:
        dict: Status information about the call ending
    """
    try:
        logger.info(f"Ending Twilio call: {call_sid}")
        
        # Initialize Twilio client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Update the call status to "completed" to end it
        call = client.calls(call_sid).update(status="completed")
        
        logger.info(f"Call {call_sid} successfully ended with status: {call.status}")
        
        return {
            "success": True,
            "call_sid": call_sid, 
            "status": call.status
        }
    except Exception as e:
        logger.error(f"Error ending call {call_sid}: {e}")
        return {
            "success": False,
            "call_sid": call_sid,
            "error": str(e)
        }


def send_sms(to_number, message, client_id="LIMF"):
    """
    Sends an SMS using Twilio.
    
    Args:
        to_number (str): The phone number to send the SMS to
        message (str): The message to send
        client_id (str, optional): Client identifier for tracking. Defaults to "LIMF".
        
    Returns:
        dict: A dictionary containing the success status and additional information
    """
    try:
        # Use the global Twilio credentials
        account_sid = TWILIO_ACCOUNT_SID
        auth_token = TWILIO_AUTH_TOKEN
        twilio_phone_number = TWILIO_PHONE_NUMBER
        
        # Check for quotation marks in credentials which could cause authentication issues
        if account_sid and (account_sid.startswith('"') or account_sid.endswith('"')):
            logger.warning("WARNING: Account SID contains quotation marks which may cause authentication issues")
            account_sid = account_sid.strip('"')
            
        if auth_token and (auth_token.startswith('"') or auth_token.endswith('"')):
            logger.warning("WARNING: Auth Token contains quotation marks which may cause authentication issues")
            auth_token = auth_token.strip('"')
            
        if twilio_phone_number and (twilio_phone_number.startswith('"') or twilio_phone_number.endswith('"')):
            logger.warning("WARNING: Twilio phone number contains quotation marks which may cause issues")
            twilio_phone_number = twilio_phone_number.strip('"')
            
        logger.info(f"Found Account SID: {bool(account_sid)}, Auth Token: {bool(auth_token)}, Phone: {bool(twilio_phone_number)}")
        # Log the account SID we're using (useful for debugging)
        logger.info(f"Using Twilio Account SID: {account_sid}")
        
        # Add more detailed credential debugging (with partial masking for security)
        if account_sid:
            logger.info(f"Account SID first/last 4 chars: {account_sid[:4]}...{account_sid[-4:]}")
        if auth_token:
            # Only show first and last 2 characters of auth token for security
            logger.info(f"Auth Token first/last 2 chars: {auth_token[:2]}...{auth_token[-2:]}")
        
        # Generate and log the Base64 encoded authorization header that will be sent to Twilio
        import base64
        if account_sid and auth_token:
            auth_string = f"{account_sid}:{auth_token}"
            encoded_auth = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
            logger.info(f"Authorization header (Base64): Basic {encoded_auth[:5]}...{encoded_auth[-5:]}")
        
        # Check if we have valid credentials
        if not account_sid or not auth_token:
            logger.error("SMS ERROR: Missing Twilio credentials: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not set")
            return {"success": False, "error": "Twilio credentials not configured"}
        
        # Detailed logging of the API request
        logger.info("-- BEGIN Twilio API Request --")
        logger.info(f"POST Request: https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json")
        logger.info("Headers:")
        logger.info("Content-Type : application/x-www-form-urlencoded")
        logger.info("Accept : application/json")
        logger.info(f"User-Agent : twilio-python (Linux x86_64) Python/{sys.version.split()[0]}")
        logger.info("Accept-Charset : utf-8")
        logger.info("-- END Twilio API Request --")
        
        # Create a new client instance with the credentials to ensure they're used
        try:
            # Force client initialization with fresh credentials
            logger.info("Creating Twilio client with explicit credentials")
            
            # Direct approach with explicit credentials
            twilio_client = Client(
                username=account_sid,  # Try using explicit parameter names
                password=auth_token,
                account_sid=account_sid
            )
            
            # Log the client configuration
            logger.info(f"Client created with account_sid: {twilio_client.account_sid}")
            logger.info(f"Client auth credentials length: {len(twilio_client.auth)}")
            
            # Verify the auth credentials are properly set
            if hasattr(twilio_client, 'auth') and twilio_client.auth:
                import base64
                auth_header = twilio_client.auth
                if isinstance(auth_header, bytes):
                    auth_header = auth_header.decode('utf-8')
                logger.info(f"Client auth header: {auth_header[:10]}...{auth_header[-5:]}")
        except Exception as e:
            logger.error(f"Error creating Twilio client: {str(e)}")
            return {"success": False, "error": f"Error creating Twilio client: {str(e)}"}
        
        # Log the SMS request
        logger.info(f"SMS REQUEST: To: {to_number}, Length: {len(message)}, Client ID: {client_id}")
        
        # Format the phone number if needed - ensure E.164 format (+1XXXXXXXXXX)
        if to_number and not to_number.startswith('+'):
            # Remove any non-digit characters
            digits_only = ''.join(filter(str.isdigit, to_number))
            
            # Add US country code if 10 digits
            if len(digits_only) == 10:
                to_number = f"+1{digits_only}"
            elif len(digits_only) == 11 and digits_only.startswith('1'):
                to_number = f"+{digits_only}"
            else:
                to_number = f"+{digits_only}"
            
            logger.info(f"SMS PHONE FORMAT: Reformatted phone number to {to_number}")
        
        # Get the from number from environment variable or constants
        from_number = twilio_phone_number
        
        # If not found in environment, try to get from CONSTANTS
        if not from_number:
            from_number = CONSTANTS.get(client_id, {}).get("TWILIO_PHONE_NUMBER")
            
            # Try generic TWILIO_PHONE_NUMBER in CONSTANTS if client-specific isn't found
            if not from_number:
                from_number = CONSTANTS.get("TWILIO_PHONE_NUMBER")
        
        logger.info(f"SMS USING PHONE NUMBER: {from_number} (source: env)")
        
        if not from_number:
            logger.error("SMS ERROR: No Twilio phone number found in environment variables or constants")
            return {"success": False, "error": "Twilio phone number not configured"}
        
        logger.info(f"SMS SENDING: From: {from_number}, To: {to_number}")
        
        # Send the SMS
        message_resource = twilio_client.messages.create(
            body=message,
            from_=from_number,
            to=to_number
        )
        
        # Log success details
        logger.info(f"SMS SUCCESS: Message SID: {message_resource.sid}")
        logger.info(f"SMS COMPLETE RESPONSE: {message_resource.__dict__}")
        
        return {
            "success": True,
            "message_sid": message_resource.sid,
            "status": message_resource.status,
            "to": message_resource.to,
            "from": message_resource.from_
        }
        
    except Exception as e:
        logger.error(f"SMS ERROR: Failed to send SMS: {str(e)}")
        return {"success": False, "error": str(e)}
