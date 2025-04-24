import os
import json
import asyncpg
import boto3
from datetime import datetime
from dotenv import load_dotenv
import logging
import wave

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# PostgreSQL connection settings
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "servio")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")

# S3 settings
S3_BUCKET = os.environ.get("S3_BUCKET", "servioaudio")
AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")

# Create S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

# Global connection pool
db_pool = None

async def init_db():
    """Initialize database connection and create tables if they don't exist"""
    global db_pool
    
    # If we already have a connection pool, return it
    if db_pool is not None:
        return db_pool
        
    try:
        # Create a connection pool with limited connections
        logger.info(f"Connecting to database {DB_NAME} at {DB_HOST}:{DB_PORT}")
        db_pool = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            host=DB_HOST,
            port=DB_PORT,
            min_size=2,        # Minimum number of connections
            max_size=10         # Maximum number of connections
        )
        
        # Create tables if they don't exist
        async with db_pool.acquire() as conn:
            logger.info("Creating 'calls' table...")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    call_sid VARCHAR(255) UNIQUE NOT NULL,
                    caller_number VARCHAR(20),
                    start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    end_time TIMESTAMP WITH TIME ZONE,
                    audio_url TEXT,
                    call_summary TEXT,
                    status VARCHAR(50),
                    order_id VARCHAR(255),
                    metadata JSONB
                )
            ''')
            
            logger.info("Creating 'utterances' table...")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS utterances (
                    id SERIAL PRIMARY KEY,
                    call_sid VARCHAR(255) REFERENCES calls(call_sid),
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    speaker VARCHAR(20),
                    transcript TEXT,
                    confidence NUMERIC(5,4)
                )
            ''')
            
            # Check if tables were created
            tables = await conn.fetch('''
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema='public'
            ''')
            
            logger.info(f"Available tables: {[table['table_name'] for table in tables]}")
            logger.info("Database tables created successfully")
        
        return db_pool
    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}")
        raise

async def save_call_start(call_sid, caller_number):
    """Save call start information to database"""
    try:
        pool = await init_db()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO calls (call_sid, caller_number, start_time, status)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (call_sid) DO UPDATE 
                SET caller_number = $2, start_time = $3, status = $4
            ''', call_sid, caller_number, datetime.now(), 'in_progress')
            
            logger.info(f"Call start recorded for SID: {call_sid}")
    except Exception as e:
        logger.error(f"Error saving call start: {str(e)}")

async def save_utterance(call_sid, speaker, transcript, confidence=None):
    """Save a transcription utterance to database"""
    try:
        pool = await init_db()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO utterances (call_sid, timestamp, speaker, transcript, confidence)
                VALUES ($1, $2, $3, $4, $5)
            ''', call_sid, datetime.now(), speaker, transcript, confidence)
            
            logger.info(f"Utterance saved for call SID: {call_sid}")
    except Exception as e:
        logger.error(f"Error saving utterance: {str(e)}")

async def save_call_end(call_sid, call_summary=None, order_id=None, audio_url=None):
    """Save call end information to database"""
    try:
        global db_pool
        if db_pool is None:
            db_pool = await init_db()
        async with db_pool.acquire() as conn:
            # Update only the provided fields
            query = '''
                UPDATE calls 
                SET end_time = $1, status = $2
            '''
            
            params = [datetime.now(), 'completed']
            param_idx = 3
            
            # Add optional parameters only if provided
            if call_summary is not None:
                query += f", call_summary = ${param_idx}"
                params.append(call_summary)
                param_idx += 1
                
            if order_id is not None:
                query += f", order_id = ${param_idx}"
                params.append(order_id)
                param_idx += 1
                
            if audio_url is not None:
                query += f", audio_url = ${param_idx}"
                params.append(audio_url)
                param_idx += 1
                
            query += " WHERE call_sid = $" + str(param_idx)
            params.append(call_sid)
            
            await conn.execute(query, *params)
            
            logger.info(f"Call end recorded for SID: {call_sid}")
            return True
    except Exception as e:
        logger.error(f"Error saving call end: {str(e)}")
        return False

