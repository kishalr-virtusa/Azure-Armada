"""Service layer: everything the web routes call. Keeps routes thin and the engine/agents testable.

Phase-1 state (profile, strategy, readiness, runbook) and the draft plan are kept in the `exercises`
table under fixed partition keys, so the console survives restarts.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dr_agent import trace
from dr_agent.config import config
from dr_agent.engine import runner
from dr_agent.engine import measurements as M
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
def _not_deployed(v: Any, what: str) -> Any:
    """Turn raw 'not found' errors into a clean state the UI can show as a badge (e.g. while a region is
    still being created), keeping the original error for the tooltip."""
    err = v.get("error", "") if isinstance(v, dict) else ""
    if err and ("ResourceNotFound" in err or "does not exist" in err or "NXDOMAIN" in err):
        return {"state": "NotDeployed", "message": f"{what} is not deployed yet", "detail": err[:200]}
    return v


def get_status(max_age_s: float = 5.0) -> dict:
    if _status_cache["v"] and time.monotonic() - _status_cache["t"] < max_age_s:
        return _status_cache["v"]
    from concurrent.futures import ThreadPoolExecutor
    from dr_agent.tools.azure_trafficmanager import get_profile_status, resolve_tm_dns
    from dr_agent.tools.azure_webapp import get_webapp_state
    with ThreadPoolExecutor(max_workers=5) as pool:  # independent Azure reads: ~1 s instead of ~5 s
        f_fog = pool.submit(_safe, lambda: runner._fog().status())
        f_dns = pool.submit(_safe, resolve_tm_dns)
        f_a = pool.submit(_safe, get_webapp_state, config.webapp_a_name)
        f_b = pool.submit(_safe, get_webapp_state, config.webapp_b_name)
        f_tm = pool.submit(_safe, get_profile_status)
    fog = f_fog.result()
    fg_view: dict = {"error": fog["error"]} if "error" in fog else {
        "primary_region": fog.get("primary_region"),
        "replication_role": (fog.get("servers", {}).get("A") or {}).get("role"),
        "replication_state": (fog.get("servers", {}).get("A") or {}).get("state"),
        "servers": fog.get("servers"),
    }
    routing = f_dns.result()
    if isinstance(routing, dict) and "error" not in routing:
        routing["active_region"] = ("A (East Asia)" if routing.get("points_to_region_a")
                                    else "B (Southeast Asia)" if routing.get("points_to_region_b") else "unknown")
    v = {
        "region_a": _not_deployed(f_a.result(), "Region A web app"),
        "region_b": _not_deployed(f_b.result(), "Region B web app"),
        "traffic_manager": _not_deployed(f_tm.result(), "Traffic Manager"),
        "failover_group": fg_view,
        "routing": _not_deployed(routing, "Traffic Manager DNS"),
        "time": datetime.now(timezone.utc).isoformat(),
    }
    _status_cache.update(t=time.monotonic(), v=v)
    return v


# One-glance answer to "can we recover right now, and when did we last prove it?" for the Overview.
# The live checks are the deterministic readiness checks (no AI call); they take several seconds, so they run in
# the background and the page shows the last result with its age.
_summary: dict[str, Any] = {"t": 0.0, "v": None, "busy": False, "dirty": False}


def _invalidate_summary() -> None:
    """Make the Overview re-check now; if a check is already running, run another one right after it."""
    _summary.update(t=0.0, dirty=True)
_summary_lock = threading.Lock()
SUMMARY_MAX_AGE_S = 45.0


def _drill_stats() -> dict:
    prof = get_profile()
    rows = []
    for e in list_exercises()[:20]:
        m = store.get_exercise(e["PartitionKey"]) or {}
        if m.get("status") != "completed" or _num(m.get("rto_s")) is None:
            continue
        rows.append({"id": e["PartitionKey"], "t0": m.get("t0"), "rto_s": _num(m.get("rto_s")),
                     "rpo_s": _num(m.get("rpo_s")), "failback_s": _num(m.get("failback_s")),
                     "iaas_s": _num(m.get("iaas_recovered_s")), "chaos": bool(m.get("chaos_injected"))})
    plain = sorted(r["rto_s"] for r in rows if not r["chaos"])
    median = None
    if plain:
        mid = len(plain) // 2
        median = plain[mid] if len(plain) % 2 else round((plain[mid - 1] + plain[mid]) / 2, 3)
    return {"latest": rows[0] if rows else None, "count": len(rows), "count_plain": len(plain),
            "median_rto_s": median, "best_rto_s": plain[0] if plain else None, "worst_rto_s": plain[-1] if plain else None,
            "max_rpo_s": max((r["rpo_s"] for r in rows if r["rpo_s"] is not None), default=None),
            "latest_iaas_s": next((r["iaas_s"] for r in rows if r["iaas_s"] is not None), None),
            "chaos_ids": [r["id"] for r in rows if r["chaos"]],
            "rto_target_min": prof.rto_min, "rpo_target_min": prof.rpo_min}


def _compute_summary() -> dict:
    from dr_agent.agents import readiness
    facts = gather_readiness_facts()
    checks = readiness.build_checks(facts)
    failing = [c for c in checks if c.status == "fail"]
    access = _safe(get_access)
    agents = _safe(lambda: __import__("dr_agent.foundry", fromlist=["x"]).get_agents())
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "go": not failing,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
        "blocking": [{"name": c.name, "detail": c.detail} for c in failing],
        "health": {k: {"ok": bool(v.get("ok")) and v.get("db_ok") is not False, "status_code": v.get("status_code"),
                       "reason": v.get("reason") or v.get("error"), "latency_ms": v.get("latency_ms")}
                   for k, v in (("A", facts.get("probe_a") or {}), ("B", facts.get("probe_b") or {}))},
        "asr": facts.get("asr"),
        "drills": _safe(_drill_stats),
        "access": {"public": access.get("public"), "public_until": access.get("public_until")} if isinstance(access, dict) and "error" not in access else None,
        "agents": {"platform": "Azure AI Foundry Agent Service", "count": 6,
                   "connected": agents is not None and not (isinstance(agents, dict) and "error" in agents)},
    }


def _refresh_summary() -> None:
    while True:
        _summary["dirty"] = False
        try:
            v = _compute_summary()
            _summary.update(t=0.0 if _summary["dirty"] else time.monotonic(), v=v)
        except Exception as e:  # noqa: BLE001
            log.warning("overview summary failed: %s", e)
        if not _summary["dirty"]:
            break
    _summary["busy"] = False


def overview_summary() -> dict:
    """Returns the latest summary immediately; refreshes it in the background when it is older than 45 s."""
    with _summary_lock:
        stale = not _summary["v"] or time.monotonic() - _summary["t"] > SUMMARY_MAX_AGE_S
        if stale and not _summary["busy"]:
            _summary["busy"] = True
            threading.Thread(target=_refresh_summary, daemon=True).start()
    if not _summary["v"]:
        return {"pending": True}
    return {**_summary["v"], "age_s": round(time.monotonic() - _summary["t"], 1), "refreshing": _summary["busy"]}


# ------------------------------------------------------------------ live map
def _epoch(v: Any) -> Optional[float]:
    iso = _utc_iso(v)
    return datetime.fromisoformat(iso).timestamp() if iso else None


def _utc_iso(v: Any) -> Optional[str]:
    """Stored timestamps are UTC but may lack the offset; browsers would read those as local time."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


