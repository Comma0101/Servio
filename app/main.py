from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import os
from dotenv import load_dotenv
from app.routers import chat, chat_response, square, test
from app.middleware.session import SessionMiddleware

print("Starting application...")  # This should always print

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

# Add session middleware
app.add_middleware(SessionMiddleware)

# Include routers
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(chat_response.router, prefix="/api/v1", tags=["Chat Response"])
app.include_router(square.router, prefix="/api/v1", tags=["Square"])
app.include_router(test.router, tags=["Testing"])

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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("FASTAPI_PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)