async def upload_audio_to_s3(call_sid, audio_data):
    """Upload audio data to S3 bucket"""
    try:
        logger.info(f"Starting audio upload to S3 for call_sid: {call_sid}, data size: {len(audio_data)} bytes")
        
        # Generate a unique filename based on call_sid
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        file_name = f"{call_sid}_{timestamp}.wav"
        logger.info(f"Generated S3 filename: {file_name}")
        
        logger.info(f"Using S3 bucket: {S3_BUCKET}, AWS region: {AWS_REGION}")
        logger.info(f"AWS credentials: Access key first/last 4 chars: {AWS_ACCESS_KEY[:4]}...{AWS_ACCESS_KEY[-4:] if AWS_ACCESS_KEY else 'None'}")
        
        # Twilio audio is 8kHz μ-law PCM (mulaw)
        # We need to add a proper WAV header for playback compatibility
        # For raw audio data, we'll convert it to a proper WAV file format
        try:
            # Create a temporary WAV file with proper headers
            temp_wav_file = f"/tmp/{file_name}"
            with wave.open(temp_wav_file, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(1)  # 8-bit
                wav_file.setframerate(8000)  # 8kHz
                wav_file.writeframes(audio_data)
            
            # Read the WAV file with proper headers
            with open(temp_wav_file, 'rb') as f:
                wav_data = f.read()
            
            logger.info(f"Created WAV file with proper headers, size: {len(wav_data)} bytes")
            
            # Clean up temporary file
            os.remove(temp_wav_file)
        except Exception as wav_error:
            logger.error(f"Error creating WAV file: {str(wav_error)}")
            logger.error(f"Falling back to raw audio data")
            wav_data = audio_data
        
        # Initialize metadata for the recording
        metadata = {
            'call_sid': call_sid,
            'format': 'mulaw',
            'sample_rate': '8000',
            'channels': '1',
            'recorded_at': timestamp,
            'source': 'twilio'
        }
        
        # Initialize S3 client
        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                region_name=AWS_REGION
            )
            
            # Upload to S3
            logger.info(f"Uploading to S3: bucket={S3_BUCKET}, key={file_name}")
            s3_client.put_object(
                Body=wav_data,
                Bucket=S3_BUCKET,
                Key=file_name,
                ContentType='audio/wav',
                Metadata=metadata
            )
            
            # Get the URL for the uploaded file
            audio_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{file_name}"
            logger.info(f"Audio successfully uploaded to S3: {audio_url}")
            
            # Verify if the file exists before returning the URL
            try:
                # Check if the object exists in the bucket
                s3_client.head_object(Bucket=S3_BUCKET, Key=file_name)
                logger.info(f"Verified S3 object exists: s3://{S3_BUCKET}/{file_name}")
            except Exception as verify_error:
                logger.warning(f"Could not verify S3 object: {str(verify_error)}")
                # Continue anyway since the put_object didn't raise an exception
            
            return audio_url
        except boto3.exceptions.S3UploadFailedError as s3_error:
            logger.error(f"S3 upload failed: {str(s3_error)}")
            import traceback
            logger.error(f"S3 traceback: {traceback.format_exc()}")
            return None
            
    except Exception as e:
        logger.error(f"Error uploading audio to S3: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None

async def get_call_transcript(call_sid):
    """Get the complete transcript for a call"""
    try:
        pool = await init_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT speaker, transcript, timestamp
                FROM utterances
                WHERE call_sid = $1
                ORDER BY timestamp ASC
            ''', call_sid)
            
            transcript = []
            for row in rows:
                transcript.append({
                    "speaker": row['speaker'],
                    "text": row['transcript'],
                    "timestamp": row['timestamp'].isoformat()
                })
                
            return transcript
    except Exception as e:
        logger.error(f"Error retrieving call transcript: {str(e)}")
        return []