_map_cache: dict[str, Any] = {"t": 0.0, "v": None}
_map_lock = threading.Lock()

# ------------------------------------------------------------------ customer payments (for the live map)
PAYMENTS_SQL = ("SELECT TOP 30 id, amount, created_at, region FROM dbo.transactions "
                "WHERE amount > 0 AND region <> 'probe' AND id > %s ORDER BY id DESC")
PAYMENTS_MAX_AGE_S = 2.0
_REGION_KEY = {"East Asia": "A", "Southeast Asia": "B"}
_pay_cache: dict[str, Any] = {"t": 0.0, "v": [], "busy": False}
_pay_lock = threading.Lock()


def read_payments(after: int = 0) -> list[dict]:
    """Newest customer payments from Azure SQL (through the failover-group listener), newest first.
    Never raises: any error (outage, failover in progress, not deployed) gives an empty list."""
    try:
        with runner._sql_connect()() as conn:
            cur = conn.cursor()
            cur.execute(PAYMENTS_SQL, (int(after),))
            rows = cur.fetchall()
        return [{"id": int(r[0]), "amount": round(float(r[1]), 2), "created_at": _utc_iso(r[2]),
                 "region": r[3], "served": _REGION_KEY.get(r[3])} for r in rows]
    except Exception as e:  # noqa: BLE001
        log.info("payments unavailable: %s", e)
        return []


def _refresh_payments() -> None:
    try:
        _pay_cache.update(v=read_payments(0), t=time.monotonic())
    finally:
        _pay_cache["busy"] = False


def recent_payments() -> list[dict]:
    """Cached (~2 s) payments that never block the map: a stale cache starts one background read and the
    last answer is returned at once (a hung SQL login during a failover cannot slow the map down)."""
    if time.monotonic() - _pay_cache["t"] >= PAYMENTS_MAX_AGE_S:
        with _pay_lock:
            if not _pay_cache["busy"] and time.monotonic() - _pay_cache["t"] >= PAYMENTS_MAX_AGE_S:
                _pay_cache["busy"] = True
                threading.Thread(target=_refresh_payments, name="live-map-payments", daemon=True).start()
    return list(_pay_cache["v"])


def live_map() -> dict:
    """Latest map state, instantly. A background refresher recomputes it every ~2 s while the page is open."""
    _map_cache["seen"] = time.monotonic()
    with _map_lock:
        if not _map_cache.get("thread"):
            _map_cache["thread"] = threading.Thread(target=_map_refresher, name="live-map", daemon=True)
            _map_cache["thread"].start()
    if not _map_cache["v"] or time.monotonic() - _map_cache["t"] > 5:  # first look after an idle spell: fresh, not stale
        return _compute_live_map()
    return _map_cache["v"]


def _map_refresher() -> None:
    try:
        while time.monotonic() - _map_cache.get("seen", 0) < 30:  # stop when nobody has looked for 30 s
            started = time.monotonic()
            try:
                _compute_live_map()
            except Exception as e:  # noqa: BLE001
                log.warning("live map refresh failed: %s", e)
            time.sleep(max(0.0, 2.0 - (time.monotonic() - started)))
    finally:
        _map_cache["thread"] = None


