# app/config.py
import os
from typing import Optional, List, Dict, Any
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load .env file explicitly to make sure environment variables are available
# Pydantic's BaseSettings will then pick them up
load_dotenv()

# Load constants from app.constants if needed for defaults or structure
# Adjust this import based on your actual project structure
try:
    from app.constants import CONSTANTS
    RESTAURANT_ID = "LIMF" # Assuming LIMF is the default or only restaurant for now
    DEFAULT_RESTAURANT_CONFIG = CONSTANTS.get(RESTAURANT_ID, {})
    DEFAULT_SYSTEM_MESSAGE = DEFAULT_RESTAURANT_CONFIG.get("SYSTEM_MESSAGE", "Default system message")
    DEFAULT_TWILIO_VOICE = DEFAULT_RESTAURANT_CONFIG.get("TWILIO_VOICE", "Polly.Joanna-Neural")
    DEFAULT_MENU_JSON = DEFAULT_RESTAURANT_CONFIG.get("MENU", "[]")
    DEFAULT_OPENAI_TOOLS = DEFAULT_RESTAURANT_CONFIG.get("OPENAI_CHAT_TOOLS", [])
    DEFAULT_TAX_RATE = DEFAULT_RESTAURANT_CONFIG.get("TAX", 0.0)
except ImportError:
    print("Warning: app.constants not found or RESTAURANT_CONFIG structure mismatch. Using hardcoded defaults.")
    DEFAULT_SYSTEM_MESSAGE = "Default system message"
    DEFAULT_TWILIO_VOICE = "Polly.Joanna-Neural"
    DEFAULT_MENU_JSON = "[]"
    DEFAULT_OPENAI_TOOLS = []
    DEFAULT_TAX_RATE = 0.0


class Settings(BaseSettings):
    """Application Configuration Settings"""

    # Server Configuration
    PORT: int = int(os.getenv("PORT", 5050)) # Keep direct os.getenv here for port binding flexibility if needed early
    HOST: str = "0.0.0.0"

    # Deepgram Configuration
    DEEPGRAM_API_KEY: str

    # Twilio Configuration (Ensure these are in your .env)
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None # Used for sending SMS

    # Database Configuration
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "servio"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    # Construct DATABASE_URL for asyncpg pool
    DATABASE_URL: str = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    # Square Configuration (Ensure these are in your .env)
    SQUARE_ACCESS_TOKEN: Optional[str] = None
    SQUARE_LOCATION_ID: Optional[str] = None
    SQUARE_ENVIRONMENT: str = "sandbox" # Or "production"
    # Move hardcoded test nonce here
    SQUARE_TEST_NONCE: str = "cnon:card-nonce-ok"

    # AWS S3 Configuration (Ensure these are in your .env for audio uploads)
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: Optional[str] = None
    S3_BUCKET_NAME: Optional[str] = None

    # Restaurant/Agent Specific Configuration (Loading defaults from constants.py logic above)
    RESTAURANT_SYSTEM_MESSAGE: str = DEFAULT_SYSTEM_MESSAGE
    RESTAURANT_TWILIO_VOICE: str = DEFAULT_TWILIO_VOICE
    RESTAURANT_MENU_JSON: str = DEFAULT_MENU_JSON # Store as JSON string
    RESTAURANT_OPENAI_TOOLS: List[Dict[str, Any]] = DEFAULT_OPENAI_TOOLS
    RESTAURANT_TAX_RATE: float = DEFAULT_TAX_RATE

    # Other Configuration
    FALLBACK_CALLER_ID: str = "+18005551234" # Moved hardcoded fallback here
    AUDIO_BUFFER_SIZE_MS: int = 20 # milliseconds per Twilio chunk
    AUDIO_SAMPLE_RATE: int = 8000 # Hz
    AUDIO_SEND_INTERVAL_MS: int = 400 # How much audio (ms) to buffer before sending to Deepgram
    # Calculated buffer size in bytes (Mulaw = 1 byte per sample)
    AUDIO_BUFFER_BYTES: int = int(AUDIO_SEND_INTERVAL_MS / 1000 * AUDIO_SAMPLE_RATE)

    class Config:
        # This tells Pydantic to load variables from a .env file
        env_file = '.env'
        env_file_encoding = 'utf-8'
        extra = 'ignore' # Ignore extra fields in .env

# Create a single instance to be imported by other modules
settings = Settings()
