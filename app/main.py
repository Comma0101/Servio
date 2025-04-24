import os
import logging
import asyncio
import sys
from fastapi import FastAPI
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# Configure logging (Basic setup, details will be in log_config.yaml)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database connection parameters from environment variables
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "servio")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

# Define lifespan event handler (recommended approach in FastAPI)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize resources
    logger.info("Starting Servio Voice Agent API")
    
    # Configure custom exception handler for CancelledError
    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler() or loop.default_exception_handler
    
    def custom_exception_handler(loop, context):
        exception = context.get('exception')
        if isinstance(exception, asyncio.CancelledError):
            # Silently ignore CancelledError exceptions
            logger.debug("Gracefully handling asyncio.CancelledError")
            # Critical: Do NOT call cancel() on other tasks here!
            return
        # For all other exceptions, use the original handler
        original_handler(loop, context)
    
    loop.set_exception_handler(custom_exception_handler)
    
    # Check for required credentials
    if not os.getenv('TWILIO_ACCOUNT_SID') or not os.getenv('TWILIO_AUTH_TOKEN'):
        logger.warning("Twilio credentials missing or incomplete. Functions requiring API access will fail.")
    
    if not os.getenv('DEEPGRAM_API_KEY'):
        logger.warning("Deepgram API key missing. Voice agent functionality will not work.")
    
    # Initialize database connections here if needed
    try:
        from app.services.database_service import init_database
        await init_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
    
    # Log server startup
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5050))
    logger.info(f"Servio Voice Agent API server ready on {host}:{port}")
    
    yield
    
    # Shutdown: Clean up resources
    logger.info("Shutting down Servio Voice Agent API")

# Create FastAPI app with lifespan manager
app = FastAPI(
    title="Servio Voice Agent API",
    description="API for handling Twilio calls and Deepgram voice agent integration",
    version="1.0.0",
    lifespan=lifespan
)

# Import and include routers
from app.api.endpoints import router as api_router
from app.api.websocket import router as websocket_router
from app.api.endpoints import db_router

app.include_router(api_router)
app.include_router(websocket_router)
app.include_router(db_router)

# Add a root endpoint
@app.get("/")
async def root():
    """Root endpoint that confirms the API is running"""
    return {
        "status": "online",
        "service": "Servio Voice Agent API",
        "version": "1.0.0"
    }
@app.get("/test")
def test_endpoint():
    return {"status": "ok"}
# Debug endpoint for checking utterances
@app.get("/utterances")
async def get_utterances():
    """Debug endpoint to list recent utterances"""
    from app.services.database_service import get_recent_utterances
    utterances = await get_recent_utterances(10)  # Get the 10 most recent
    return {"utterances": utterances}

if __name__ == "__main__":
    import uvicorn
    import signal
    import sys
    
    def handle_exit(signum, frame):
        """Handle exit signals more gracefully"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        
        # Critical: Don't force exit immediately, allow active calls to complete
        # Set should_exit but not force_exit to allow graceful shutdown
        if 'server' in globals():
            server.should_exit = True
            # Don't set force_exit=True to give active WebSockets time to clean up
        
        # Let the normal signal handling continue
        sys.exit(0)
    
    # Register signal handlers (primarily for the main reloader process)
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    uvicorn.run(
        "app.main:app", 
        host="0.0.0.0", 
        port=int(os.getenv("FASTAPI_PORT", 5050)),
        reload=True,
        log_config="log_config.yaml" # Use the config file
    )