def _compute_live_map() -> dict:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_st = pool.submit(get_status, 1.5)
        f_pa = pool.submit(_safe, runner._probe, "A")
        f_pb = pool.submit(_safe, runner._probe, "B")
        f_tm = pool.submit(_safe, runner.tm_resolve)  # the engine's fresh DNS answer, not a cached lookup
    st = f_st.result()
    probes = {"A": f_pa.result(), "B": f_pb.result()}
    eps = {e.get("name"): e for e in ((st.get("traffic_manager") or {}).get("endpoints") or [])}
    fog = st.get("failover_group") or {}
    servers = fog.get("servers") or {}
    regions = {}
    for r, key in (("A", "region_a"), ("B", "region_b")):
        app, pr = st.get(key) or {}, probes[r]
        ep = eps.get(f"endpoint-{r.lower()}") or {}
        regions[r] = {
            "app_state": app.get("state") or ("unknown" if app.get("error") else None),
            "healthy": bool(pr.get("ok")) and pr.get("db_ok") is not False,
            "reason": None if pr.get("ok") else (pr.get("reason") or pr.get("error")),
            "latency_ms": pr.get("latency_ms"),
            "sql_role": (servers.get(r) or {}).get("role"),
            "tm_enabled": ep.get("endpoint_status") == "Enabled",
            "tm_monitor": ep.get("endpoint_monitor_status"),
        }
    fresh = f_tm.result() or {}
    routing = st.get("routing") or {}
    serving = fresh.get("dns_region") or ("A" if routing.get("points_to_region_a") else "B" if routing.get("points_to_region_b") else None)
    out: dict[str, Any] = {"regions": regions, "serving": serving, "primary": fog.get("primary_region"),
                           "replication": fog.get("replication_state"), "drill": None,
                           "server_time": datetime.now(timezone.utc).isoformat()}
    pays = _safe(recent_payments)
    pays = pays if isinstance(pays, list) else []
    out["payments"] = pays
    out["last_payment_id"] = pays[0]["id"] if pays else None
    ex = list_exercises()[:1]
    if ex:
        xid = ex[0]["PartitionKey"]
        m = store.get_exercise(xid) or {}
        plan = json.loads(m.get("plan_json") or "{}")
        step_id = m.get("current_step") or m.get("waiting_step") or ""
        step = next((s for s in plan.get("steps", []) if s["id"] == step_id), None)
        r = runner._RUNNERS.get(xid)
        hb = list(getattr(r, "heartbeats", []) or [])
        now = datetime.now(timezone.utc)
        aware = lambda t: t if t.tzinfo else t.replace(tzinfo=timezone.utc)  # noqa: E731
        recent = [h for h in hb if (now - aware(h.client_ts)).total_seconds() <= 10]
        ev = json.loads(m.get("rpo_evidence_json") or "{}")
        out["drill"] = {
            "id": xid, "status": m.get("status"), "running": runner.is_running(xid),
            "step": step and {"id": step["id"], "title": step["title"], "phase": step["phase"], "type": step["type"],
                              "action": step.get("action") or "",
                              "params": {p["key"]: p["value"] for p in step.get("params", [])}},
            "steps_done": len(json.loads(m.get("completed_steps_json") or "[]")), "steps_total": len(plan.get("steps", [])),
            "t0": _utc_iso(m.get("t0")), "t_recovered": _utc_iso(m.get("t_recovered")),
            "rto_s": _num(m.get("rto_s")), "rpo_s": _num(m.get("rpo_s")), "chaos": bool(m.get("chaos_injected")),
            # live counts while the writer runs; afterwards the drill's evidence (writes checked, writes lost)
            "heartbeat": {"acked": sum(h.acked for h in hb) if hb else (ev.get("checked_writes") or 0),
                          "failed": sum(not h.acked for h in hb) if hb else None,
                          "acked_10s": sum(h.acked for h in recent), "failed_10s": sum(not h.acked for h in recent),
                          "lost": ev.get("lost_writes"), "writing": bool(r and getattr(r, "hb_writer", None))},
            "series": (M.write_series(hb)[-120:] if hb else json.loads(m.get("write_series_json") or "[]")[-120:]),
            "markers": {k: _epoch(m.get(k)) for k in ("t0", "t_failover", "t_failover_done", "t_recovered")},
            "lost_at": [_epoch(t) for t in (ev.get("lost_write_times") or [])],
            "load": _num(m.get("load_writes_per_s")) or 1,
            "asr": {"started": bool(m.get("iaas_started")), "done": bool(m.get("iaas_failover_done")),
                    "recovered_s": _num(m.get("iaas_recovered_s"))},
        }
    _map_cache.update(t=time.monotonic(), v=out)
    return out


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
    inv = trace.step("tool", f"Azure Resource Graph: resources in {config.workload_rg}", _safe, get_workload_inventory,
                     summary=lambda r: f"{len(r.get('resources', []))} resources found" if isinstance(r, dict) else "")
    return inv if isinstance(inv, dict) else {}


def recommend_strategy() -> dict:
    from dr_agent.agents import architect
    prof = get_profile()
    trace.emit("input", "Application profile", f"{prof.name}: tier {prof.tier}, RTO {prof.rto_min} min, RPO {prof.rpo_min} min",
               data=prof.model_dump())
    res = architect.run(prof, _inventory_summary())
    from dr_agent.tools.pricing import deployed_monthly_usd
    deployed = _safe(deployed_monthly_usd)
    meta = {"source": res.source, "via": res.via, "at": _now_iso(), "deployed_cost": deployed if "error" not in deployed else None}
    store.upsert_exercise(PHASE1, {"strategy_json": res.output.model_dump_json(), "strategy_meta_json": json.dumps(meta)})
    return {**res.output.model_dump(), **meta}


