"""DR Agent Console: FastAPI app (pages + JSON API). Run: gunicorn -k uvicorn.workers.UvicornWorker web.main:app"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote
from typing import Optional

from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dr_agent import service
from web import auth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dr-console")

BASE = Path(__file__).parent
app = FastAPI(title="DR Agent Console")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
api = Depends(auth.require_auth)


@app.on_event("startup")
def _resume() -> None:
    try:
        from dr_agent.engine.runner import resume_interrupted
        resumed = resume_interrupted()
        if resumed:
            log.info("resumed exercises: %s", resumed)
    except Exception:  # noqa: BLE001
        log.exception("resume check failed")


FOUNDRY_AGENTS: dict = {}


@app.on_event("startup")
def _register_foundry_agents() -> None:
    """Register the six agents in Azure AI Foundry Agent Service (background; idempotent)."""
    import threading

    def _go():
        from dr_agent.foundry import register_all
        FOUNDRY_AGENTS.update(register_all())
        log.info("foundry agents: %s", FOUNDRY_AGENTS)
    threading.Thread(target=_go, name="foundry-register", daemon=True).start()


@app.exception_handler(Exception)
async def _errors(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:500]}, status_code=500)


# ------------------------------------------------------------------ auth + pages
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------------------ public (no auth): judges' phones
@app.get("/public/live")
async def public_live():
    """Read-only drill status polled by Contoso Payments on phones (CORS open, cached 1 s, no secrets)."""
    data = await run_in_threadpool(service.public_live)
    return JSONResponse(data, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})


def _healthy_region_url() -> Optional[str]:
    from dr_agent.config import config
    from dr_agent.tools import probes
    for app_name in (config.webapp_a_name, config.webapp_b_name):
        if app_name:
            base = f"https://{app_name}.azurewebsites.net"
            res = probes.probe(base + "/health")
            if res.get("ok") and res.get("db_ok") is not False:
                return base
    return None


@app.get("/pay")
@app.get("/go")
async def go(request: Request):
    """Send a phone to the region Traffic Manager currently routes to (fresh DNS lookup on every call)."""
    from dr_agent.engine.runner import tm_resolve
    r = await run_in_threadpool(tm_resolve)
    url = r.get("url") if isinstance(r, dict) else None
    if not url:  # Traffic Manager unreachable / not deployed: send the phone to whichever region is healthy
        url = await run_in_threadpool(_healthy_region_url)
    if not url:
        return HTMLResponse("<h3>Contoso Payments is recovering. Retry in a few seconds.</h3>", status_code=503,
                            headers={"Refresh": "3", "Cache-Control": "no-store"})
    code, token = request.query_params.get("code"), request.query_params.get("k")
    target = url + "/" + (f"?code={quote(code)}" if code else f"?k={quote(token)}" if token else "")  # team link: hand the code to the app once
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})


@app.get("/public/access-mode")
async def public_access_mode():
    """Read by Contoso Payments (server side) to decide whether to admit visitors without the team code."""
    data = await run_in_threadpool(service.get_access)
    return JSONResponse({"public": data["public"], "public_until": data["public_until"]},
                        headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None):
    if auth.entra_enabled():
        return RedirectResponse("/.auth/login/aad?post_login_redirect_uri=/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login(passcode: str = Form(...)):
    if not auth.verify_passcode(passcode):
        return RedirectResponse("/login?error=Invalid+passcode", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    auth.create_session(resp)
    return resp


@app.get("/logout")
def logout():
    if auth.entra_enabled():
        return RedirectResponse("/.auth/logout?post_logout_redirect_uri=/", status_code=303)
    resp = RedirectResponse("/login", status_code=303)
    auth.logout(resp)
    return resp


def _page(request: Request, name: str, active: str, **ctx):
    if not auth.verify_session(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, name, {"active_page": active, **ctx})


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    return _page(request, "overview.html", "overview")


@app.get("/phase1", response_class=HTMLResponse)
def phase1(request: Request):
    return _page(request, "phase1.html", "phase1", profile=service.get_profile().model_dump())


@app.get("/plan", response_class=HTMLResponse)
def plan_page(request: Request):
    return _page(request, "plan.html", "plan")


@app.get("/exercise", response_class=HTMLResponse)
def exercise_latest(request: Request):
    ex = service.list_exercises()
    if ex:
        return RedirectResponse(f"/exercise/{ex[0]['PartitionKey']}", status_code=303)
    return RedirectResponse("/plan", status_code=303)


@app.get("/exercise/{xid}", response_class=HTMLResponse)
def exercise_page(request: Request, xid: str):
    from dr_agent.models import AppProfile
    from dr_agent.tools import store
    m = store.get_exercise(xid) or {}
    prof = AppProfile.model_validate_json(m["profile_json"]) if m.get("profile_json") else AppProfile()
    return _page(request, "exercise.html", "exercise", exercise_id=xid, rto_target_min=prof.rto_min,
                 app_url=service.live_config().get("app_url") or "/pay")


@app.get("/results", response_class=HTMLResponse)
@app.get("/results/{xid}", response_class=HTMLResponse)
async def results_page(request: Request, xid: Optional[str] = None):
    ctx = await run_in_threadpool(service.results_context, xid)
    return _page(request, "results.html", "results", **ctx)


@app.get("/live", response_class=HTMLResponse)
def live_page(request: Request):
    return _page(request, "live.html", "live")


@app.get("/reports/{xid}", response_class=HTMLResponse)
async def report(request: Request, xid: str):
    if not auth.verify_session(request):
        return RedirectResponse("/login", status_code=303)
    html = await run_in_threadpool(service.get_report_html, xid)
    return HTMLResponse(html or "<p>Report not generated yet.</p>", status_code=200 if html else 404)


def _who(request: Request, typed: Optional[str]) -> str:
    """Approver identity: the signed-in Entra user when available (non-repudiable), else the typed name."""
    return auth.current_user(request) or (typed or "").strip()


# ------------------------------------------------------------------ JSON API
@app.get("/api/me", dependencies=[api])
def api_me(request: Request):
    return {"user": auth.current_user(request), "auth": "entra" if auth.entra_enabled() else "passcode"}


@app.get("/api/status", dependencies=[api])
async def api_status():
    return await run_in_threadpool(service.get_status)


@app.get("/api/probe/tm", dependencies=[api])
async def api_probe_tm():
    return await run_in_threadpool(service.probe_tm)


@app.get("/api/agents", dependencies=[api])
def api_agents():
    """Which Foundry agents are registered (name -> agent id or error)."""
    import os
    return {"project_endpoint": os.environ.get("FOUNDRY_PROJECT_ENDPOINT"), "agents": FOUNDRY_AGENTS}


@app.get("/api/profile", dependencies=[api])
def api_get_profile():
    return service.get_profile().model_dump()


@app.post("/api/profile", dependencies=[api])
def api_save_profile(data: dict = Body(...)):
    return service.save_profile(data).model_dump()


@app.post("/api/strategy", dependencies=[api])
async def api_strategy():
    return await run_in_threadpool(service.recommend_strategy)


@app.post("/api/readiness", dependencies=[api])
async def api_readiness():
    return await run_in_threadpool(service.check_readiness)


@app.post("/api/readiness/fix", dependencies=[api])
async def api_readiness_fix(request: Request, data: dict = Body(...)):
    return await run_in_threadpool(service.apply_parity_fix, data.get("keys", []), _who(request, data.get("approver_name")))


@app.post("/api/runbook", dependencies=[api])
async def api_runbook():
    return await run_in_threadpool(service.review_runbook)


@app.get("/api/plan", dependencies=[api])
def api_get_plan():
    return service.get_draft_plan()


@app.post("/api/plan", dependencies=[api])
async def api_generate_plan():
    return await run_in_threadpool(service.generate_plan)


@app.post("/api/plan/approve", dependencies=[api])
async def api_approve(request: Request, data: dict = Body(...)):
    return await run_in_threadpool(service.approve_and_start, _who(request, data.get("approver_name")), data.get("plan"),
                                   bool(data.get("chaos")))


@app.get("/api/access", dependencies=[api])
async def api_get_access():
    return await run_in_threadpool(service.get_access)


@app.post("/api/access", dependencies=[api])
async def api_set_access(request: Request, data: dict = Body(...)):
    """{"public": true|false, "minutes": 120, "by": "name"}: switch Contoso Payments between private and public."""
    return await run_in_threadpool(service.set_access, bool(data.get("public")), _who(request, data.get("by")),
                                   data.get("minutes") or service.DEFAULT_PUBLIC_MINUTES)


@app.get("/api/live-config", dependencies=[api])
async def api_live_config():
    return await run_in_threadpool(service.live_config)


@app.get("/api/exercises", dependencies=[api])
async def api_exercises():
    return await run_in_threadpool(service.list_exercises)


@app.get("/api/exercise/{xid}", dependencies=[api])
async def api_exercise(xid: str):
    return await run_in_threadpool(service.get_exercise_view, xid)


@app.get("/api/exercise/{xid}/events", dependencies=[api])
async def api_events(xid: str, after: int = 0):
    from dr_agent.tools import store
    return await run_in_threadpool(store.get_events, xid, after)


@app.post("/api/exercise/{xid}/confirm/{step_id}", dependencies=[api])
def api_confirm(request: Request, xid: str, step_id: str, data: Optional[dict] = Body(None)):
    return service.confirm_step(xid, step_id, _who(request, (data or {}).get("by")) or "console user")


@app.post("/api/exercise/{xid}/chaos", dependencies=[api])
def api_chaos(xid: str):
    return service.arm_chaos(xid)


@app.post("/api/exercise/{xid}/{cmd}", dependencies=[api])
def api_command(xid: str, cmd: str):
    return service.send_command(xid, cmd)


@app.post("/api/notifications/test-email", dependencies=[api])
async def api_test_email():
    """Pre-demo check: synchronously deliver a test email and report which channel delivered it and how fast."""
    from dr_agent.config import config
    from dr_agent.tools.notify import deliver_email, email_channels
    if not config.notify_email_to:
        return {"email_sent": False, "email_errors": "NOTIFY_EMAIL_TO is not set"}
    out = await run_in_threadpool(deliver_email, "info", "Test email from DR Agent",
                                  "If you can read this, DR Agent email notifications are working.", "/")
    return {**out, "to": config.notify_email_to, "channels": email_channels()}


@app.get("/api/notifications", dependencies=[api])
async def api_notifications(after: Optional[str] = None):
    from dr_agent.tools import store
    return await run_in_threadpool(store.get_notifications, after)


@app.post("/api/notifications/{nid}/read", dependencies=[api])
def api_notification_read(nid: str):
    from dr_agent.tools import store
    store.mark_notification_read(nid)
    return {"ok": True}
