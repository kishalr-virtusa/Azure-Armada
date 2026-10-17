"""Contoso Payments Demo App - FastAPI application."""

import os
import random
import logging
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

# Required app settings (for config parity checks)
REQUIRED_SETTINGS = ["PAYMENT_PROVIDER"]

app = FastAPI(title="Contoso Payments", description="Demo payment app for DR Agent")

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
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


@app.get("/health")
async def health():
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
    
    # Then check database
    check = db.health_check()
    
    response = {
        "status": "ok" if check["db_ok"] else "error",
        "region": REGION_NAME,
        "db_ok": check["db_ok"],
        "db_server": check.get("db_server"),
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
