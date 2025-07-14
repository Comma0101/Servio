"""
Database Service - Handle database operations for call tracking and utterances
"""
import asyncpg
import logging
import os
import datetime
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional
import time

from app.config import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Connection pool
_pool = None

async def get_db_pool():
    """Get or create a database connection pool"""
    global _pool
    if _pool is None:
        logger.info(f"Creating database connection pool to {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
        _pool = await asyncpg.create_pool(
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.DB_NAME,
            host=settings.DB_HOST,
            port=settings.DB_PORT
        )
    return _pool

async def init_database():
    """Initialize the database tables"""
    try:
        logger.info(f"Connecting to database {settings.DB_NAME} at {settings.DB_HOST}:{settings.DB_PORT}")
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            # Create calls table if it doesn't exist
            logger.info("Creating 'calls' table if it doesn't exist...")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    call_sid TEXT UNIQUE NOT NULL,
                    caller_phone TEXT,
                    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    end_time TIMESTAMP,
                    audio_url TEXT,
                    order_id TEXT,
                    metadata JSONB
                )
            ''')
            
            # Check if caller_phone column exists and add it if it doesn't
            logger.info("Ensuring caller_phone column exists in calls table...")
            column_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'calls' AND column_name = 'caller_phone'
                )
            ''')
            
            if not column_exists:
                logger.info("Adding caller_phone column to calls table...")
                await conn.execute('''
                    ALTER TABLE calls ADD COLUMN caller_phone TEXT
                ''')
                logger.info("caller_phone column added successfully")
            else:
                logger.info("caller_phone column already exists")
            
            # Create utterances table if it doesn't exist
            logger.info("Creating 'utterances' table if it doesn't exist...")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS utterances (
                    id SERIAL PRIMARY KEY,
                    call_sid TEXT REFERENCES calls(call_sid),
                    speaker TEXT NOT NULL,
                    text TEXT NOT NULL,
                    confidence FLOAT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Check if text column exists in utterances table and add it if it doesn't
            logger.info("Ensuring text column exists in utterances table...")
            text_column_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'utterances' AND column_name = 'text'
                )
            ''')
            
            if not text_column_exists:
                logger.info("text column does not exist, adding it...")
                await conn.execute('''
                    ALTER TABLE utterances 
                    ADD COLUMN text TEXT
                ''')
                logger.info("text column added successfully")
            else:
                logger.info("text column already exists")
            
            # List available tables for verification
            tables = await conn.fetch('''
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
            ''')
            table_names = [table['table_name'] for table in tables]
            logger.info(f"Available tables: {table_names}")
            
        logger.info("Database tables created successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database: {e}", exc_info=True)
        return False

async def save_call_start(call_sid: str, caller_phone: str):
    """Save call start information to the database"""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO calls (call_sid, caller_phone)
                VALUES ($1, $2)
                ON CONFLICT (call_sid) DO UPDATE
                SET caller_phone = $2
            ''', call_sid, caller_phone)
        logger.info(f"Saved call start: {call_sid}")
        return True
    except Exception as e:
        logger.error(f"Error saving call start: {e}")
        return False

async def save_call_end(call_sid: str, audio_url: Optional[str] = None):
    """Save call end information to the database"""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            if audio_url:
                await conn.execute('''
                    UPDATE calls
                    SET end_time = CURRENT_TIMESTAMP, audio_url = $2
                    WHERE call_sid = $1
                ''', call_sid, audio_url)
            else:
                await conn.execute('''
                    UPDATE calls
                    SET end_time = CURRENT_TIMESTAMP
                    WHERE call_sid = $1
                ''', call_sid)
        logger.info(f"Saved call end: {call_sid}")
        return True
    except Exception as e:
        logger.error(f"Error saving call end: {e}")
        return False

