# init_database.py
import asyncio
import logging
from app.utils.database import init_db

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def init_tables():
    """Initialize database tables"""
    try:
        await init_db()
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database tables: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(init_tables())