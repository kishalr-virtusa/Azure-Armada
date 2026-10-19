"""DR Agent Console: FastAPI app (pages + JSON API). Run: gunicorn -k uvicorn.workers.UvicornWorker web.main:app"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlencode
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
app = FastAPI(title="DR Agent Console", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")            # the console is never framed
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return resp
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def _hhmmss(v) -> str:
    """ISO timestamp -> '15:01:31.0 UTC' for evidence tables (keeps tenths: RTO/RPO evidence is about seconds)."""
    if not v:
        return "—"
    from datetime import datetime
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d.strftime("%H:%M:%S.") + f"{d.microsecond // 100000} UTC"
    except ValueError:
        return str(v)


templates.env.filters["hhmmss"] = _hhmmss
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


@app.on_event("startup")
def _start_autopilot() -> None:
    from dr_agent import autopilot
    autopilot.start()


FOUNDRY_AGENTS: dict = {}


@app.on_event("startup")
def _register_foundry_agents() -> None:
    """Register the seven agents in Azure AI Foundry Agent Service (background; idempotent)."""
    import threading

    def _go():
        from dr_agent.foundry import register_all
        FOUNDRY_AGENTS.update(register_all())
        log.info("foundry agents: %s", FOUNDRY_AGENTS)
    threading.Thread(target=_go, name="foundry-register", daemon=True).start()


@app.exception_handler(Exception)
async def _errors(request: Request, exc: Exception):
    ref = secrets.token_hex(4)
    log.exception("unhandled error %s on %s", ref, request.url.path)
    return JSONResponse({"error": f"internal error (ref {ref}); details are in the console log"}, status_code=500)


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
    # team link: hand the code (or device token) to the app once; d = the phone's id so its balance follows it
    q = request.query_params
    params = [("code", q["code"])] if q.get("code") else [("k", q["k"])] if q.get("k") else []
    params += [("d", q["d"])] if q.get("d") else []
    target = url + "/" + (f"?{urlencode(params)}" if params else "")
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})


@app.get("/public/access-mode")
async def public_access_mode():
    """Read by Contoso Payments (server side) to decide whether to admit visitors without the team code."""
    data = await run_in_threadpool(service.get_access)
    return JSONResponse({"public": data["public"], "public_until": data["public_until"]},
                        headers={"Cache-Control": "no-store"})


@app.post("/public/pay-event")
async def public_pay_event(request: Request):
    """Contoso Payments announces a payment the database just confirmed (server to server, with the team access
    code), so the live map shows it within a second."""
    code = os.environ.get("APP_ACCESS_CODE", "")
    if not code or not hmac.compare_digest(request.headers.get("x-access-code", ""), code):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    ok = isinstance(body, dict) and service.push_payment(body)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 400)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None):
    if auth.entra_enabled():
        return RedirectResponse("/.auth/login/aad?post_login_redirect_uri=/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")  # App Service front end
    return (fwd.split(",")[0].split(":")[0].strip() if fwd else "") or (request.client.host if request.client else "?")


@app.post("/login")
def login(request: Request, passcode: str = Form(...)):
    ip = _client_ip(request)
    wait = auth.login_blocked(ip)
    if wait:
        return RedirectResponse(f"/login?error=Too+many+attempts.+Try+again+in+{max(1, wait // 60)}+min", status_code=303)
    if not auth.verify_passcode(passcode):
        auth.login_failed(ip)
        return RedirectResponse("/login?error=Invalid+passcode", status_code=303)
    auth.login_succeeded(ip)
    resp = RedirectResponse("/", status_code=303)
    auth.create_session(resp)
    return resp


@app.get("/logout")
def logout(request: Request):
    if auth.entra_enabled():
        return RedirectResponse("/.auth/logout?post_logout_redirect_uri=/", status_code=303)
    resp = RedirectResponse("/login", status_code=303)
    auth.logout(resp, request)
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
    if not auth.verify_session(request):  # before any storage or DNS work
        return RedirectResponse("/login", status_code=303)
    from dr_agent.models import AppProfile
    from dr_agent.tools import store
    m = store.get_exercise(xid) or {}
    prof = AppProfile.model_validate_json(m["profile_json"]) if m.get("profile_json") else AppProfile()
    return _page(request, "exercise.html", "exercise", exercise_id=xid, rto_target_min=prof.rto_min,
                 app_url=service.live_config().get("app_url") or "/pay")


@app.get("/results", response_class=HTMLResponse)
@app.get("/results/{xid}", response_class=HTMLResponse)
async def results_page(request: Request, xid: Optional[str] = None):
    if not auth.verify_session(request):
        return RedirectResponse("/login", status_code=303)
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


@app.get("/api/live-map", dependencies=[api])
def api_live_map():
    return JSONResponse(service.live_map(), headers={"Cache-Control": "no-store"})


@app.get("/map", response_class=HTMLResponse)
def map_page(request: Request):
    return _page(request, "map.html", "map")


@app.get("/blackbox", response_class=HTMLResponse)
def blackbox_page(request: Request):
    return _page(request, "blackbox.html", "blackbox")


@app.post("/api/blackbox/ask", dependencies=[api])
async def api_blackbox_ask(request: Request, data: dict = Body(...)):
    """Ask the Black box agent about recorded drills; it answers only from the records, with citations."""
    from dr_agent import trace
    from dr_agent.agents import blackbox
    question = str(data.get("question") or "").strip()
    if not 1 <= len(question) <= 500:
        return JSONResponse({"error": "question must be 1-500 characters"}, status_code=422)
    history = data.get("history") if isinstance(data.get("history"), list) else []
    conv_id = str(data.get("conversation_id") or "")
    if not _BB_CONV.fullmatch(conv_id):
        conv_id = secrets.token_hex(6)  # a new conversation
    tid = _trace_id(request)
    res = await run_in_threadpool(trace.run, tid, blackbox.run, question, history)
    reply = {**res.output.model_dump(), "source": res.source, "via": res.via, "latency_ms": res.latency_ms}
    try:  # every question and answer is kept: "New conversation" starts another one, it never deletes
        from dr_agent.tools import store
        await run_in_threadpool(store.add_conversation_turn, conv_id, question, reply,
                                trace.snapshot(tid) if tid else None, auth.current_user(request) or "")
    except Exception as e:  # noqa: BLE001 - the answer is still shown; only its history is missing
        log.warning("could not save black box turn: %s", e)
    return {**reply, "conversation_id": conv_id}


_BB_CONV = re.compile(r"[0-9a-f]{12}")


@app.get("/api/blackbox/conversations", dependencies=[api])
def api_blackbox_conversations():
    from dr_agent.tools import store
    return JSONResponse(store.list_conversations(), headers={"Cache-Control": "no-store"})


@app.delete("/api/blackbox/conversations/{conv_id}", dependencies=[api])
def api_blackbox_delete(request: Request, conv_id: str):
    from dr_agent.tools import store
    n = store.delete_conversation(conv_id) if _BB_CONV.fullmatch(conv_id) else 0
    if not n:
        return JSONResponse({"error": "conversation not found"}, status_code=404)
    log.info("black box conversation %s deleted by %s", conv_id, auth.current_user(request) or "console user")
    return {"ok": True, "deleted": conv_id}


@app.get("/api/blackbox/conversations/{conv_id}", dependencies=[api])
def api_blackbox_conversation(conv_id: str):
    from dr_agent.tools import store
    conv = store.get_conversation(conv_id) if _BB_CONV.fullmatch(conv_id) else None
    if not conv:
        return JSONResponse({"error": "conversation not found"}, status_code=404)
    return JSONResponse(conv, headers={"Cache-Control": "no-store"})


@app.get("/api/autopilot", dependencies=[api])
def api_autopilot():
    from dr_agent import autopilot
    from dr_agent.tools import store
    st = autopilot.get_state()
    row = store.get_exercise(autopilot.STATE) or {}
    st["trace"] = json.loads(row["last_trace_json"]) if row.get("last_trace_json") else None
    st["feed"] = [{"ts": e.get("ts"), "kind": e.get("kind"), "message": e.get("message"),
                   "data": json.loads(e["data_json"]) if e.get("data_json") else None} for e in autopilot.feed(20)]
    return JSONResponse(st, headers={"Cache-Control": "no-store"})


@app.post("/api/autopilot/run", dependencies=[api])
async def api_autopilot_run(request: Request):
    from dr_agent import autopilot
    return await run_in_threadpool(autopilot.run_once, "manual", _trace_id(request) or None)


@app.post("/api/autopilot/settings", dependencies=[api])
def api_autopilot_settings(request: Request, data: dict = Body(...)):
    from dr_agent import autopilot
    return autopilot.set_settings(data.get("enabled"), data.get("level"), data.get("interval_min"), _who(request, data.get("by")))


@app.post("/api/autopilot/approve", dependencies=[api])
async def api_autopilot_approve(request: Request, data: Optional[dict] = Body(None)):
    from dr_agent import autopilot
    return await run_in_threadpool(autopilot.approve, _who(request, (data or {}).get("by")))


@app.get("/api/overview", dependencies=[api])
def api_overview():
    """Readiness verdict from live checks, region health with reasons, proven-recovery stats, system info."""
    return JSONResponse(service.overview_summary(), headers={"Cache-Control": "no-store"})


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


def _trace_id(request: Request) -> str:
    t = request.headers.get("x-trace-id", "")
    return t if 8 <= len(t) <= 64 and all(c.isalnum() or c == "-" for c in t) else ""


@app.get("/api/trace/{run_id}", dependencies=[api])
def api_trace(run_id: str):
    """Live activity of one agent run (polled by the page while the agent works)."""
    from dr_agent import trace
    return JSONResponse(trace.get(run_id), headers={"Cache-Control": "no-store"})


def _run_saving_trace(request: Request, record: str, field: str, fn, *args):
    """Run an agent call with live tracing, then save its activity next to the result so it survives navigation."""
    from dr_agent import trace
    from dr_agent.tools import store
    tid = _trace_id(request)
    out = trace.run(tid, fn, *args)
    snap = trace.snapshot(tid) if tid else None
    if snap:
        try:
            store.upsert_exercise(record, {field: snap})
        except Exception as e:  # noqa: BLE001 - the result itself is already saved; the trace is a nice-to-have
            log.warning("could not save trace %s: %s", field, e)
    return out


@app.get("/api/phase1", dependencies=[api])
def api_phase1_state():
    """Saved phase 1 results (profile, strategy, readiness, runbook) so the page restores them on return."""
    return JSONResponse(service.get_phase1_state(), headers={"Cache-Control": "no-store"})


@app.post("/api/strategy", dependencies=[api])
async def api_strategy(request: Request):
    return await run_in_threadpool(_run_saving_trace, request, service.PHASE1, "strategy_trace_json", service.recommend_strategy)


@app.post("/api/readiness", dependencies=[api])
async def api_readiness(request: Request):
    return await run_in_threadpool(_run_saving_trace, request, service.PHASE1, "readiness_trace_json", service.check_readiness)


@app.post("/api/readiness/fix", dependencies=[api])
async def api_readiness_fix(request: Request, data: dict = Body(...)):
    try:
        return await run_in_threadpool(service.apply_parity_fix, data.get("keys", []), _who(request, data.get("approver_name")))
    except Exception as e:  # noqa: BLE001 - shown to the operator; nothing was half-applied (single settings update)
        return {"success": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.get("/api/readiness/fix-progress", dependencies=[api])
def api_fix_progress():
    return JSONResponse(service.fix_progress(), headers={"Cache-Control": "no-store"})


@app.post("/api/runbook", dependencies=[api])
async def api_runbook(request: Request):
    return await run_in_threadpool(_run_saving_trace, request, service.PHASE1, "runbook_trace_json", service.review_runbook)


@app.get("/api/plan", dependencies=[api])
def api_get_plan():
    return service.get_draft_plan()


@app.post("/api/plan", dependencies=[api])
async def api_generate_plan(request: Request):
    return await run_in_threadpool(_run_saving_trace, request, service.DRAFT, "plan_trace_json", service.generate_plan)


@app.post("/api/plan/approve", dependencies=[api])
async def api_approve(request: Request, data: dict = Body(...)):
    # the approved plan is always the saved one the Planner produced and the approver reviewed, never a client copy
    return await run_in_threadpool(service.approve_and_start, _who(request, data.get("approver_name")), None,
                                   bool(data.get("chaos")), bool(data.get("load")))


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
def api_command(request: Request, xid: str, cmd: str, data: Optional[dict] = Body(None)):
    return service.send_command(xid, cmd, _who(request, (data or {}).get("by")))


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
