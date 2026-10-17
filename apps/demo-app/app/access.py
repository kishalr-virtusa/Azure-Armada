"""Private/public access gate for Contoso Payments.

Private (default): only visitors with the team access code (once, via ?code=... or the gate form; then a
cookie) or the DR Agent (X-Access-Code header) get in. Public: anyone. The mode is owned by the DR console
(GET {console}/public/access-mode), switched there by an authenticated operator, time-boxed and audited.
Fail closed: if the mode cannot be read for 60 s, the app is private.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import threading
import time

import httpx
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger(__name__)

ACCESS_CODE = os.getenv("APP_ACCESS_CODE", "")
COOKIE = "contoso_access"
EXEMPT_PREFIXES = ("/health", "/static/", "/favicon", "/manifest.webmanifest", "/access")
MODE_TTL_S = 5
FAIL_CLOSED_AFTER_S = 60

_mode = {"public": False, "checked": 0.0, "ok_at": 0.0}
_lock = threading.Lock()


def _mode_url() -> str:
    live = os.getenv("DR_LIVE_URL", "")
    return live.replace("/public/live", "/public/access-mode") if live else ""


def _token() -> str:
    return hashlib.sha256(f"contoso:{ACCESS_CODE}".encode()).hexdigest()


def is_public() -> bool:
    """Cached read of the console's mode; keeps the last answer for up to 60 s on errors, then private."""
    now = time.monotonic()
    if now - _mode["checked"] < MODE_TTL_S:
        return _mode["public"]
    with _lock:
        if now - _mode["checked"] < MODE_TTL_S:
            return _mode["public"]
        _mode["checked"] = now
        url = _mode_url()
        try:
            if not url:
                raise RuntimeError("DR_LIVE_URL not set")
            r = httpx.get(url, timeout=3)
            r.raise_for_status()
            _mode.update(public=bool(r.json().get("public")), ok_at=now)
        except Exception as e:  # noqa: BLE001
            if now - _mode["ok_at"] > FAIL_CLOSED_AFTER_S:
                _mode["public"] = False
            log.warning("access mode unavailable (%s); public=%s", e, _mode["public"])
    return _mode["public"]


def has_access(request: Request) -> bool:
    if not ACCESS_CODE:  # gate disabled (local dev)
        return True
    if hmac.compare_digest(request.headers.get("x-access-code", ""), ACCESS_CODE):
        return True
    if hmac.compare_digest(request.cookies.get(COOKIE, ""), _token()):
        return True
    return is_public()


def _grant(resp, request: Request):
    """Remember the device. SameSite=None + Partitioned so it also works inside the presenter view's phone
    frame (an iframe on the console's site); the cookie stays HttpOnly and Secure (HTTPS only)."""
    resp.headers.append("set-cookie", f"{COOKIE}={_token()}; Max-Age={30 * 86400}; Path=/; HttpOnly; Secure; "
                                      "SameSite=None; Partitioned")
    return resp


GATE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#5E2CFE"><title>Contoso Payments · Private preview</title>
<link rel="stylesheet" href="/static/virtusa.css"><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg,#F7F7F9);font-family:Montserrat,system-ui,sans-serif;padding:16px}}
.g{{max-width:380px;width:100%;background:var(--card,#fff);border:1px solid var(--line,#E3E3E8);border-radius:14px;padding:28px;text-align:center}}
h1{{font-size:22px;margin:8px 0 4px}}p{{color:#5B6170;font-size:14px;line-height:1.5}}
input{{width:100%;box-sizing:border-box;font-size:18px;padding:12px;border:1px solid #CFCFD8;border-radius:10px;margin:12px 0;text-align:center;letter-spacing:.08em}}
button{{width:100%;min-height:48px;font-size:16px;font-weight:600;color:#fff;background:#5E2CFE;border:0;border-radius:10px}}
.err{{color:#C0392B;font-size:13px}}.f{{margin-top:18px;font-size:11px;color:#7C8190}}
</style></head><body><form class="g" method="post" action="/access">
<div style="font-size:40px">🔒</div><h1>Contoso Payments</h1>
<p>This demo is in <b>private preview</b>. Enter the team access code, or wait until the presenter opens it to everyone.</p>
{error}<input name="code" type="password" autocomplete="one-time-code" placeholder="Access code" required>
<input type="hidden" name="next" value="{next}"><button type="submit">Enter</button>
<div class="f">DR protected by Virtusa DR Agent · Powered by Microsoft Azure</div></form></body></html>"""


async def gate_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)
    code = request.query_params.get("code")
    if code and ACCESS_CODE and hmac.compare_digest(code, ACCESS_CODE):
        # one-time link: remember the device, then drop the code from the URL
        return _grant(RedirectResponse(path or "/", status_code=303), request)
    if await run_in_threadpool(has_access, request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "private", "detail": "Contoso Payments is in private preview"}, status_code=401)
    return HTMLResponse(GATE_HTML.format(error="", next=path), status_code=401, headers={"Cache-Control": "no-store"})


async def access_post(request: Request):
    form = await request.form()
    code, nxt = str(form.get("code", "")), str(form.get("next", "/"))
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    if ACCESS_CODE and hmac.compare_digest(code, ACCESS_CODE):
        return _grant(RedirectResponse(nxt, status_code=303), request)
    await asyncio.sleep(1)  # slow down guessing
    return HTMLResponse(GATE_HTML.format(error='<p class="err">That code is not valid.</p>', next=nxt), status_code=401)
