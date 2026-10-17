"""Service layer: everything the web routes call. Keeps routes thin and the engine/agents testable.

Phase-1 state (profile, strategy, readiness, runbook) and the draft plan are kept in the `exercises`
table under fixed partition keys, so the console survives restarts.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dr_agent.config import config
from dr_agent.engine import runner
from dr_agent.engine.measurements import fmt_duration
from dr_agent.engine.plan import validate_plan, PlanError
from dr_agent.models import AppProfile, ExercisePlan, ReadinessReport, StrategyRec
from dr_agent.tools import store

log = logging.getLogger(__name__)
PHASE1 = "phase1"
DRAFT = "draft-plan"
RUNBOOK_PATH = Path(__file__).resolve().parents[1] / "runbooks" / "payments-dr-runbook.md"
_status_cache: dict[str, Any] = {"t": 0.0, "v": None}


def _safe(fn, *a, **kw) -> Any:
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("%s failed: %s", getattr(fn, "__name__", fn), e)
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def _phase1() -> dict:
    return store.get_exercise(PHASE1) or {}


# ------------------------------------------------------------------ overview
def get_status(max_age_s: float = 5.0) -> dict:
    if _status_cache["v"] and time.monotonic() - _status_cache["t"] < max_age_s:
        return _status_cache["v"]
    from dr_agent.tools.azure_trafficmanager import get_profile_status, resolve_tm_dns
    from dr_agent.tools.azure_webapp import get_webapp_state
    fog = _safe(lambda: runner._fog().status())
    fg_view: dict = {"error": fog["error"]} if "error" in fog else {
        "primary_region": fog.get("primary_region"),
        "replication_role": (fog.get("servers", {}).get("A") or {}).get("role"),
        "replication_state": (fog.get("servers", {}).get("A") or {}).get("state"),
        "servers": fog.get("servers"),
    }
    routing = _safe(resolve_tm_dns)
    if isinstance(routing, dict) and "error" not in routing:
        routing["active_region"] = ("A (Central India)" if routing.get("points_to_region_a")
                                    else "B (Southeast Asia)" if routing.get("points_to_region_b") else "unknown")
    v = {
        "region_a": _safe(get_webapp_state, config.webapp_a_name),
        "region_b": _safe(get_webapp_state, config.webapp_b_name),
        "traffic_manager": _safe(get_profile_status),
        "failover_group": fg_view,
        "routing": routing,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    _status_cache.update(t=time.monotonic(), v=v)
    return v


def probe_tm() -> dict:
    return runner._probe("tm")


# ------------------------------------------------------------------ phase 1
def get_profile() -> AppProfile:
    raw = _phase1().get("profile_json")
    return AppProfile.model_validate_json(raw) if raw else AppProfile()


def save_profile(data: dict) -> AppProfile:
    p = AppProfile.model_validate(data)
    store.upsert_exercise(PHASE1, {"profile_json": p.model_dump_json()})
    return p


def _inventory_summary() -> dict:
    from dr_agent.tools.inventory import get_workload_inventory
    inv = _safe(get_workload_inventory)
    return inv if isinstance(inv, dict) else {}


def recommend_strategy() -> dict:
    from dr_agent.agents import architect
    res = architect.run(get_profile(), _inventory_summary())
    store.upsert_exercise(PHASE1, {"strategy_json": res.output.model_dump_json()})
    return {**res.output.model_dump(), "source": res.source, "via": res.via}


def gather_readiness_facts() -> dict:
    from dr_agent.tools.azure_trafficmanager import get_profile_status
    from dr_agent.tools.azure_webapp import list_app_settings
    from dr_agent.tools.heartbeat import replication_lag_seconds
    facts: dict[str, Any] = {}
    fog = _safe(lambda: runner._fog().status())
    facts["fog_status"] = fog if "error" not in fog else {"primary_region": None, "servers": {}}
    facts["lag_s"] = _safe(lambda: replication_lag_seconds(runner._sql_connect()))
    if isinstance(facts["lag_s"], dict):
        facts["lag_s"] = None
    facts["probe_a"] = _safe(runner._probe, "A")
    facts["probe_b"] = _safe(runner._probe, "B")
    tm = _safe(get_profile_status)
    facts["tm_endpoints"] = [{"name": e.get("name"), "enabled": e.get("endpoint_status") == "Enabled",
                              "monitor_status": e.get("endpoint_monitor_status")}
                             for e in (tm.get("endpoints") or [])] if isinstance(tm, dict) else []
    sa = _safe(list_app_settings, config.webapp_a_name)
    sb = _safe(list_app_settings, config.webapp_b_name)
    if "error" not in sa and "error" not in sb:
        facts["settings_a"], facts["settings_b"] = sa, sb
    facts["keyvault_ok"] = bool(config.console_passcode)  # resolved via Key Vault reference at startup
    return facts


def check_readiness() -> dict:
    from dr_agent.agents import readiness
    checks = readiness.build_checks(gather_readiness_facts())
    res = readiness.run(checks)
    store.upsert_exercise(PHASE1, {"readiness_json": res.output.model_dump_json()})
    out = res.output.model_dump(mode="json")
    out["via"] = res.via
    parity = next((c for c in res.output.checks if c.name.startswith("App settings parity")), None)
    out["fixable"] = parity.evidence.get("missing_in_region_b", []) if parity and parity.status == "fail" else []
    return out


def apply_parity_fix(keys: list[str], approver: str) -> dict:
    """Copy missing app settings from Region A to Region B (approved remediation, design G3)."""
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.web import WebSiteManagementClient
    client = WebSiteManagementClient(DefaultAzureCredential(), config.azure_subscription_id)
    a = client.web_apps.list_application_settings(config.workload_rg, config.webapp_a_name).properties or {}
    b_obj = client.web_apps.list_application_settings(config.workload_rg, config.webapp_b_name)
    b = dict(b_obj.properties or {})
    copied = [k for k in keys if k in a and k not in b]
    for k in copied:
        b[k] = a[k]
    if copied:
        b_obj.properties = b
        client.web_apps.update_application_settings(config.workload_rg, config.webapp_b_name, b_obj)
    from dr_agent.tools.notify import notify
    notify("success", "Configuration drift fixed",
           f"Copied {', '.join(copied) or 'nothing'} to Region B (approved by {approver}).", link="/phase1", send_email=False)
    return {"copied": copied, "approved_by": approver,
            "note": "Region B app restarts to load the setting; re-run readiness in ~60s."}


def review_runbook() -> dict:
    from dr_agent.agents import runbook
    md = RUNBOOK_PATH.read_text(encoding="utf-8") if RUNBOOK_PATH.exists() else "1. (no runbook found)"
    inv = _inventory_summary()
    res = runbook.run(md, inv)
    store.upsert_exercise(PHASE1, {"runbook_json": res.output.model_dump_json()[:60000]})
    return {**res.output.model_dump(), "source": res.source, "via": res.via}


# ------------------------------------------------------------------ plan
def generate_plan() -> dict:
    from dr_agent.agents import planner
    p1 = _phase1()
    strategy = StrategyRec.model_validate_json(p1["strategy_json"]) if p1.get("strategy_json") else None
    readiness = ReadinessReport.model_validate_json(p1["readiness_json"]) if p1.get("readiness_json") else None
    res = planner.run(get_profile(), strategy, readiness, _inventory_summary())
    store.upsert_exercise(DRAFT, {"plan_json": res.output.model_dump_json(), "source": res.source, "via": res.via})
    return {**res.output.model_dump(), "source": res.source, "via": res.via}


def get_draft_plan() -> dict:
    raw = (store.get_exercise(DRAFT) or {}).get("plan_json")
    return json.loads(raw) if raw else {}


def approve_and_start(approver: str, plan_data: Optional[dict] = None) -> dict:
    if not approver.strip():
        return {"error": "approver name is required"}
    plan = ExercisePlan.model_validate(plan_data) if plan_data else ExercisePlan.model_validate(get_draft_plan())
    try:
        validate_plan(plan)
    except PlanError as e:
        return {"error": f"plan rejected by safety validation: {e}"}
    p1 = _phase1()
    xid = "ex-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    now = datetime.now(timezone.utc).isoformat()
    store.upsert_exercise(xid, {
        "status": "approved", "plan_json": plan.model_dump_json(),
        "profile_json": get_profile().model_dump_json(),
        "strategy_json": p1.get("strategy_json"), "readiness_json": p1.get("readiness_json"),
        "runbook_json": (p1.get("runbook_json") or "")[:30000],
        "approvals_json": json.dumps({"plan": {"by": approver, "at": now}}),
        "confirmations_json": "{}", "completed_steps_json": "[]", "command": "", "t0": "",
    })
    store.add_event(xid, "approval", f"Plan approved by {approver}", None, {"by": approver, "at": now})
    try:
        runner.start(xid)
    except RuntimeError as e:
        return {"error": str(e)}
    return {"exercise_id": xid}


# ------------------------------------------------------------------ exercise
def get_exercise_view(xid: str) -> dict:
    m = store.get_exercise(xid)
    if not m:
        return {"error": "not found"}
    plan = json.loads(m.get("plan_json") or "{}")
    steps = plan.get("steps", [])
    done = set(json.loads(m.get("completed_steps_json") or "[]"))
    current = m.get("current_step") or m.get("waiting_step") or ""
    idx = next((i for i, s in enumerate(steps) if s["id"] == current),
               next((i for i, s in enumerate(steps) if s["id"] not in done), len(steps)))
    raw_status = m.get("status") or "unknown"
    return {
        "exercise_id": xid,
        "status": "running" if raw_status == "waiting_human" else raw_status,
        "raw_status": raw_status,
        "waiting_for_human": raw_status == "waiting_human",
        "plan": plan, "completed_steps": len(done), "completed_step_ids": sorted(done),
        "current_step_index": idx, "current_step": current,
        "t0": m.get("t0") or None, "t_failover": m.get("t_failover"), "t_recovered": m.get("t_recovered"),
        "t_failback_done": m.get("t_failback_done"),
        "rto_s": _num(m.get("rto_s")), "rpo_s": _num(m.get("rpo_s")), "failback_s": _num(m.get("failback_s")),
        "rto_met": _bool(m.get("rto_met")), "rpo_met": _bool(m.get("rpo_met")),
        "failed_step": m.get("failed_step") or None,
        "diagnosis": json.loads(m["diagnosis_json"]) if m.get("diagnosis_json") and raw_status == "paused_failed" else None,
        "report_url": m.get("report_url"), "error": m.get("error"), "running": runner.is_running(xid),
    }


def confirm_step(xid: str, step_id: str, by: str) -> dict:
    m = store.get_exercise(xid) or {}
    conf = json.loads(m.get("confirmations_json") or "{}")
    conf[step_id] = {"by": by or "console user", "at": datetime.now(timezone.utc).isoformat()}
    store.upsert_exercise(xid, {"confirmations_json": json.dumps(conf)})
    return {"ok": True}


def send_command(xid: str, cmd: str) -> dict:
    if cmd not in ("kill", "retry", "continue", "rollback", "abandon"):
        return {"error": "unknown command"}
    m = store.get_exercise(xid) or {}
    store.upsert_exercise(xid, {"command": cmd})
    store.add_event(xid, "info", f"Command received: {cmd}")
    # rollback requested while no runner is alive (e.g. after a crash): run it directly
    if cmd == "rollback" and not runner.is_running(xid) and m.get("status") not in ("completed", "rolled_back"):
        r = runner.ExerciseRunner(xid)
        import threading
        threading.Thread(target=r.rollback, daemon=True).start()
    return {"ok": True}


def list_exercises() -> list[dict]:
    rows = [e for e in store.list_exercises(50) if e.get("PartitionKey", "").startswith("ex-")]
    return rows


def results_context(xid: Optional[str]) -> dict:
    if not xid:
        ex = list_exercises()
        xid = ex[0]["PartitionKey"] if ex else None
    if not xid:
        return {"exercise_id": None, "status": "no exercise yet", "status_class": "NR"}
    m = store.get_exercise(xid) or {}
    prof = AppProfile.model_validate_json(m["profile_json"]) if m.get("profile_json") else AppProfile()
    rto, rpo, fb = _num(m.get("rto_s")), _num(m.get("rpo_s")), _num(m.get("failback_s"))
    ev = json.loads(m.get("rpo_evidence_json") or "{}")
    rto_met, rpo_met = _bool(m.get("rto_met")), _bool(m.get("rpo_met"))
    status = m.get("status", "unknown")
    return {
        "exercise_id": xid, "status": status,
        "status_class": {"completed": "pass", "rolled_back": "warn", "failed": "fail", "killed": "fail"}.get(status, "upd"),
        "rto_display": fmt_duration(rto), "rto_met": rto_met, "rto_target_min": prof.rto_min, "rto_target_s": prof.rto_min * 60,
        "rpo_s": None if rpo is None else round(rpo, 1), "rpo_met": rpo_met,
        "rpo_target_min": prof.rpo_min, "rpo_target_s": prof.rpo_min * 60,
        "failback_display": fmt_duration(fb) if fb is not None else None,
        "overall_pass": rto_met and rpo_met,
        "t0": m.get("t0"), "t_failover": m.get("t_failover"), "t_recovered": m.get("t_recovered"),
        "t_failback_done": m.get("t_failback_done"),
        "last_hb_seq": ev.get("last_acked_seq"), "last_hb_ts": ev.get("last_acked_ts"),
        "dr_hb_seq": None, "dr_hb_ts": ev.get("newest_present_ts"),
        "remediation": json.loads(m.get("remediation_json") or "[]"),
        "report_url": f"/reports/{xid}" if m.get("report_url") else None,
    }


def get_report_html(xid: str) -> Optional[str]:
    return store.get_report(xid)


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool:
    return v is True or str(v).lower() == "true"
