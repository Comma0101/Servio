import openai
import os
import time
import asyncio
from typing import List, Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv
import logging

# Load environment variables directly in this file too
load_dotenv()

# Get API key with a fallback
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("WARNING: OpenAI API key not found in environment variables!")
    # You could set a default key for testing or raise an error

# Initialize the client with explicit key
client = OpenAI(api_key=api_key)

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def create_chat_completion(
    chat_history: List[Dict[str, str]],
    model: str = "gpt-4o",
    max_tokens: int = 150,
    temperature: float = 0.9,
    top_p: float = 1,
    functions: Optional[List[Dict[str, Any]]] = None,
):
    start_time = time.time()

    try:
        # Use the new client format
        parameters = {
            "model": model,
            "messages": chat_history,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
        }
        
        # Convert functions to tools format if provided
        if functions:
            # Convert from functions to tools format for newer OpenAI models
            tools = []
            for func in functions:
                tools.append({
                    "type": "function",
                    "function": func
                })
            
            parameters["tools"] = tools
            parameters["tool_choice"] = "auto"  # Let the model decide when to call functions
        
        # Make the API call with all parameters
        completion = await asyncio.to_thread(
            lambda: client.chat.completions.create(**parameters)
        )

        end_time = time.time()
        elapsed_time = end_time - start_time
        logger.debug(f"createChatCompletion execution time: {elapsed_time} seconds")
        logger.debug(f"createChatCompletion completion: {completion.choices[0]}")
        logger.debug(f"createChatCompletion completion: {completion.choices[0].message}")
        return completion.choices[0].message

    except openai.error.OpenAIError as e:
        logger.error(f"OpenAI API Error: {str(e)}")
        logger.error(f"Request parameters: {parameters}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(f"Request parameters: {parameters}")
        return None
