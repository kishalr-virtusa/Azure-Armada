"""DR Agent Console: FastAPI app (pages + JSON API). Run: gunicorn -k uvicorn.workers.UvicornWorker web.main:app"""
from __future__ import annotations

import logging
from pathlib import Path
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


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None):
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
    return _page(request, "exercise.html", "exercise", exercise_id=xid)


@app.get("/results", response_class=HTMLResponse)
@app.get("/results/{xid}", response_class=HTMLResponse)
async def results_page(request: Request, xid: Optional[str] = None):
    ctx = await run_in_threadpool(service.results_context, xid)
    return _page(request, "results.html", "results", **ctx)


@app.get("/reports/{xid}", response_class=HTMLResponse)
async def report(request: Request, xid: str):
    if not auth.verify_session(request):
        return RedirectResponse("/login", status_code=303)
    html = await run_in_threadpool(service.get_report_html, xid)
    return HTMLResponse(html or "<p>Report not generated yet.</p>", status_code=200 if html else 404)


# ------------------------------------------------------------------ JSON API
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
async def api_readiness_fix(data: dict = Body(...)):
    return await run_in_threadpool(service.apply_parity_fix, data.get("keys", []), data.get("approver_name", ""))


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
async def api_approve(data: dict = Body(...)):
    return await run_in_threadpool(service.approve_and_start, data.get("approver_name", ""), data.get("plan"))


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
def api_confirm(xid: str, step_id: str, data: Optional[dict] = Body(None)):
    return service.confirm_step(xid, step_id, (data or {}).get("by", "console user"))


@app.post("/api/exercise/{xid}/{cmd}", dependencies=[api])
def api_command(xid: str, cmd: str):
    return service.send_command(xid, cmd)


@app.get("/api/notifications", dependencies=[api])
async def api_notifications(after: Optional[str] = None):
    from dr_agent.tools import store
    return await run_in_threadpool(store.get_notifications, after)


@app.post("/api/notifications/{nid}/read", dependencies=[api])
def api_notification_read(nid: str):
    from dr_agent.tools import store
    store.mark_notification_read(nid)
    return {"ok": True}