async def save_utterance(call_sid: str, speaker: str, text: str, confidence: float = 1.0, language: str = None):
    """Save an utterance to the database"""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Check if the text column exists
            column_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'utterances' AND column_name = 'text'
                )
            ''')
            
            if not column_exists:
                # Use the 'content' column instead if 'text' doesn't exist
                # or any other column that actually exists in the table
                await conn.execute('''
                    INSERT INTO utterances (call_sid, speaker, confidence)
                    VALUES ($1, $2, $3)
                ''', call_sid, speaker, confidence)
                logger.info(f"Saved utterance without text content: [{speaker}]")
            else:
                # Use the original query if the text column exists
                await conn.execute('''
                    INSERT INTO utterances (call_sid, speaker, text, confidence)
                    VALUES ($1, $2, $3, $4)
                ''', call_sid, speaker, text, confidence)
                logger.info(f"Saved utterance: [{speaker}] {text[:30]}{'...' if len(text) > 30 else ''}")
        return True
    except Exception as e:
        logger.error(f"Error saving utterance for call {call_sid}: {e}")
        return False

async def save_order_details(call_sid: str, items: List[Dict[str, Any]], total_price: float, summary_status: str):
    """Save order details associated with a call by updating the calls table."""
    pool = await get_db_pool()
    order_id = f"ORD-{call_sid[:12]}-{int(time.time())}" # Generate a unique order ID
    
    order_metadata = {
        "items": items,
        "total_price": total_price,
        "status": summary_status, # e.g., "IN PROGRESS", "DONE", "CANCELLED"
        "last_updated": datetime.datetime.now().isoformat()
    }
    
    try:
        async with pool.acquire() as conn:
            # This assumes the 'calls' table has 'order_id' (TEXT) and 'metadata' (JSONB) columns
            # as defined in app/utils/database.py's init_db function.
            
            # Serialize metadata to JSON string for asyncpg
            import json
            json_metadata = json.dumps(order_metadata)
            
            await conn.execute('''
                UPDATE calls
                SET order_id = $1, metadata = $2
                WHERE call_sid = $3
            ''', order_id, json_metadata, call_sid)
            logger.info(f"Saved order details for call {call_sid} with order_id {order_id}. Metadata: {json_metadata}")
            return order_id
    except Exception as e:
        logger.error(f"Error saving order details for call {call_sid}: {e}")
        return None

async def update_order_payment_status(order_id: str, status: str, square_order_id: Optional[str], square_payment_id: Optional[str]):
    """Update the payment status and Square IDs for an order."""
    try:
        logger.info(f"Updating payment status for order {order_id}: Status={status}, SquareOrderID={square_order_id}, SquarePaymentID={square_payment_id}")
        # TODO: Implement database logic to update the order record.
        # Example: Connect to DB, UPDATE orders table SET status=?, square_order_id=?, square_payment_id=? WHERE order_id=?
        pass # Placeholder - just log for now
    except Exception as e:
        logger.error(f"Error updating payment status for order {order_id}: {e}")

async def get_recent_utterances(limit: int = 20) -> List[Dict[str, Any]]:
    """Get the most recent utterances from all calls"""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Check if the 'text' column exists in the utterances table
            text_column_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'utterances' AND column_name = 'text'
                )
            """)

            if text_column_exists:
                # If 'text' column exists, select it
                query = """
                    SELECT u.id, u.call_sid, u.speaker, u.text, u.confidence, u.timestamp, c.caller_phone
                    FROM utterances u
                    JOIN calls c ON u.call_sid = c.call_sid
                    ORDER BY u.timestamp DESC
                    LIMIT $1
                """
            else:
                # If 'text' column does NOT exist, select NULL as text
                logger.warning("Column 'text' not found in 'utterances' table. Selecting NULL instead.")
                query = """
                    SELECT u.id, u.call_sid, u.speaker, NULL AS text, u.confidence, u.timestamp, c.caller_phone
                    FROM utterances u
                    JOIN calls c ON u.call_sid = c.call_sid
                    ORDER BY u.timestamp DESC
                    LIMIT $1
                """
            
            rows = await conn.fetch(query, limit)
            
            # Convert rows to dictionaries for JSON serialization
            utterances = []
            for row in rows:
                utterances.append({
                    "id": row["id"],
                    "call_sid": row["call_sid"],
                    "speaker": row["speaker"],
                    "text": row["text"],  # This will now be NULL if the column didn't exist
                    "confidence": row["confidence"],
                    "timestamp": row["timestamp"].isoformat(),
                    "caller_phone": row["caller_phone"]
                })
            
            return utterances
    except Exception as e:
        logger.error(f"Error fetching recent utterances: {e}")
        return []

async def get_call_utterances(call_sid: str) -> List[Dict[str, Any]]:
    """Get all utterances for a specific call"""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT id, speaker, text, confidence, timestamp
                FROM utterances
                WHERE call_sid = $1
                ORDER BY timestamp ASC
            ''', call_sid)
            
            # Convert rows to dictionaries for JSON serialization
            utterances = []
            for row in rows:
                utterances.append({
                    "id": row["id"],
                    "speaker": row["speaker"],
                    "text": row["text"],
                    "confidence": row["confidence"],
                    "timestamp": row["timestamp"].isoformat()
                })
            
            return utterances
    except Exception as e:
        logger.error(f"Error fetching call utterances: {e}")
        return []

async def get_call_details(call_sid: str) -> Optional[Dict[str, Any]]:
    """Get details for a specific call"""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Ensure we select order_id and metadata, assuming these columns exist
            # as per app/utils/database.py's init_db schema
            row = await conn.fetchrow('''
                SELECT id, call_sid, caller_phone, start_time, end_time, audio_url, order_id, metadata
                FROM calls
                WHERE call_sid = $1
            ''', call_sid)
            
            if row:
                return {
                    "id": row["id"],
                    "call_sid": row["call_sid"],
                    "caller_phone": row["caller_phone"],
                    "start_time": row["start_time"].isoformat() if row["start_time"] else None,
                    "end_time": row["end_time"].isoformat() if row["end_time"] else None,
                    "audio_url": row["audio_url"],
                    "duration_seconds": (row["end_time"] - row["start_time"]).total_seconds() if row["end_time"] and row["start_time"] else None,
                    "order_id": row["order_id"],
                    "order_details": row["metadata"] # 'metadata' column from DB contains the order content (items, price, status)
                }
            return None
    except Exception as e:
        logger.error(f"Error fetching call details: {e}")
        return None

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
