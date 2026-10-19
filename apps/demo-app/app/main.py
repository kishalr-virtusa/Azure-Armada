"""Contoso Payments Demo App - FastAPI application."""

import hashlib
import hmac
import os
import random
import re
import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Request, HTTPException
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
from app.access import _token as _access_token, ACCESS_CODE as _ACCESS_CODE  # noqa: E402
access_token = _access_token if _ACCESS_CODE else None
app.middleware("http")(gate_middleware)


# Only the DR console's presenter view may frame the app (its phone mock-up); nothing else can embed it.
_CONSOLE_ORIGIN = "/".join(PUBLIC_URL.split("/")[:3]) if PUBLIC_URL.startswith("https://") else ""


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Content-Security-Policy", f"frame-ancestors 'self' {_CONSOLE_ORIGIN}".strip())
    return resp
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
        if v.get("db_ok"):  # only successes are cached: a stale failure would delay recovery detection (RTO)
            _health_cache.update(t=time.monotonic(), v=v)
        else:
            _health_cache.update(t=0.0, v=None)
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
        # public endpoint: say what failed, not the driver's message (server names, SQL text)
        "db_error": "database unavailable" if not check.get("db_ok") else None,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    
    if not check["db_ok"]:
        return JSONResponse(content=response, status_code=503)
    
    return response


OPENING_BALANCE = 12450.00
_DEVICE_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _device(request: Request) -> str | None:
    """Anonymous per-phone id (random, made in the browser) so each phone sees its own balance."""
    d = request.headers.get("x-device") or request.query_params.get("device") or ""
    return d if _DEVICE_RE.match(d) else None


@app.post("/api/txn")
def create_transaction(request: Request):
    """Create a new random transaction."""
    try:
        # Random amount between $10 and $500
        amount = round(random.uniform(10, 500), 2)
        txn = db.insert_transaction(amount, REGION_NAME, _device(request))
        logger.info(f"Transaction created: {txn['id']} for ${amount}")
        return txn
    except Exception as e:
        logger.error(f"Failed to create transaction: {e}")
        raise HTTPException(status_code=503, detail="payment service temporarily unavailable")


@app.get("/api/recent")
def recent_transactions(request: Request):
    """Get recent transactions (this phone's own when it sends its device id)."""
    try:
        return db.get_recent_transactions(10, _device(request))
    except Exception as e:
        logger.error(f"Failed to get transactions: {e}")
        raise HTTPException(status_code=503, detail="payment service temporarily unavailable")


@app.get("/api/balance")
def balance(request: Request):
    """This phone's balance, computed from its payments stored in Azure SQL (so it survives a failover)."""
    device = _device(request)
    if not device:
        raise HTTPException(status_code=400, detail="device id required")
    try:
        spent, n = db.device_spent(device)
        return {"balance": round(OPENING_BALANCE - spent, 2), "payments": n, "region": REGION_NAME}
    except Exception as e:
        logger.error(f"Failed to get balance: {e}")
        raise HTTPException(status_code=503, detail="balance temporarily unavailable")


# Shared live activity: payments from every phone, with an anonymous per-device label. The salt must be the
# same in both regions so a customer keeps their label across a failover.
FEED_SALT = os.getenv("FEED_SALT") or "contoso-feed-v1"


def _customer_label(device: str | None) -> str:
    if not device:
        return "A customer"
    h = hmac.new(FEED_SALT.encode(), device.encode(), hashlib.sha256).hexdigest()
    return f"Customer {h[:4].upper()}"


def _utc_iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return str(v)


@app.get("/api/feed")
def feed(request: Request, after: int = Query(0, ge=0)):
    """Latest payments from all phones newer than `after` (newest first). Never returns device ids."""
    me = _device(request)
    try:
        rows = db.get_feed(after, 20)
    except Exception as e:
        logger.error(f"Failed to get feed: {e}")
        raise HTTPException(status_code=503, detail="activity temporarily unavailable")
    items = []
    for r in rows:
        mine = bool(me) and r.get("device") == me
        items.append({
            "id": r["id"],
            "amount": r["amount"],
            "created_at": _utc_iso(r.get("created_at")),
            "region": r["region"],
            "label": "You" if mine else _customer_label(r.get("device")),
            "mine": mine,
        })
    return {"payments": items, "server_time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/config")
async def get_config():
    """Get app configuration for live DR experience."""
    return {
        "region": REGION_NAME,
        "live_url": DR_LIVE_URL,
        "public_url": PUBLIC_URL,
        "alt_url": ALT_PUBLIC_URL,
        # device token (not the code): only reachable by visitors who already passed the gate
        "access_token": access_token() if access_token else "",
    }
