from fastapi import Request, Response
import json
from typing import Dict, Any
import base64
from starlette.middleware.base import BaseHTTPMiddleware


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Get session from cookie
        session_cookie = request.cookies.get("session")
        session_data = {}

        if session_cookie:
            try:
                decoded = base64.b64decode(session_cookie.encode()).decode()
                session_data = json.loads(decoded)
            except:
                session_data = {}

        # Add session to request state
        request.state.session = session_data
        request.state.session_modified = False

        # Process request
        response = await call_next(request)

        # Update session cookie if needed
        if hasattr(request.state, "session_modified") and request.state.session_modified:
            encoded = base64.b64encode(
                json.dumps(request.state.session).encode()
            ).decode()
            response.set_cookie(key="session", value=encoded, httponly=True)

        return response


# Helper functions to use in route handlers
def get_session(request: Request) -> Dict[str, Any]:
    return request.state.session


def set_session(request: Request, key: str, value: Any):
    request.state.session[key] = value
    request.state.session_modified = True