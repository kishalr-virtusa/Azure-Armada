"""Console authentication.

Microsoft Entra ID (preferred): App Service built-in auth signs users in and injects
X-MS-CLIENT-PRINCIPAL-NAME (App Service strips client-supplied X-MS-* headers when auth is on).
We only trust it when ENTRA_AUTH_ENABLED=true, set by infra/scripts/entra-auth.sh together with the auth config.
Fallback: a shared passcode with an in-memory session (when Entra is not configured).
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Request, Response, HTTPException
from fastapi.responses import RedirectResponse

from dr_agent.config import config

# Session cookie settings
COOKIE_NAME = "dra_session"
COOKIE_MAX_AGE = 60 * 60 * 24  # 24 hours

# In-memory session store (simple for hackathon)
_sessions: dict[str, datetime] = {}


def _hash_passcode(passcode: str) -> str:
    """Hash the passcode for comparison."""
    return hashlib.sha256(passcode.encode()).hexdigest()


def create_session(response: Response) -> str:
    """Create a new session and set the cookie."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = datetime.utcnow() + timedelta(seconds=COOKIE_MAX_AGE)
    
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return session_id


def entra_enabled() -> bool:
    return os.getenv("ENTRA_AUTH_ENABLED", "").lower() == "true"


def current_user(request: Request) -> str | None:
    """Signed-in Entra user (UPN), or None."""
    if not entra_enabled():
        return None
    return request.headers.get("x-ms-client-principal-name") or None


def verify_session(request: Request) -> bool:
    """Verify that the request has a valid session."""
    if entra_enabled():
        return current_user(request) is not None
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        return False
    
    expiry = _sessions.get(session_id)
    if not expiry:
        return False
    
    if datetime.utcnow() > expiry:
        del _sessions[session_id]
        return False
    
    return True


def verify_passcode(passcode: str) -> bool:
    """Verify the provided passcode."""
    if not config.console_passcode:
        # No passcode configured, allow access (dev mode)
        return True
    return passcode == config.console_passcode


def require_auth(request: Request):
    """Dependency to require authentication."""
    if not verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


def logout(response: Response) -> None:
    """Clear the session."""
    response.delete_cookie(COOKIE_NAME)
