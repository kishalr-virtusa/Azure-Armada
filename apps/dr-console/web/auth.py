"""Console authentication.

Microsoft Entra ID (preferred): App Service built-in auth signs users in and injects
X-MS-CLIENT-PRINCIPAL-NAME (App Service strips client-supplied X-MS-* headers when auth is on).
We only trust it when ENTRA_AUTH_ENABLED=true, set by infra/scripts/entra-auth.sh together with the auth config.
Fallback: a shared passcode with an in-memory session (when Entra is not configured).
"""

import hashlib
import hmac
import threading
import time
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

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
    """Both our flag AND App Service's own auth flag must be on before the identity header is trusted."""
    return (os.getenv("ENTRA_AUTH_ENABLED", "").lower() == "true"
            and os.getenv("WEBSITE_AUTH_ENABLED", "").lower() == "true")


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
    """Verify the provided passcode (timing-safe). With no passcode configured, refuse unless explicitly in dev mode."""
    if not config.console_passcode:
        return os.environ.get("DR_DEV_MODE", "").lower() == "true"
    return hmac.compare_digest(passcode.encode(), config.console_passcode.encode())


# Login throttling: 5 failures per address in 10 minutes locks that address for 10 minutes; 30 failures from
# anywhere locks the form for 5 minutes. Brute-forcing the passcode becomes impractical.
_fail_lock = threading.Lock()
_fails: dict[str, list[float]] = {}
PER_IP, GLOBAL, WINDOW_S = 5, 30, 600


def login_blocked(ip: str) -> int:
    """Seconds until this address may try again (0 = allowed)."""
    now = time.monotonic()
    with _fail_lock:
        mine = [t for t in _fails.get(ip, []) if now - t < WINDOW_S]
        everyone = [t for ts in _fails.values() for t in ts if now - t < 300]
        if len(mine) >= PER_IP:
            return int(WINDOW_S - (now - mine[0])) + 1
        if len(everyone) >= GLOBAL:
            return int(300 - (now - min(everyone))) + 1
    return 0


def login_failed(ip: str) -> None:
    with _fail_lock:
        _fails.setdefault(ip, []).append(time.monotonic())
    time.sleep(1)  # slow down guessing


def login_succeeded(ip: str) -> None:
    with _fail_lock:
        _fails.pop(ip, None)


def require_auth(request: Request):
    """Dependency to require authentication."""
    if not verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


def logout(response: Response, request: Optional[Request] = None) -> None:
    """End the session on the server (not just in the browser) and clear the cookie."""
    if request is not None:
        _sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)
    response.delete_cookie(COOKIE_NAME)
