"""Contoso Payments Demo App - FastAPI application."""

import os
import random
import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Environment
REGION_NAME = os.getenv("REGION_NAME", "Unknown")
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "")
DR_LIVE_URL = os.getenv("DR_LIVE_URL", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")
ALT_PUBLIC_URL = os.getenv("ALT_PUBLIC_URL", "")

# Required app settings (for config parity checks)
REQUIRED_SETTINGS = ["PAYMENT_PROVIDER"]

app = FastAPI(title="Contoso Payments", description="Demo payment app for DR Agent")

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Private/public access gate (mode is switched in the DR console; see app/access.py)
from app.access import access_post, gate_middleware  # noqa: E402
app.middleware("http")(gate_middleware)
app.add_api_route("/access", access_post, methods=["POST"], include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")


def check_required_config() -> list[str]:
    """Check for missing required configuration. Returns list of missing keys."""
    missing = []
    if not PAYMENT_PROVIDER:
        missing.append("PAYMENT_PROVIDER")
    return missing


@app.on_event("startup")
async def startup():
    """Initialize database tables on startup."""
    missing = check_required_config()
    if missing:
        logger.warning(f"Missing required configuration: {missing}")
    
    try:
        db.init_tables()
        logger.info(f"Contoso Payments started in {REGION_NAME}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main page."""
    missing_config = check_required_config()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "region": REGION_NAME,
            "config_error": len(missing_config) > 0,
            "missing_config": missing_config,
        },
    )


_health_cache: dict = {"t": 0.0, "v": None}
_health_lock = threading.Lock()
HEALTH_CACHE_S = 3.0


def _cached_health() -> dict:
    now = time.monotonic()
    if _health_cache["v"] is not None and now - _health_cache["t"] < HEALTH_CACHE_S:
        return _health_cache["v"]
    with _health_lock:  # one database check at a time; concurrent probes reuse its result
        if _health_cache["v"] is not None and time.monotonic() - _health_cache["t"] < HEALTH_CACHE_S:
            return _health_cache["v"]
        v = db.health_check()
        _health_cache.update(t=time.monotonic(), v=v)
        return v


@app.get("/health")
def health():
    """Health check endpoint for Traffic Manager probes."""
    # First check required configuration
    missing_config = check_required_config()
    if missing_config:
        return JSONResponse(
            content={
                "status": "config_error",
                "region": REGION_NAME,
                "db_ok": False,
                "missing": missing_config,
                "time": datetime.now(timezone.utc).isoformat(),
            },
            status_code=503,
        )
    
    # Then check database (cached ~3s: Traffic Manager probes from many locations at once, and each check
    # writes to the database; without the cache a probe storm after failback can exceed TM's 5s probe timeout)
    check = _cached_health()
    
    response = {
        "status": "ok" if check["db_ok"] else "error",
        "region": REGION_NAME,
        "db_ok": check["db_ok"],
        "db_server": check.get("db_server"),
        "db_error": (check.get("error") or "")[:300] or None,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    
    if not check["db_ok"]:
        return JSONResponse(content=response, status_code=503)
    
    return response


@app.post("/api/txn")
async def create_transaction():
    """Create a new random transaction."""
    try:
        # Random amount between $10 and $500
        amount = round(random.uniform(10, 500), 2)
        txn = db.insert_transaction(amount, REGION_NAME)
        logger.info(f"Transaction created: {txn['id']} for ${amount}")
        return txn
    except Exception as e:
        logger.error(f"Failed to create transaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recent")
async def recent_transactions():
    """Get recent transactions."""
    try:
        return db.get_recent_transactions(10)
    except Exception as e:
        logger.error(f"Failed to get transactions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config():
    """Get app configuration for live DR experience."""
    return {
        "region": REGION_NAME,
        "live_url": DR_LIVE_URL,
        "public_url": PUBLIC_URL,
        "alt_url": ALT_PUBLIC_URL,
    }