def _keyvault_check() -> dict:
    """Live call to Key Vault (secret read with the managed identity; the value is discarded)."""
    import os as _os
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    suffix = _os.environ.get("RESOURCE_SUFFIX") or config.webapp_a_name.rsplit("-", 1)[-1]
    vault = _os.environ.get("KEY_VAULT_NAME") or f"dra-kv-{suffix}"
    started = time.monotonic()
    try:
        SecretClient(f"https://{vault}.vault.azure.net", DefaultAzureCredential()).get_secret("console-passcode")
        return {"ok": True, "vault": vault, "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "vault": vault, "error": f"{type(e).__name__}: {e}"[:200]}


def gather_readiness_facts() -> dict:
    from dr_agent.tools.azure_trafficmanager import get_profile_status
    from dr_agent.tools.azure_webapp import list_app_settings
    from dr_agent.tools.heartbeat import replication_lag_seconds
    facts: dict[str, Any] = {}
    s = trace.step
    fog = s("tool", f"Azure SQL: failover group {config.sql_fog_name} state", _safe, lambda: runner._fog().status(),
            summary=lambda r: f"primary {r.get('primary_region')}")
    facts["fog_status"] = fog if "error" not in fog else {"primary_region": None, "servers": {}}
    facts["lag_s"] = s("tool", "SQL query: replication lag from the heartbeat table", _safe,
                       lambda: replication_lag_seconds(runner._sql_connect()),
                       summary=lambda r: f"{r:.1f}s behind" if isinstance(r, (int, float)) else "")
    if isinstance(facts["lag_s"], dict):
        facts["lag_s"] = None
    probe_sum = lambda r: f"HTTP {r.get('status_code', '?')} · {r.get('region', '')}".rstrip(" ·")  # noqa: E731
    facts["probe_a"] = s("tool", "HTTPS probe: East Asia /health", _safe, runner._probe, "A", summary=probe_sum)
    facts["probe_b"] = s("tool", "HTTPS probe: Southeast Asia /health", _safe, runner._probe, "B", summary=probe_sum)
    tm = s("tool", f"Traffic Manager: profile {config.tm_profile_name} endpoints", _safe, get_profile_status,
           summary=lambda r: f"{len(r.get('endpoints') or [])} endpoints")
    facts["tm_endpoints"] = [{"name": e.get("name"), "enabled": e.get("endpoint_status") == "Enabled",
                              "monitor_status": e.get("endpoint_monitor_status")}
                             for e in (tm.get("endpoints") or [])] if isinstance(tm, dict) else []
    sa = s("tool", f"App Service: app settings of {config.webapp_a_name}", _safe, list_app_settings, config.webapp_a_name,
           summary=lambda r: f"{len(r)} settings (names only are compared)")
    sb = s("tool", f"App Service: app settings of {config.webapp_b_name}", _safe, list_app_settings, config.webapp_b_name,
           summary=lambda r: f"{len(r)} settings (names only are compared)")
    if "error" not in sa and "error" not in sb:
        facts["settings_a"], facts["settings_b"] = sa, sb
    asr = s("tool", "Site Recovery: replication health of dra-vm-reports", _safe,
            lambda: __import__("dr_agent.tools.azure_asr", fromlist=["x"]).AsrTestFailover.from_env().status(),
            summary=lambda r: str(r.get("health") or r.get("state") or ""))
    facts["asr"] = asr if "error" not in asr else {"protected": False, "state": asr["error"]}
    facts["keyvault"] = s("tool", "Key Vault: read a secret with the console's managed identity", _keyvault_check,
                          summary=lambda r: f"{r.get('vault')} answered in {r.get('latency_ms')} ms" if r.get("ok") else "")
    facts["keyvault_ok"] = bool(facts["keyvault"].get("ok"))
    return facts


def check_readiness() -> dict:
    from dr_agent.agents import readiness
    checks = readiness.build_checks(gather_readiness_facts())
    n_fail = sum(c.status == "fail" for c in checks)
    trace.emit("check", f"{len(checks)} deterministic checks evaluated",
               f"{sum(c.status == 'pass' for c in checks)} pass, {sum(c.status == 'warn' for c in checks)} warn, {n_fail} fail"
               + (" (any fail forces NO-GO, whatever the agent says)" if n_fail else ""),
               status="fail" if n_fail else "ok")
    res = readiness.run(checks)
    meta = {"source": res.source, "via": res.via, "at": _now_iso()}
    store.upsert_exercise(PHASE1, {"readiness_json": res.output.model_dump_json(), "readiness_meta_json": json.dumps(meta)})
    return {**res.output.model_dump(mode="json"), **meta, "fixable": _fixable(res.output)}


def fix_progress() -> dict:
    """After a fix: is Southeast Asia serving again, and does Traffic Manager trust it again? (cheap, polled)"""
    from dr_agent.tools.azure_trafficmanager import get_profile_status
    probe = _safe(runner._probe, "B")
    tm = _safe(get_profile_status)
    ep = next((e for e in (tm.get("endpoints") or []) if e.get("name") == "endpoint-b"), {}) if isinstance(tm, dict) else {}
    app_ok = bool(probe.get("ok")) and probe.get("db_ok") is not False
    tm_ok = ep.get("endpoint_monitor_status") == "Online"
    if app_ok and tm_ok:
        _invalidate_summary()
    return {"app_ok": app_ok, "app_detail": "healthy" if app_ok else (probe.get("reason") or probe.get("error") or "restarting"),
            "tm_ok": tm_ok, "tm_detail": ep.get("endpoint_monitor_status") or "unknown", "done": app_ok and tm_ok}


def _fixable(report: ReadinessReport) -> list[str]:
    parity = next((c for c in report.checks if c.name.startswith("App settings parity")), None)
    return parity.evidence.get("missing_in_region_b", []) if parity and parity.status == "fail" else []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_phase1_state() -> dict:
    """Everything phase 1 has produced so far, so the page can be left and reopened without losing results."""
    p1 = _phase1()
    out: dict[str, Any] = {"profile": json.loads(p1["profile_json"]) if p1.get("profile_json") else None,
                           "profile_saved": bool(p1.get("profile_json")), "strategy": None, "readiness": None, "runbook": None}
    meta = lambda k: json.loads(p1[k]) if p1.get(k) else {}  # noqa: E731
    if p1.get("strategy_json"):
        out["strategy"] = {**json.loads(p1["strategy_json"]), **meta("strategy_meta_json")}
    if p1.get("readiness_json"):
        rep_ = ReadinessReport.model_validate_json(p1["readiness_json"])
        out["readiness"] = {**rep_.model_dump(mode="json"), **meta("readiness_meta_json"), "fixable": _fixable(rep_)}
    if p1.get("runbook_json"):
        try:
            rb = json.loads(p1["runbook_json"])
            md = RUNBOOK_PATH.read_text(encoding="utf-8") if RUNBOOK_PATH.exists() else ""
            out["runbook"] = {**rb, **meta("runbook_meta_json"), "original_md": md}
        except ValueError:
            out["runbook"] = None  # an older, truncated save
    if p1.get("fix_json"):
        out["last_fix"] = json.loads(p1["fix_json"])
    for k in ("strategy", "readiness", "runbook"):
        if out.get(k) and p1.get(f"{k}_trace_json"):
            out[k]["trace"] = json.loads(p1[f"{k}_trace_json"])
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
    store.upsert_exercise(PHASE1, {"fix_json": json.dumps({"copied": copied, "approved_by": approver, "at": _now_iso()})})
    _invalidate_summary()  # the Overview re-checks now instead of within 45 s
    return {"success": True, "copied": copied, "approved_by": approver,
            "message": (f"Copied {', '.join(copied)} to Southeast Asia (approved by {approver})." if copied
                        else "Nothing to copy: Southeast Asia already has these settings.")
                       + " The app restarts to load it; re-run readiness in about 60 seconds."}


def review_runbook() -> dict:
    from dr_agent.agents import runbook
    md = RUNBOOK_PATH.read_text(encoding="utf-8") if RUNBOOK_PATH.exists() else "1. (no runbook found)"
    trace.emit("input", f"Runbook {RUNBOOK_PATH.name}", f"{len(md.splitlines())} lines of markdown")
    inv = _inventory_summary()
    res = runbook.run(md, inv)
    meta = {"source": res.source, "via": res.via, "at": _now_iso()}
    saved = res.output.model_dump()
    raw = json.dumps(saved)
    if len(raw) > 30000:  # Azure Table string properties hold ~32K characters; keep valid JSON by shortening the long text
        over = len(raw) - 30000
        saved["updated_runbook_md"] = saved["updated_runbook_md"][:max(0, len(saved["updated_runbook_md"]) - over - 200)] + "\n\n…(shortened for storage)"
        raw = json.dumps(saved)
    store.upsert_exercise(PHASE1, {"runbook_json": raw, "runbook_meta_json": json.dumps(meta)})
    return {**res.output.model_dump(), **meta, "original_md": md}


# ------------------------------------------------------------------ plan
def generate_plan() -> dict:
    from dr_agent.agents import planner
    p1 = _phase1()
    strategy = StrategyRec.model_validate_json(p1["strategy_json"]) if p1.get("strategy_json") else None
    readiness = ReadinessReport.model_validate_json(p1["readiness_json"]) if p1.get("readiness_json") else None
    prof = get_profile()
    trace.emit("input", "Inputs from phase 1", f"strategy {strategy.recommended if strategy else 'warm-standby (default)'}; "
               f"{len(readiness.risks) if readiness else 0} readiness risks; allowlisted actions and the mandatory safe sequence")
    res = planner.run(prof, strategy, readiness, _inventory_summary())
    store.upsert_exercise(DRAFT, {"plan_json": res.output.model_dump_json(), "source": res.source, "via": res.via,
                                  "plan_at": _now_iso()})
    return {**res.output.model_dump(), "source": res.source, "via": res.via}


def get_draft_plan() -> dict:
    row = store.get_exercise(DRAFT) or {}
    raw = row.get("plan_json")
    if not raw:
        return {}
    return {**json.loads(raw), "source": row.get("source"), "via": row.get("via"),
            "at": row.get("plan_at"), "trace": json.loads(row["plan_trace_json"]) if row.get("plan_trace_json") else None}


LOAD_WRITES_PER_S = 20  # "under payment load" option: confirmed database writes per second during the drill


def approve_and_start(approver: str, plan_data: Optional[dict] = None, chaos: bool = False, load: bool = False) -> dict:
    if not approver.strip():
        return {"error": "approver name is required"}
    plan = ExercisePlan.model_validate(plan_data) if plan_data else ExercisePlan.model_validate(get_draft_plan())
    try:
        validate_plan(plan)
    except PlanError as e:
        return {"error": f"plan rejected by safety validation: {e}"}
    if runner.any_running():  # check before writing anything, so a second click cannot leave a ghost drill
        return {"error": "another exercise is already running"}
    p1 = _phase1()
    xid = "ex-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if store.get_exercise(xid):
        return {"error": "an exercise was started this second; refresh to see it"}
    now = datetime.now(timezone.utc).isoformat()
    store.upsert_exercise(xid, {
        "status": "approved", "plan_json": plan.model_dump_json(),
        "profile_json": get_profile().model_dump_json(),
        "strategy_json": p1.get("strategy_json"), "readiness_json": p1.get("readiness_json"),
        "runbook_json": (p1.get("runbook_json") or "")[:30000],
        "approvals_json": json.dumps({"plan": {"by": approver, "at": now}}),
        "confirmations_json": "{}", "completed_steps_json": "[]", "command": "", "t0": "",
        "chaos_armed": "true" if chaos else "", "chaos_injected": "",
        "load_writes_per_s": LOAD_WRITES_PER_S if load else 1,
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
        "diagnosis_source": m.get("diagnosis_source"), "diagnosis_via": m.get("diagnosis_via"),
        "chaos_armed": str(m.get("chaos_armed")).lower() == "true", "chaos_injected": m.get("chaos_injected") or None,
    }


def arm_chaos(xid: str) -> dict:
    """Arm the surprise fault; the engine injects it at the next failover/validation checkpoint."""
    m = store.get_exercise(xid) or {}
    if m.get("status") not in ("approved", "running", "waiting_human"):
        return {"error": f"exercise is {m.get('status')}; chaos only while it runs"}
    if m.get("t_recovered"):
        return {"error": "already recovered; inject the fault before recovery"}
    store.upsert_exercise(xid, {"chaos_armed": "true"})
    store.add_event(xid, "info", "Surprise fault armed by the presenter")
    return {"ok": True}


def confirm_step(xid: str, step_id: str, by: str) -> dict:
    m = store.get_exercise(xid) or {}
    # only the step the engine is actually waiting for can be confirmed (no approving failback in advance)
    if m.get("status") != "waiting_human" or m.get("waiting_step") != step_id:
        return {"error": f"step {step_id} is not waiting for confirmation"}
    conf = json.loads(m.get("confirmations_json") or "{}")
    conf[step_id] = {"by": by or "console user", "at": datetime.now(timezone.utc).isoformat()}
    store.upsert_exercise(xid, {"confirmations_json": json.dumps(conf)})
    return {"ok": True}


# Which commands make sense in which state. Anything else is refused, so a stale click (double-click, an old tab)
# can never approve a later failure or roll back the wrong drill.
COMMANDS_BY_STATUS = {
    "running": {"kill"}, "waiting_human": {"kill"},
    "paused_failed": {"kill", "retry", "continue", "rollback"},
    "killed": {"rollback", "abandon"}, "failed": {"rollback"},
}


def send_command(xid: str, cmd: str, by: str = "") -> dict:
    if cmd not in ("kill", "retry", "continue", "rollback", "abandon"):
        return {"error": "unknown command"}
    m = store.get_exercise(xid)
    if not m:
        return {"error": "exercise not found"}
    status = m.get("status") or ""
    if cmd not in COMMANDS_BY_STATUS.get(status, set()):
        return {"error": f"'{cmd}' is not possible while the exercise is {status or 'unknown'}"}
    alive = runner.is_running(xid)
    if cmd == "rollback" and not alive and runner.any_running():
        return {"error": "another exercise is running; roll this one back after it finishes"}
    store.upsert_exercise(xid, {"command": cmd})
    who = f" by {by}" if by else ""
    if cmd == "retry" and m.get("diagnosis_json"):
        d = json.loads(m["diagnosis_json"])
        if d.get("fix_action") and d["fix_action"] != "none":
            approvals = json.loads(m.get("approvals_json") or "{}")
            approvals[f"fix:{m.get('failed_step')}"] = {"by": by or "console user", "at": _now_iso(), "fix": d["fix_action"]}
            store.upsert_exercise(xid, {"approvals_json": json.dumps(approvals)})
            store.add_event(xid, "approval", f"Fix approved{who}: {d['fix_action']}", m.get("failed_step"))
    store.add_event(xid, "info", f"Command received: {cmd}{who}")
    # rollback requested while no runner is alive (e.g. after a crash): run it directly
    if cmd == "rollback" and not alive:
        try:
            runner.start_rollback(xid)
        except RuntimeError as e:
            return {"error": str(e)}
    return {"ok": True}


def list_exercises() -> list[dict]:
    """Newest first (ids are ex-YYYYMMDD-HHMMSS, so the id order is the start order)."""
    rows = [e for e in store.list_exercises(1000) if e.get("PartitionKey", "").startswith("ex-")]
    return sorted(rows, key=lambda e: e["PartitionKey"], reverse=True)[:50]


def _history_view(xid: str, m: dict) -> dict:
    """Trend for the Results page: earlier drills plus this one, and recurring remediation items."""
    from dr_agent.engine.history import drill_history, recurring
    try:
        h = drill_history(xid)
    except Exception as e:  # noqa: BLE001
        log.warning("history failed: %s", e)
        return {}
    items = [r.get("item", "") for r in json.loads(m.get("remediation_json") or "[]")]
    h["recurring"] = recurring(items, h)
    if _num(m.get("rto_s")) is not None:
        h["trend"] = h["trend"] + [{"id": xid, "rto_s": _num(m.get("rto_s")), "chaos": bool(m.get("chaos_injected")), "current": True}]
    return h


def _precise(sec):
    """Measured seconds shown exactly as recorded (2 decimals), never rounded to whole seconds."""
    if sec is None:
        return "—"
    return f"{sec:.2f}s" if sec < 60 else f"{int(sec // 60)}m {sec % 60:05.2f}s"


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
        "rto_display": fmt_duration(rto), "rto_precise": _precise(rto), "rpo_precise": _precise(rpo), "failback_precise": _precise(fb), "rto_met": rto_met, "rto_target_min": prof.rto_min, "rto_target_s": prof.rto_min * 60,
        "rpo_s": None if rpo is None else round(rpo, 1), "rpo_met": rpo_met,
        "rpo_target_min": prof.rpo_min, "rpo_target_s": prof.rpo_min * 60,
        "failback_display": fmt_duration(fb) if fb is not None else None,
        "overall_pass": rto_met and rpo_met,
        "t0": m.get("t0"), "t_failover": m.get("t_failover"), "t_recovered": m.get("t_recovered"),
        "t_failback_done": m.get("t_failback_done"),
        "last_hb_seq": ev.get("last_acked_seq"), "last_hb_ts": ev.get("last_acked_ts"),
        "dr_hb_seq": None, "dr_hb_ts": ev.get("newest_present_ts"),
        "remediation_items": json.loads(m.get("remediation_json") or "[]"),  # same items as the report
        "history": _history_view(xid, m),
        **_rto_accounting(xid, m, rto),
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


# ------------------------------------------------------------------ public live feed (judges' phones)
_live_cache: dict[str, Any] = {"t": 0.0, "v": None}
_serving_cache: dict[str, Any] = {"t": 0.0, "v": None}
PUBLIC_EVENT_KINDS = {"info", "measurement", "approval", "diagnosis", "chaos", "remediation"}  # no raw errors


def _public_message(e: dict) -> str:
    """The public feed (unauthenticated, shown on phones) never carries approver identities or raw diagnosis text."""
    kind, msg = e.get("kind"), (e.get("message") or "")
    if kind == "approval":
        return "Approved by the responsible team" if msg.lower().startswith(("confirmed", "plan approved")) else "Fix approved by an operator"
    if kind == "diagnosis":
        return "Validator agent diagnosed the failure and proposed a fix"
    if kind == "info" and msg.startswith("Command received"):
        return "Operator command received"
    return msg[:200]
FINISHED = ("completed", "rolled_back", "aborted", "failed", "killed")
DEFAULT_ETA_S = 45.0


def _serving_region() -> Optional[str]:
    if time.monotonic() - _serving_cache["t"] < 3:
        return _serving_cache["v"]
    r = _safe(lambda: runner._probe("tm"))
    v = r.get("region") if isinstance(r, dict) and r.get("ok") else None
    _serving_cache.update(t=time.monotonic(), v=v)
    return v


def _eta_basis(exclude: str) -> tuple[float, str]:
    """Median RTO of the last 3 clean drills (no chaos) = expected time to recover."""
    rtos = []
    for e in list_exercises():
        if e["PartitionKey"] == exclude or len(rtos) >= 3:
            continue
        full = store.get_exercise(e["PartitionKey"]) or {}
        if _num(full.get("rto_s")) and not full.get("chaos_injected"):
            rtos.append(_num(full.get("rto_s")))
    if not rtos:
        return DEFAULT_ETA_S, "typical for this setup"
    rtos.sort()
    return rtos[len(rtos) // 2], f"median of last {len(rtos)} drill{'s' if len(rtos) > 1 else ''}"


def public_live() -> dict:
    """Read-only, unauthenticated drill status for the Contoso app and the presenter view (cached 1 s)."""
    if _live_cache["v"] and time.monotonic() - _live_cache["t"] < 1.0:
        return _live_cache["v"]
    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {
        "state": "normal", "exercise_id": None, "headline": "All systems normal. DR drills verified by DR Agent.",
        "primary_region": "East Asia", "serving_region": _serving_region(), "server_time": now.isoformat(),
        "t0": None, "elapsed_s": None, "eta_s": None, "eta_basis": "", "rto_target_s": None, "rpo_target_s": None,
        "rto_s": None, "rpo_s": None, "rto_met": None, "rpo_met": None, "current_step": "", "steps_done": 0,
        "steps_total": 0, "chaos": False, "timeline": [],
        "iaas": {"state": "idle", "recovered_s": None},
    }
    ex = list_exercises()
    m = store.get_exercise(ex[0]["PartitionKey"]) if ex else None
    if m:
        xid = ex[0]["PartitionKey"]
        status = m.get("status") or ""
        finished_at = _dt(m.get("finished_at") or m.get("t_failback_done"))
        stale = status in FINISHED and (not finished_at or (now - finished_at).total_seconds() > 900)
        if not stale:
            prof = AppProfile.model_validate_json(m.get("profile_json") or AppProfile().model_dump_json())
            steps = json.loads(m.get("plan_json") or "{}").get("steps", [])
            titles = {s["id"]: s["title"] for s in steps}
            t0, t_rec = _dt(m.get("t0")), _dt(m.get("t_recovered"))
            out.update(exercise_id=xid, rto_target_s=prof.rto_min * 60, rpo_target_s=prof.rpo_min * 60,
                       rto_s=_num(m.get("rto_s")), rpo_s=_num(m.get("rpo_s")),
                       rto_met=_bool(m.get("rto_met")) if m.get("rto_s") not in (None, "") else None,
                       rpo_met=_bool(m.get("rpo_met")) if m.get("rpo_s") not in (None, "") else None,
                       current_step=titles.get(m.get("current_step") or m.get("waiting_step") or "", ""),
                       steps_done=len(json.loads(m.get("completed_steps_json") or "[]")), steps_total=len(steps),
                       chaos=bool(m.get("chaos_injected")), t0=t0.isoformat() if t0 else None)
            if t0:
                out["elapsed_s"] = round(((t_rec or now) - t0).total_seconds(), 1)
            iaas_s = _num(m.get("iaas_recovered_s"))
            out["iaas"] = {"state": "recovered" if iaas_s is not None else ("recovering" if m.get("iaas_started") else "idle"),
                           "recovered_s": iaas_s}
            if status in ("rolled_back",):
                out.update(state="rolled_back", headline="Drill rolled back. East Asia is serving again.")
            elif status == "completed":
                out.update(state="completed", headline="DR drill complete. Back in East Asia.")
            elif m.get("t_failback_done"):
                out.update(state="completed", headline="DR drill complete. Back in East Asia.")
            elif m.get("t_failback_start") and not m.get("t_failback_done"):
                out.update(state="failing_back", headline="Recovered. Now returning service to East Asia.")
            elif t_rec:
                out.update(state="recovered", headline=f"Recovered in Southeast Asia in {fmt_duration(out['rto_s'])}.")
            elif t0:
                eta, basis = _eta_basis(xid)
                out.update(state="paused" if status == "paused_failed" else "outage",
                           eta_s=round(max(0.0, eta - out["elapsed_s"]), 1), eta_basis=basis,
                           headline="East Asia is down. DR Agent is failing over to Southeast Asia.")
            elif status in ("approved", "running", "waiting_human"):
                out.update(state="armed", headline="A DR drill is about to start. Keep paying!")
            evs = [e for e in store.get_events(xid) if e.get("kind") in PUBLIC_EVENT_KINDS][-8:]
            out["timeline"] = [{"ts": e.get("ts") or e.get("Timestamp"), "kind": e.get("kind"),
                                "message": _public_message(e)} for e in evs]
    _live_cache.update(t=time.monotonic(), v=out)
    return out


def live_config() -> dict:
    """URLs for the presenter view: the public URL judges open, and the app URL users are routed to now."""
    import os
    public = os.environ.get("PUBLIC_URL") or ""
    r = _safe(runner.tm_resolve)
    access = get_access()
    app_url = public or (r.get("url") if isinstance(r, dict) else None)
    if not access["public"]:
        app_url = team_link()  # presenter phone frame opens the private app without typing the code
    return {"public_url": public, "app_url": app_url, "go_url": "/go", "team_link": team_link(), "access": access}


def _dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ------------------------------------------------------------------ public/private access to Contoso Payments
ACCESS = "settings"  # exercises-table row holding the access mode
DEFAULT_PUBLIC_MINUTES = 120
_access_cache: dict[str, Any] = {"t": 0.0, "v": None}


def get_access() -> dict:
    """Current mode. Public is time-boxed: it lapses back to private at `public_until` (fail closed)."""
    if _access_cache["v"] is not None and time.monotonic() - _access_cache["t"] < 2:
        return _access_cache["v"]
    row = store.get_exercise(ACCESS) or {}
    until = _dt(row.get("public_until"))
    public = _bool(row.get("public")) and bool(until) and datetime.now(timezone.utc) < until
    v = {"public": public, "public_until": until.isoformat() if public else None,
         "changed_by": row.get("changed_by"), "changed_at": row.get("changed_at")}
    _access_cache.update(t=time.monotonic(), v=v)
    return v


def set_access(public: bool, by: str, minutes: int = DEFAULT_PUBLIC_MINUTES) -> dict:
    by = (by or "").strip() or "console user"
    minutes = max(5, min(int(minutes or DEFAULT_PUBLIC_MINUTES), 8 * 60))
    now = datetime.now(timezone.utc)
    until = now + timedelta(minutes=minutes) if public else None
    store.upsert_exercise(ACCESS, {"public": "true" if public else "", "public_until": until.isoformat() if until else "",
                                   "changed_by": by, "changed_at": now.isoformat()})
    _access_cache.update(t=0.0, v=None)
    title = (f"Contoso Payments is now PUBLIC until {until.strftime('%H:%M')} UTC" if public
             else "Contoso Payments is now PRIVATE")
    log.warning("ACCESS MODE CHANGED: %s by %s", title, by)  # audit trail in App Service logs / Log Analytics
    try:
        from dr_agent.tools.notify import notify
        notify("info", title, f"Changed by {by}.", link="/live", send_email=False)
    except Exception:  # noqa: BLE001
        log.exception("could not record access change notification")
    return get_access()


def team_link() -> str:
    """Private link for the team (carries the access code once; the phone then remembers it)."""
    import os
    base = os.environ.get("PUBLIC_URL") or "/pay"
    code = os.environ.get("APP_ACCESS_CODE", "")
    return f"{base}?code={code}" if code else base


def _rto_accounting(xid: str, m: dict, rto: Optional[float]) -> dict:
    """Every second of the RTO, from the engine's own step timestamps (same clock as t0), plus the measurement
    precision and the RPO write-by-write evidence. Nothing here is estimated."""
    from dr_agent.engine.measurements import rto_breakdown
    segs = rto_breakdown(_dt(m.get("t0")), _dt(m.get("t_recovered")), store.get_events(xid)) if rto else []
    ev = json.loads(m.get("rto_evidence_json") or "{}")
    total = round(sum(s["seconds"] for s in segs), 3)
    for s in segs:
        s["pct"] = round(100 * s["seconds"] / total, 1) if total else 0
    rpo_ev = json.loads(m.get("rpo_evidence_json") or "{}")
    return {"rto_segments": segs, "rto_accounted_s": total, "rto_precision_s": ev.get("precision_s"),
            "rto_probe_interval_s": ev.get("probe_interval_s"),
            "rto_window": (ev.get("last_unhealthy_probe_end"), ev.get("first_healthy_probe_end")),
            "rpo_lost_writes": rpo_ev.get("lost_writes"), "rpo_note": rpo_ev.get("note")}
