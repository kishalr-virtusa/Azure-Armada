"""Autopilot: the DR Agent watches readiness by itself, with no one asking.

Every `interval_min` (and on demand) it runs the deterministic readiness checks. When a check starts failing it asks
the Readiness agent (which investigates with its read-only tools) to explain, and then, depending on the autonomy
level:
  observe   notify only
  propose   prepare the allowlisted fix and wait for a human to approve it          (default)
  auto-fix  apply the allowlisted, DR-side-only fix itself (copy missing app settings to Southeast Asia), audited
It never runs a drill or touches production traffic on its own. Every run is stored and shown on the Overview.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dr_agent import trace
from dr_agent.tools import store

log = logging.getLogger(__name__)
STATE = "autopilot"            # exercises-table row: settings + last result + pending proposal
FEED = "autopilot"             # events partition: one event per run
LEVELS = ("observe", "propose", "auto-fix")
DEFAULTS = {"enabled": True, "level": "propose", "interval_min": 10}
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_state() -> dict:
    row = store.get_exercise(STATE) or {}
    settings = {**DEFAULTS, **json.loads(row.get("settings_json") or "{}")}
    return {"settings": settings, "last_run_at": row.get("last_run_at"), "running": bool(row.get("running")),
            "last": json.loads(row.get("last_json") or "{}"),
            "proposal": json.loads(row.get("proposal_json") or "null"),
            "next_run_at": row.get("next_run_at")}


def set_settings(enabled: Optional[bool] = None, level: Optional[str] = None, interval_min: Optional[int] = None,
                 by: str = "") -> dict:
    st = get_state()["settings"]
    if enabled is not None:
        st["enabled"] = bool(enabled)
    if level is not None:
        if level not in LEVELS:
            return {"error": f"level must be one of {LEVELS}"}
        st["level"] = level
    if interval_min is not None:
        st["interval_min"] = max(2, min(240, int(interval_min)))
    store.upsert_exercise(STATE, {"settings_json": json.dumps(st)})
    store.add_event(FEED, "info", f"Autopilot settings changed by {by or 'console user'}: "
                                  f"{'on' if st['enabled'] else 'off'}, level {st['level']}, every {st['interval_min']} min")
    return {"ok": True, "settings": st}


def run_once(trigger: str = "schedule", trace_id: Optional[str] = None) -> dict:
    """One autonomous readiness pass. Returns the run record."""
    from dr_agent.engine import runner
    from dr_agent import service
    from dr_agent.agents import readiness
    if not _lock.acquire(blocking=False):
        return {"skipped": "a check is already running"}
    try:
        if runner.any_running():
            rec = {"at": _now(), "trigger": trigger, "skipped": "a drill is running"}
            store.add_event(FEED, "info", "Autopilot skipped: a drill is running", data=rec)
            return rec
        store.upsert_exercise(STATE, {"running": "true"})
        prev = get_state()
        run_id = trace_id or f"autopilot-{secrets.token_hex(4)}"

        def _work() -> dict:
            facts = service.gather_readiness_facts()
            checks = readiness.build_checks(facts)
            failing = {c.name: c.detail for c in checks if c.status == "fail"}
            prev_failing = set((prev.get("last") or {}).get("failing", {}))
            new, resolved = sorted(set(failing) - prev_failing), sorted(prev_failing - set(failing))
            rec: dict[str, Any] = {"at": _now(), "trigger": trigger, "go": not failing, "failing": failing,
                                   "new": new, "resolved": resolved, "checks": len(checks)}
            if failing and (new or trigger == "manual"):
                res = readiness.run(checks)  # the agent investigates with its read-only tools
                rec["ai"] = {"summary": res.output.summary, "risks": res.output.risks[:4], "via": res.via}
                keys = service._fixable(res.output)
                if keys:
                    level = prev["settings"]["level"]
                    rec["proposal"] = {"action": "copy_app_settings", "keys": keys, "to": "Southeast Asia",
                                       "status": "awaiting approval" if level == "propose" else
                                                 ("applied automatically" if level == "auto-fix" else "not proposed (observe)")}
                    if level == "auto-fix":
                        out = service.apply_parity_fix(keys, "Autopilot (auto-fix level)")
                        rec["proposal"]["result"] = out.get("message")
            return rec

        rec = trace.run(run_id, _work)
        snap = trace.snapshot(run_id)
        rec["trace_id"] = run_id
        level = prev["settings"]["level"]
        proposal = rec.get("proposal") if rec.get("proposal", {}).get("status") == "awaiting approval" else None
        if not proposal and not rec["go"]:
            proposal = prev.get("proposal")  # the same problem is still there: its fix keeps waiting for a human
        if rec["go"]:
            proposal = None
        nxt = datetime.fromtimestamp(time.time() + 60 * prev["settings"]["interval_min"], timezone.utc).isoformat()
        store.upsert_exercise(STATE, {"running": "", "last_run_at": rec["at"], "next_run_at": nxt,
                                      "last_json": json.dumps(rec)[:30000],
                                      "proposal_json": json.dumps(proposal) if proposal else "",
                                      "last_trace_json": snap or ""})
        msg = ("all checks pass" if rec["go"] else f"{len(rec['failing'])} check(s) failing")
        if rec["new"]:
            msg += f"; new: {', '.join(rec['new'])}"
        if rec["resolved"]:
            msg += f"; resolved: {', '.join(rec['resolved'])}"
        store.add_event(FEED, "measurement" if rec["go"] else "error", f"Autopilot ({trigger}): {msg}", data=rec)
        from dr_agent.tools.notify import notify
        if rec["new"]:
            notify("failure", "Autopilot found a DR readiness problem",
                   f"{', '.join(rec['new'])}. {(rec.get('ai') or {}).get('summary', '')[:200]}"
                   + (" A fix is waiting for approval on the Overview." if proposal else ""), link="/", send_email=False)
        elif rec["resolved"]:
            notify("success", "Autopilot: readiness restored", f"Resolved: {', '.join(rec['resolved'])}.", link="/", send_email=False)
        return rec
    finally:
        store.upsert_exercise(STATE, {"running": ""})
        _lock.release()


def approve(by: str) -> dict:
    """A human approves the pending proposal; the fix itself is the existing allowlisted parity copy."""
    from dr_agent import service
    st = get_state()
    p = st["proposal"]
    if not p:
        return {"error": "nothing is waiting for approval"}
    out = service.apply_parity_fix(p["keys"], by or "console user")
    store.upsert_exercise(STATE, {"proposal_json": ""})
    store.add_event(FEED, "approval", f"Autopilot fix approved by {by or 'console user'}: copied {', '.join(p['keys'])} "
                                      f"to {p['to']}", data=out)
    return out


def feed(limit: int = 20) -> list[dict]:
    return list(reversed(store.get_events(FEED)[-limit:]))


def _loop() -> None:
    time.sleep(60)  # let the console start
    while True:
        try:
            st = get_state()
            if st["settings"]["enabled"]:
                last = st.get("last_run_at")
                due = not last or (time.time() - datetime.fromisoformat(last).timestamp()) >= 60 * st["settings"]["interval_min"]
                if due:
                    run_once("schedule")
        except Exception:  # noqa: BLE001
            log.exception("autopilot loop")
        time.sleep(30)


def start() -> None:
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, name="autopilot", daemon=True)
        _thread.start()
