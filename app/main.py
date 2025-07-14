import os
import logging
import asyncio
import re
import sys
from fastapi import FastAPI
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# Configure logging (Basic setup, details will be in log_config.yaml)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration is now managed by app.config.settings
from app.config import settings

from app.core.clients import initialize_global_clients, close_global_clients
import json # For test endpoint
import random # For test endpoint
from typing import List # For response_model
from fastapi import HTTPException # For test endpoint
from app.utils.thirty_nine_miles import find_dish_by_chinese_name, get_extracted_dishes # Simplified imports
from app.constants import THIRTY_NINE_MILES_PORTAL_ID_TAKEOUT # Import for test endpoint

# Define lifespan event handler (recommended approach in FastAPI)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize resources
    logger.info("Starting Servio Voice Agent API")

    # Initialize global API clients
    # await initialize_global_clients()
    
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
        if not await init_database():
            logger.error("Database initialization failed. Please check database logs for details.")
            raise RuntimeError("Database initialization failed.")
        logger.info("Database initialized successfully")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Database initialization error: {e}", exc_info=True)
        # Re-raise the exception to prevent the application from starting
        raise
    
    # Log server startup
    logger.info(f"Servio Voice Agent API server ready on {settings.HOST}:{settings.PORT}")
    
    yield
    
    # Shutdown: Clean up resources
    logger.info("Shutting down Servio Voice Agent API")
    # await close_global_clients()

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
from app.api.endpoints import db_router, menu_router # Renamed miles_router to menu_router

app.include_router(api_router)
app.include_router(websocket_router)
app.include_router(db_router)
app.include_router(menu_router) # Renamed miles_router to menu_router

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

@app.get("/test-extract-chinese-names/{portal_id}", response_model=List[str])
async def test_extract_chinese_names_endpoint(portal_id: str):
    """
    Test endpoint to extract all Chinese dish names for a given portal_id.
    Uses the cached menu data via get_extracted_dishes.
    """
    logger.info(f"Test endpoint /test-extract-chinese-names/{portal_id} hit.")
    
    response = await get_extracted_dishes(portal_id)
    
    if response and response.get("success"):
        dishes = response.get("data", [])
        if dishes:
            chinese_names = []
            for dish in dishes:
                chinese_name = dish.get("dish_name_zh")
                if chinese_name:
                    chinese_names.append(chinese_name)
            
            if not chinese_names:
                 logger.info(f"No Chinese dish names found in the extracted data for portal ID {portal_id}, though dishes were present.")
                 return [] # Return empty list if dishes are there but no chinese names

            logger.info(f"Successfully extracted {len(chinese_names)} Chinese dish names for portal ID {portal_id}.")
            return chinese_names
        else:
            logger.info(f"No dishes found in the extracted data for portal ID {portal_id}.")
            return [] # Return empty list if no dishes
    else:
        error_message = response.get("message", "Unknown error") if response else "No response from get_extracted_dishes"
        logger.error(f"Failed to get extracted dishes for portal ID {portal_id}. Error: {error_message}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to get extracted dishes for portal ID {portal_id}: {error_message}"
        )

@app.get("/test-extract-chinese-categories/{portal_id}", response_model=List[str])
async def test_extract_chinese_categories_endpoint(portal_id: str):
    """
    Test endpoint to extract and clean all unique Chinese category names for a given portal_id.
    """
    logger.info(f"Test endpoint /test-extract-chinese-categories/{portal_id} hit.")
    
    response = await get_extracted_dishes(portal_id)
    
    if not (response and response.get("success")):
        error_message = response.get("message", "Unknown error") if response else "No response from get_extracted_dishes"
        logger.error(f"Failed to get extracted dishes for portal ID {portal_id} (for categories). Error: {error_message}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to get extracted dishes for portal ID {portal_id} (for categories): {error_message}"
        )

    dishes = response.get("data", [])
    if not dishes:
        logger.info(f"No dishes found for portal ID {portal_id}, so no categories to extract.")
        return []

    # Use a set to automatically handle uniqueness
    unique_category_names = set()
    for dish in dishes:
        category_name = dish.get("category_name_zh")
        if category_name:
            unique_category_names.add(category_name.strip())
    
    if not unique_category_names:
         logger.info(f"No Chinese category names found for portal ID {portal_id}.")
         return []

    # Define the noise filter regex
    noise_re = re.compile(r'秒杀|免费|测试|退款|规定时间|特定时间|周[一二三四五六日]')
    
    # Filter the categories
    cleaned_categories = [
        name for name in unique_category_names if not noise_re.search(name)
    ]

    sorted_categories = sorted(cleaned_categories)
    logger.info(f"Successfully extracted and cleaned {len(sorted_categories)} unique Chinese category names for portal ID {portal_id}.")
    return sorted_categories

@app.get("/test-extract-english-names/{portal_id}", response_model=List[str])
async def test_extract_english_names_endpoint(portal_id: str):
    """
    Test endpoint to extract all English dish names for a given portal_id.
    Uses the cached menu data via get_extracted_dishes.
    """
    logger.info(f"Test endpoint /test-extract-english-names/{portal_id} hit.")
    
    response = await get_extracted_dishes(portal_id)
    
    if response and response.get("success"):
        dishes = response.get("data", [])
        if dishes:
            english_names = []
            for dish in dishes:
                english_name = dish.get("dish_name_en")
                if english_name:
                    english_names.append(english_name)
            
            if not english_names:
                 logger.info(f"No English dish names found in the extracted data for portal ID {portal_id}, though dishes were present.")
                 return []

            logger.info(f"Successfully extracted {len(english_names)} English dish names for portal ID {portal_id}.")
            return english_names
        else:
            logger.info(f"No dishes found in the extracted data for portal ID {portal_id}.")
            return []
    else:
        error_message = response.get("message", "Unknown error") if response else "No response from get_extracted_dishes"
        logger.error(f"Failed to get extracted dishes for portal ID {portal_id} (for English names). Error: {error_message}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to get extracted dishes for portal ID {portal_id} (for English names): {error_message}"
        )

@app.get("/test-find-dish/{portal_id}/{chinese_name}")
async def test_find_dish_endpoint(portal_id: str, chinese_name: str):
    """
    Test endpoint to find a dish by its Chinese name for a given portal_id.
    Example usage: /test-find-dish/your_portal_id/宫保鸡丁
    (Remember to URL-encode the Chinese name if calling from a browser or some tools)
    """
    logger.info(f"Test endpoint /test-find-dish/{portal_id}/{chinese_name} hit.")
    
    # Ensure app.constants has THIRTY_NINE_MILES_BASE_URL, 
    # THIRTY_NINE_MILES_CLIENT_ID, and THIRTY_NINE_MILES_CLIENT_SEC defined.
    # These are used by find_dish_by_chinese_name indirectly via fetch_token.

    found_dishes = await find_dish_by_chinese_name(portal_id, chinese_name)

    if found_dishes: # Check if the list is not empty
        logger.info(f"{len(found_dishes)} dish(es) matching '{chinese_name}' found in portal '{portal_id}': {found_dishes}")
        return {
            "status": "found",
            "portal_id": portal_id,
            "chinese_name_searched": chinese_name,
            "matched_dishes": found_dishes # Return the list of dishes
        }
    else:
        logger.warning(f"Dish '{chinese_name}' not found in portal '{portal_id}'.")
        raise HTTPException(
            status_code=404, 
            detail=f"Dish '{chinese_name}' not found in portal '{portal_id}'. No exact or partial matches found. Check spelling, portal ID, or API/cache status."
        )

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
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
        log_config="log_config.yaml" # Use the config file
    )
