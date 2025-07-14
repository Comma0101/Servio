import logging
# from google.cloud.speech_v2 import SpeechAsyncClient  # Import v2 client
# from google.cloud.speech_v2.types import cloud_speech # Import v2 types
# from google.cloud.texttospeech_v1 import TextToSpeechAsyncClient
from openai import AsyncOpenAI
import os
try:
    from soniox.speech_service import SpeechClient as SonioxSpeechClient
except ImportError:
    logger.warning("Could not import SpeechClient from soniox.speech_service, trying direct import.")
    try:
        import soniox
        SonioxSpeechClient = soniox.SpeechClient # type: ignore
    except (ImportError, AttributeError) as e:
        logger.error(f"Failed to import Soniox SpeechClient: {e}. Soniox STT will not be available.")
        SonioxSpeechClient = None # type: ignore


logger = logging.getLogger(__name__)

# Global singleton instances, to be initialized at startup
# speech_client_instance: SpeechAsyncClient | None = None # Use v2 client type
# tts_client_instance: TextToSpeechAsyncClient | None = None
openai_client_instance: AsyncOpenAI | None = None
soniox_client_instance: SonioxSpeechClient | None = None # Using SonioxSpeechClient

async def initialize_global_clients():
    """Initializes all global clients (Google STT v2, Google TTS, OpenAI, Soniox)."""
    global speech_client_instance, tts_client_instance, openai_client_instance, soniox_client_instance
    
    # Initialize Google Speech-to-Text Client (v2)
    # try:
    #     if speech_client_instance is None:
    #         speech_client_options = {"api_endpoint": "asia-southeast1-speech.googleapis.com"}
    #         speech_client_instance = SpeechAsyncClient(client_options=speech_client_options)
    #         logger.info("Singleton Google SpeechAsyncClient (STT v2) initialized successfully for asia-southeast1.")
    #     else:
    #         logger.info("Singleton Google SpeechAsyncClient (STT v2) already initialized for asia-southeast1.")
    # except Exception as e:
    #     logger.error(f"Failed to initialize singleton Google SpeechAsyncClient (STT v2): {e}", exc_info=True)

    # Initialize Google Text-to-Speech Client
    # try:
    #     if tts_client_instance is None:
    #         tts_client_instance = TextToSpeechAsyncClient()
    #         logger.info("Singleton Google TextToSpeechAsyncClient (TTS) initialized successfully.")
    #     else:
    #         logger.info("Singleton Google TextToSpeechAsyncClient (TTS) already initialized.")
    # except Exception as e:
    #     logger.error(f"Failed to initialize singleton Google TextToSpeechAsyncClient (TTS): {e}", exc_info=True)

    # Initialize OpenAI Client
    try:
        if openai_client_instance is None:
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if openai_api_key:
                openai_client_instance = AsyncOpenAI(api_key=openai_api_key)
                logger.info("Singleton OpenAI AsyncClient initialized successfully.")
            else:
                logger.error("OPENAI_API_KEY not found in environment. Cannot initialize singleton OpenAI client.")
        else:
            logger.info("Singleton OpenAI AsyncClient already initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize singleton OpenAI AsyncClient: {e}", exc_info=True)

    # Initialize Soniox Client
    if SonioxSpeechClient: # Proceed only if import was successful
        try:
            if soniox_client_instance is None:
                soniox_api_key = os.getenv("SONIOX_API_KEY")
                if soniox_api_key: # Soniox SDK typically uses env var SONIOX_API_KEY automatically
                    soniox_client_instance = SonioxSpeechClient() 
                    logger.info("Singleton Soniox SpeechClient initialized successfully.")
                else:
                    logger.error("SONIOX_API_KEY not found in environment. Cannot initialize Soniox SpeechClient.")
            else:
                logger.info("Singleton Soniox SpeechClient already initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Soniox SpeechClient: {e}", exc_info=True)
    else:
        logger.error("SonioxSpeechClient class not available, Soniox client cannot be initialized.")


async def close_global_clients():
    """Placeholder for closing clients if needed during shutdown."""
    # Google gRPC clients and OpenAI's AsyncOpenAI (using HTTPX) generally manage
    # their connections and don't require explicit async close methods for basic use.
    # If specific cleanup (like await client.aclose()) becomes necessary, add it here.
    # For SpeechAsyncClient v2, an explicit close is good practice if the application has a clear shutdown phase.
    # await speech_client_instance.close()
    if soniox_client_instance:
        try:
            soniox_client_instance.close() # SpeechClient.close() is synchronous
            logger.info("Soniox SpeechClient closed successfully.")
        except Exception as e:
            logger.error(f"Error closing Soniox SpeechClient: {e}", exc_info=True)
            
    logger.info("Global clients shutdown/cleanup (if any specific actions were needed).")

# def get_speech_client() -> SpeechAsyncClient | None: # Return v2 client type
#     """Returns the singleton Google Speech-to-Text v2 client instance."""
#     if speech_client_instance is None:
#         logger.warning("Attempted to get Google STT v2 client before initialization or after failed initialization.")
#     return speech_client_instance

# def get_tts_client() -> TextToSpeechAsyncClient | None:
#     """Returns the singleton Google Text-to-Speech client instance."""
#     if tts_client_instance is None:
#         logger.warning("Attempted to get Google TTS client before initialization or after failed initialization.")
#     return tts_client_instance

def get_openai_client() -> AsyncOpenAI | None:
    """Returns the singleton OpenAI client instance."""
    if openai_client_instance is None:
        logger.warning("Attempted to get OpenAI client before initialization or after failed initialization.")
    return openai_client_instance

def get_soniox_client() -> SonioxSpeechClient | None:
    """Returns the singleton Soniox SpeechClient instance."""
    if SonioxSpeechClient and soniox_client_instance is None: # Check if class was imported
        logger.warning("Attempted to get Soniox SpeechClient before successful initialization or if class import failed.")
    elif not SonioxSpeechClient:
        logger.error("Soniox SpeechClient class was not imported successfully. Cannot get client.")
        return None
    return soniox_client_instance
