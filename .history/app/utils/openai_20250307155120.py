import openai
import os
import time
import asyncio
from typing import List, Dict, Any, Optional

# Load the API key from environment variables


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
        # Use asyncio to make the API call non-blocking
        completion = await asyncio.to_thread(
            openai.ChatCompletion.create,
            messages=chat_history,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            functions=functions,
            function_call="auto" if functions else None,
        )

        end_time = time.time()
        elapsed_time = end_time - start_time
        print(f"createChatCompletion execution time: {elapsed_time} seconds")
        print(f"createChatCompletion completion: {completion.choices[0]}")
        print(f"createChatCompletion completion: {completion.choices[0].message}")
        return completion.choices[0].message

    except Exception as e:
        print(f"Error in create_chat_completion: {e}")
        return None
