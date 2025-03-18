import redis
import json
import os
from datetime import timedelta

# Initialize Redis client - defaults to localhost if environment variables aren't set
redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    password=os.getenv('REDIS_PASSWORD', None),
    decode_responses=True
)

def store_chat_history(call_sid, chat_history, expire_seconds=3600):
    """Store chat history in Redis with expiration"""
    try:
        redis_client.setex(
            f"chat_history:{call_sid}", 
            timedelta(seconds=expire_seconds),
            json.dumps(chat_history)
        )
        print(f"[REDIS] Stored chat history for call SID: {call_sid}")
        return True
    except Exception as e:
        print(f"[REDIS] Error storing chat history: {e}")
        return False

def get_chat_history(call_sid):
    """Get chat history from Redis"""
    try:
        chat_history_json = redis_client.get(f"chat_history:{call_sid}")
        if chat_history_json:
            print(f"[REDIS] Retrieved chat history for call SID: {call_sid}")
            return json.loads(chat_history_json)
        print(f"[REDIS] No chat history found for call SID: {call_sid}")
        return None
    except Exception as e:
        print(f"[REDIS] Error retrieving chat history: {e}")
        return None

def clear_chat_history(call_sid):
    """Clear chat history from Redis"""
    try:
        redis_client.delete(f"chat_history:{call_sid}")
        print(f"[REDIS] Cleared chat history for call SID: {call_sid}")
        return True
    except Exception as e:
        print(f"[REDIS] Error clearing chat history: {e}")
        return False
