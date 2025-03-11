from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import os
from dotenv import load_dotenv
from app.routers import chat, chat_response, square

# Load environment variables
load_dotenv()

# Create FastAPI app
app = FastAPI(
    title="Restaurant Voice Ordering System",
    description="A voice-based food ordering system using Twilio, OpenAI, and Square",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(chat_response.router, prefix="/api/v1", tags=["Chat Response"])
app.include_router(square.router, prefix="/api/v1", tags=["Square"])


# Root endpoint
@app.get("/", response_class=HTMLResponse)
async def root():
    return "Hello world!"


# Print available routes
@app.on_event("startup")
async def startup_event():
    print("Available routes:")
    for route in app.routes:
        print(f"Endpoint: {route.name}, Path: {route.path}")


@app.get("/test-openai")
async def test_openai():
    try:
        from app.utils.openai import create_chat_completion

        # Simple test message
        chat_history = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello!"},
        ]

        # Call the OpenAI function
        response = await create_chat_completion(
            chat_history,
            model="gpt-3.5-turbo",  # Use a smaller model for testing
            max_tokens=50,
        )

        # Return the response
        return {"status": "success", "response": response}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("FASTAPI_PORT", 8000))
    # Add this to your main.py to debug
    print(f"OpenAI API Key: {os.getenv('OPENAI_API_KEY')[:5]}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
