import openai
import os
import time
import asyncio
from typing import List, Dict, Any, Optional
from openai import OpenAI

# Load environment variables directly in this file too
load_dotenv()

# Get API key with a fallback
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("WARNING: OpenAI API key not found in environment variables!")
    # You could set a default key for testing or raise an error

# Initialize the client with explicit key
client = OpenAI(api_key=api_key)


async def create_chat_completion(
    chat_history: List[Dict[str, str]],
    model: str = "gpt-3.5-turbo",
    max_tokens: int = 150,
    temperature: float = 0.9,
    top_p: float = 1,
    functions: Optional[List[Dict[str, Any]]] = None,
):
    start_time = time.time()

    try:
        # Use the new client format
        completion = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=model,
                messages=chat_history,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
        )

        end_time = time.time()
        elapsed_time = end_time - start_time
        print(f"createChatCompletion execution time: {elapsed_time} seconds")
        print(f"createChatCompletion completion: {completion.choices[0]}")
        print(f"createChatCompletion completion: {completion.choices[0].message}")
        return completion.choices[0].message

    except Exception as e:
        print(f"OpenAI API Error: {str(e)}")
        return None
