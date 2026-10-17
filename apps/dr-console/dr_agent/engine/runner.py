"""Exercise engine: executes an approved plan step by step (requirements R5-R7, design §4.6).

- One background thread per running exercise (single App Service instance).
- Every transition is persisted (exercise row + events), so the UI only reads the store.
- Human steps pause until confirmed in the console. Kill switch / retry / rollback / continue are
  commands written to the exercise row by the web layer (see service.py) and polled here.
- RTO/RPO are computed from recorded timestamps only (engine/measurements.py).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from dr_agent.config import config
from dr_agent.engine import measurements as M
from dr_agent.engine.plan import validate_plan
from dr_agent.models import AppProfile, Diagnosis, ExercisePlan, PlanStep
from dr_agent.tools import store

log = logging.getLogger(__name__)

REGION_NAMES = {"A": "Central India", "B": "Southeast Asia"}
FINAL = {"completed", "rolled_back", "aborted"}
_RUNNERS: dict[str, "ExerciseRunner"] = {}
_LOCK = threading.Lock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def parse_dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s.replace(tzinfo=None)
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)


class StepFailed(Exception):
    pass


class Killed(Exception):
    pass


class RolledBack(Exception):
    pass


# ---------------------------------------------------------------- tool adapters
def _fog():
    from dr_agent.tools.azure_sql import FailoverGroupController
    return FailoverGroupController(config.azure_subscription_id, config.workload_rg, config.sql_fog_name,
                                   {"A": config.sql_server_a, "B": config.sql_server_b})


def _sql_connect():
    import os
    from dr_agent.tools.heartbeat import sql_connector
    return sql_connector(f"{config.sql_fog_name}.database.windows.net", os.environ.get("SQL_DB", "paymentsdb"),
                         os.environ.get("SQL_USER", "sqladmin"), os.environ.get("SQL_PASSWORD", ""))


def tm_resolve() -> dict:
    """Fresh (uncached) DNS resolution of the Traffic Manager name -> the app hostname TM routes users to.

    App Service on this subscription does not activate the trafficmanager.net hostname binding, so clients
    follow TM's DNS answer and call the app's own hostname: the same routing decision, made by Traffic Manager.
    """
    import dns.resolver
    r = dns.resolver.Resolver()
    r.cache = None
    try:
        cname = str(r.resolve(config.tm_fqdn, "CNAME", lifetime=5)[0].target).rstrip(".")
    except Exception as e:  # noqa: BLE001
        return {"error": f"DNS: {e}"[:200]}
    region = "A" if config.webapp_a_name in cname else "B" if config.webapp_b_name in cname else None
    return {"cname": cname, "dns_region": region, "url": f"https://{cname}"}


def _probe(target: str) -> dict:
    """target: tm | A | B. Adds `region_key` (A/B) derived from the app's reported region name."""
    from dr_agent.tools import probes
    if target == "tm":
        dns_info = tm_resolve()
        if "error" in dns_info:
            r = {"ok": False, **dns_info}
        else:
            r = probes.probe(dns_info["url"] + "/health")
            r.update({"tm_fqdn": config.tm_fqdn, "routed_to": dns_info["cname"], "dns_region": dns_info["dns_region"],
                      "app_url": dns_info["url"]})
    else:
        r = probes.probe_direct(target)
    name = (r.get("region") or "").lower()
    r["region_key"] = next((k for k, v in REGION_NAMES.items() if v.lower() == name), None)
    return r


class ExerciseRunner(threading.Thread):
    def __init__(self, exercise_id: str):
        super().__init__(name=f"exercise-{exercise_id}", daemon=True)
        self.xid = exercise_id
        self.heartbeats: list[M.HeartbeatRecord] = []
        self.hb_writer = None
        self.rolling_back = False

    # ------------------------------------------------------------ state helpers
    def meta(self) -> dict:
        return store.get_exercise(self.xid) or {}

    def update(self, **fields) -> None:
        store.upsert_exercise(self.xid, {k: (iso(v) if isinstance(v, datetime) else v) for k, v in fields.items()})

    def event(self, kind: str, message: str, step_id: Optional[str] = None, data: Optional[dict] = None) -> None:
        try:
            store.add_event(self.xid, kind, message, step_id, data)
        except Exception:  # noqa: BLE001
            log.exception("could not write event")

    def notify(self, kind: str, title: str, body: str) -> None:
        try:
            from dr_agent.tools.notify import notify
            notify(kind, title, body, link=f"/exercise/{self.xid}", exercise_id=self.xid,
                   send_email=kind in ("action", "success", "failure"))
        except Exception:  # noqa: BLE001
            log.exception("notify failed")

    def command(self) -> Optional[str]:
        return (self.meta().get("command") or None)

    def clear_command(self) -> None:
        self.update(command="")

    def check_kill(self) -> None:
        if self.command() == "kill":
            raise Killed()

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check_kill()
            time.sleep(min(1.0, end - time.monotonic()))

    def plan(self) -> ExercisePlan:
        return ExercisePlan.model_validate_json(self.meta()["plan_json"])

    def profile(self) -> AppProfile:
        return AppProfile.model_validate_json(self.meta().get("profile_json") or AppProfile().model_dump_json())

    # ------------------------------------------------------------ main loop
    def run(self) -> None:
        try:
            m = self.meta()
            plan = self.plan()
            validate_plan(plan)
            done = set(json.loads(m.get("completed_steps_json") or "[]"))
            self.update(status="running", started_at=m.get("started_at") or iso(utcnow()))
            if not done:
                self.event("info", "Exercise started")
                self.notify("info", "DR exercise started",
                            f"{self.profile().name}: targets RTO {self.profile().rto_min} min, RPO {self.profile().rpo_min} min.")
            for step in plan.steps:
                if step.id in done:
                    continue
                self.run_step(step)
                done.add(step.id)
                self.update(completed_steps_json=json.dumps(sorted(done)), current_step="")
            self.update(status="completed", finished_at=iso(utcnow()))
            self.event("info", "Exercise completed")
        except RolledBack:
            pass  # status already set by rollback()
        except Killed:
            self.update(status="killed", current_step="")
            self.event("error", "Kill switch activated: engine stopped. Use Rollback to restore the primary region.")
            self.notify("failure", "DR exercise stopped (kill switch)", "Rollback is available in the console.")
            self.clear_command()
            self.wait_for_rollback_after_kill()
        except Exception as e:  # noqa: BLE001
            log.exception("exercise crashed")
            self.update(status="failed", error=f"{type(e).__name__}: {e}"[:1000])
            self.event("error", f"Engine error: {e}", data={"trace": traceback.format_exc()[-2000:]})
        finally:
            self.stop_heartbeat()
            with _LOCK:
                _RUNNERS.pop(self.xid, None)

    def wait_for_rollback_after_kill(self) -> None:
        while True:
            c = self.command()
            if c == "rollback":
                self.clear_command()
                self.rollback()
                return
            if c == "abandon":
                self.update(status="aborted")
                return
            time.sleep(1)

    # ------------------------------------------------------------ steps
    def run_step(self, step: PlanStep) -> None:
        while True:
            self.check_kill()
            self.maybe_chaos(step.phase)
            self.update(current_step=step.id)
            self.event("step_start", step.title, step.id, {"type": step.type, "owner": step.owner_team,
                                                          "action": step.action, "params": step.param_dict()})
            started = time.monotonic()
            try:
                if step.type == "human":
                    result = self.wait_human(step)
                else:
                    result = self.execute(step)
                self.event("step_end", f"{step.title}: done", step.id,
                           {"ok": True, "duration_s": round(time.monotonic() - started, 1), "result": result})
                return
            except (Killed, RolledBack):
                raise
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"[:800]
                self.event("step_end", f"{step.title}: FAILED: {err}", step.id,
                           {"ok": False, "duration_s": round(time.monotonic() - started, 1), "error": err})
                if not step.abort_on_failure:
                    self.event("info", f"{step.id} failed but is non-blocking; continuing")
                    return
                decision = self.handle_failure(step, err)
                if decision == "retry":
                    self.apply_fix(step)
                    continue
                if decision == "continue":
                    return
                raise Killed() if decision == "kill" else StepFailed(err)

    def wait_human(self, step: PlanStep) -> dict:
        self.update(status="waiting_human", waiting_step=step.id)
        self.notify("action", f"Action needed: {step.title}",
                    f"Owner: {step.owner_team} team. Confirm in the DR console when done.")
        while True:
            self.check_kill()
            conf = json.loads(self.meta().get("confirmations_json") or "{}")
            if step.id in conf:
                self.update(status="running", waiting_step="")
                self.event("approval", f"Confirmed by {conf[step.id].get('by')}", step.id, conf[step.id])
                return conf[step.id]
            time.sleep(1.5)

    def handle_failure(self, step: PlanStep, err: str) -> str:
        from dr_agent.agents import validator
        recent = store.get_events(self.xid)[-15:]
        diag = validator.run(step, err, recent, {"A": _safe(lambda: _probe("A")), "B": _safe(lambda: _probe("B")),
                                                 "tm": _safe(lambda: _probe("tm"))})
        self.update(status="paused_failed", failed_step=step.id, diagnosis_json=diag.output.model_dump_json(),
                    diagnosis_source=diag.source, diagnosis_via=diag.via)
        self.event("diagnosis", diag.output.likely_cause, step.id, diag.output.model_dump())
        self.notify("failure", f"Step failed: {step.title}",
                    f"{diag.output.likely_cause} Recommended: {diag.output.recommended_action}.")
        while True:
            c = self.command()
            if c in ("retry", "continue", "rollback", "kill"):
                self.clear_command()
                self.update(status="running", failed_step="")
                if c == "rollback":
                    self.rollback()
                    raise RolledBack()
                return c
            time.sleep(1)

    # ------------------------------------------------------------ actions
    def execute(self, step: PlanStep) -> dict:
        p = step.param_dict()
        a = step.action
        if a == "snapshot_inventory":
            from dr_agent.tools.inventory import snapshot_inventory
            snap = snapshot_inventory()
            key = "inventory_after_json" if step.phase == "closure" else "inventory_before_json"
            self.update(**{key: json.dumps(snap, default=str)[:30000]})
            return {"resources": len(snap.get("resources", [])) if isinstance(snap, dict) else None}
        if a == "check_fog":
            st = _fog().status()
            if st.get("primary_region") != "A":
                raise StepFailed(f"Region A must be SQL primary, found {st.get('primary_region')}")
            return st
        if a == "start_heartbeat":
            return self.start_heartbeat()
        if a == "inject_outage":
            from dr_agent.tools.azure_webapp import stop_webapp_region
            t0 = utcnow()
            res = stop_webapp_region(p["region"])
            self.update(t0=t0)
            self.notify("info", "Outage injected", f"Region {p['region']} ({REGION_NAMES[p['region']]}) web app stopped. RTO clock started.")
            return {"t0": iso(t0), **_jsonable(res)}
        if a == "sql_failover":
            target, forced = p["target_region"], p["allow_data_loss"].lower() == "true"
            if forced and not self.rolling_back:
                self.update(t_failover=utcnow())
            res = _fog().failover(target, forced)
            return res
        if a == "tm_set_endpoint":
            from dr_agent.tools.azure_trafficmanager import set_endpoint_region
            return _jsonable(set_endpoint_region(p["region"], p["enabled"].lower() == "true"))
        if a == "wait_healthy":
            return self.wait_healthy(p["target"], p["expect_region"], int(p.get("timeout_s", 900)), step.phase)
        if a == "functional_txn_check":
            return self.functional_check()
        if a == "measure_rpo":
            return self.measure_rpo()
        if a == "start_webapp":
            from dr_agent.tools.azure_webapp import start_webapp_region
            if p["region"] == "A" and step.phase == "failback":
                self.update(t_failback_start=utcnow())
            return _jsonable(start_webapp_region(p["region"]))
        if a == "stop_heartbeat":
            return self.stop_heartbeat()
        if a == "generate_report":
            from dr_agent.engine.reporting import build_report
            url = build_report(self.xid)
            self.notify("success", "DR exercise report ready", "The DR exercise finished and the report is published.")
            return {"report": url}
        raise StepFailed(f"unknown action {a}")

    def wait_healthy(self, target: str, expect: str, timeout_s: int, phase: str) -> dict:
        deadline = time.monotonic() + timeout_s
        attempts, last = 0, {}
        while time.monotonic() < deadline:
            self.check_kill()
            self.maybe_chaos(phase)
            attempts += 1
            if target == "tm":
                self.assert_endpoint_enabled(expect)
            last = _probe(target)
            if last.get("ok") and last.get("db_ok") and last.get("region_key") == expect:
                t = utcnow()
                if target == "tm" and phase == "validation" and expect == "B":
                    self.record_recovery(t)
                if target == "tm" and phase == "failback" and expect == "A":
                    self.record_failback_done(t)
                return {"attempts": attempts, "served_by": last.get("region"), "db_server": last.get("db_server")}
            if attempts % 6 == 1:
                self.event("info", f"waiting for {target} to serve from Region {expect} "
                                   f"(now: {last.get('region') or last.get('error') or last.get('status_code')})")
            self.sleep(5)
        raise StepFailed(f"{target} not healthy from Region {expect} within {timeout_s}s; last probe: {last}")

    def assert_endpoint_enabled(self, region: str) -> None:
        """Fail fast (instead of waiting for the timeout) if Traffic Manager cannot route to `region` at all."""
        from dr_agent.tools.azure_trafficmanager import get_endpoint
        ep = _safe(lambda: get_endpoint(ENDPOINTS[region]))
        if isinstance(ep, dict) and ep.get("endpoint_status") == "Disabled":
            raise StepFailed(f"Traffic Manager endpoint {ENDPOINTS[region]} (Region {region}) is Disabled, so Traffic "
                             f"Manager has no endpoint to route users to Region {region}.")

    # ------------------------------------------------------------ chaos + remediation
    def maybe_chaos(self, phase: str) -> None:
        """Inject the armed surprise fault: disable the Region B endpoint (a bad change made during the incident)."""
        m = self.meta()
        if str(m.get("chaos_armed")).lower() != "true" or m.get("chaos_injected") or m.get("t_recovered"):
            return
        if phase not in ("failover", "validation") or self.rolling_back:
            return
        from dr_agent.tools.azure_trafficmanager import set_endpoint_region
        set_endpoint_region("B", False)
        self.update(chaos_injected=iso(utcnow()))
        self.event("chaos", "Surprise fault injected: Region B Traffic Manager endpoint disabled")
        self.notify("info", "Surprise fault injected", "The Region B Traffic Manager endpoint was disabled mid-failover.")

    def apply_fix(self, step: PlanStep) -> None:
        """Run the Validator's remediation (already approved by the human who clicked retry)."""
        raw = self.meta().get("diagnosis_json")
        if not raw:
            return
        diag = Diagnosis.model_validate_json(raw)
        if diag.fix_action == "none":
            return
        params = {p.key: p.value.strip().lower() for p in diag.fix_params}
        problem = check_fix(diag.fix_action, params, step.phase)
        if problem:
            self.event("error", f"Proposed fix rejected by the engine: {problem}", step.id)
            return
        from dr_agent.tools.azure_trafficmanager import set_endpoint_region
        from dr_agent.tools.azure_webapp import start_webapp_region
        if diag.fix_action == "tm_set_endpoint":
            res = set_endpoint_region(params["region"].upper(), params["enabled"] == "true")
        else:
            res = start_webapp_region(params["region"].upper())
        self.update(diagnosis_json="")
        self.event("remediation", f"Approved fix applied: {diag.fix_action} {params}", step.id, _jsonable(res))

    def record_recovery(self, t: datetime) -> None:
        m = self.meta()
        t0 = parse_dt(m.get("t0"))
        rto = M.compute_rto(t0, t)
        prof = self.profile()
        ev = M.evaluate(rto, _num(m.get("rpo_s")), prof.rto_min, prof.rpo_min)
        self.update(t_recovered=t, rto_s=rto, rto_met=ev["rto_met"])
        self.event("measurement", f"Recovered in {M.fmt_duration(rto)} (target {prof.rto_min} min): "
                                  f"{'MET' if ev['rto_met'] else 'MISSED'}", data={"rto_s": rto, "t0": iso(t0), "t_recovered": iso(t)})
        self.notify("success" if ev["rto_met"] else "failure", "Application recovered in Region B",
                    f"RTO {M.fmt_duration(rto)} vs target {prof.rto_min} min.")

    def record_failback_done(self, t: datetime) -> None:
        start = parse_dt(self.meta().get("t_failback_start"))
        fb = M.compute_rto(start, t)
        self.update(t_failback_done=t, failback_s=fb)
        self.event("measurement", f"Failback completed in {M.fmt_duration(fb)}", data={"failback_s": fb})

    def functional_check(self) -> dict:
        import httpx
        dns_info = tm_resolve()
        if "error" in dns_info:
            raise StepFailed(dns_info["error"])
        base = dns_info["url"]
        import os
        hdr = {"X-Access-Code": os.environ.get("APP_ACCESS_CODE", "")}  # the agent works in private mode too
        r = httpx.post(f"{base}/api/txn", timeout=10, headers=hdr)
        r.raise_for_status()
        txn = r.json()
        recent = httpx.get(f"{base}/api/recent", timeout=10, headers=hdr).json()
        if not any(str(x.get("id")) == str(txn.get("id")) for x in recent):
            raise StepFailed("payment written but not readable via Traffic Manager")
        return {"txn": txn}

    # ------------------------------------------------------------ heartbeat / RPO
    def start_heartbeat(self) -> dict:
        from dr_agent.tools.heartbeat import HeartbeatWriter, ensure_table
        connect = _sql_connect()
        ensure_table(connect)

        def on_result(seq, ts, acked, error):
            self.heartbeats.append(M.HeartbeatRecord(seq, ts, acked))
            try:
                store.record_heartbeat(seq, ts, acked, error)
            except Exception:  # noqa: BLE001
                pass
        self.hb_writer = HeartbeatWriter(connect, on_result, interval=2.0)
        self.hb_writer.start()
        self.sleep(5)
        acked = sum(1 for h in self.heartbeats if h.acked)
        if not acked:
            raise StepFailed("heartbeat writer could not write to the database")
        return {"acked": acked}

    def stop_heartbeat(self) -> dict:
        if self.hb_writer:
            self.hb_writer.stop()
            self.hb_writer = None
        return {"written": len(self.heartbeats), "acked": sum(1 for h in self.heartbeats if h.acked)}

    def measure_rpo(self) -> dict:
        from dr_agent.tools.heartbeat import newest_present
        m = self.meta()
        t_fo = parse_dt(m.get("t_failover"))
        if not t_fo:
            raise StepFailed("no failover timestamp recorded")
        newest, present = newest_present(_sql_connect(), t_fo)
        res = M.compute_rpo(self.heartbeats, t_fo, newest, present)
        prof = self.profile()
        ev = M.evaluate(_num(m.get("rto_s")), res.rpo_s, prof.rto_min, prof.rpo_min)
        evidence = {"t_failover": iso(t_fo), "last_acked_seq": res.last_acked_seq, "last_acked_ts": iso(res.last_acked_ts),
                    "newest_present_ts": iso(res.newest_present_ts), "lost_writes": len(res.lost_seqs), "note": res.note}
        self.update(rpo_s=res.rpo_s, rpo_met=ev["rpo_met"], rpo_evidence_json=json.dumps(evidence))
        self.event("measurement", f"RPO {M.fmt_duration(res.rpo_s)} (target {prof.rpo_min} min): "
                                  f"{'MET' if ev['rpo_met'] else 'MISSED'}. {res.note}", data=evidence)
        return evidence

    # ------------------------------------------------------------ rollback
    def rollback(self) -> None:
        """Restore Region A as primary (used after kill or on a failed step)."""
        from dr_agent.tools.azure_trafficmanager import set_endpoint_region
        from dr_agent.tools.azure_webapp import start_webapp_region
        self.rolling_back = True
        self.update(status="rolling_back")
        self.event("info", "Rollback started: restoring Region A")
        try:
            start_webapp_region("A")
            self.wait_healthy_simple("A", 600)
            if _fog().status().get("primary_region") != "A":
                _fog().failover("A", False)
            set_endpoint_region("A", True)
            self.stop_heartbeat()
            self.update(status="rolled_back", current_step="", finished_at=iso(utcnow()))
            self.event("info", "Rollback complete: Region A is primary again")
            self.notify("success", "Rollback complete", "Region A (Central India) is serving again.")
        except Exception as e:  # noqa: BLE001
            self.update(status="failed", error=f"rollback failed: {e}"[:1000])
            self.event("error", f"Rollback failed: {e}")
            self.notify("failure", "Rollback failed", str(e)[:300])

    def wait_healthy_simple(self, region: str, timeout_s: int) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            r = _probe(region)
            if r.get("ok") and r.get("db_ok") is not False:
                return
            time.sleep(5)
        raise StepFailed(f"Region {region} not healthy after {timeout_s}s")


ENDPOINTS = {"A": "endpoint-a", "B": "endpoint-b"}
FIX_PARAMS = {"tm_set_endpoint": {"region", "enabled"}, "start_webapp": {"region"}}


def check_fix(action: str, params: dict, phase: str) -> Optional[str]:
    """Allowlist for Validator remediations. During the drill Region A must stay down (no cheating the outage)."""
    if action not in FIX_PARAMS:
        return f"action {action!r} not allowed"
    if set(params) != FIX_PARAMS[action]:
        return f"params must be {sorted(FIX_PARAMS[action])}"
    if params.get("region", "").upper() not in ("A", "B"):
        return "region must be A or B"
    if params.get("enabled", "true") not in ("true", "false"):
        return "enabled must be true/false"
    if phase in ("outage", "failover", "validation") and params["region"].upper() == "A":
        return "Region A must stay down during the outage"
    return None


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


# ---------------------------------------------------------------- public API
def start(exercise_id: str) -> None:
    with _LOCK:
        if exercise_id in _RUNNERS:
            return
        if any(r.is_alive() for r in _RUNNERS.values()):
            raise RuntimeError("another exercise is already running")
        r = ExerciseRunner(exercise_id)
        _RUNNERS[exercise_id] = r
        r.start()


def is_running(exercise_id: str) -> bool:
    r = _RUNNERS.get(exercise_id)
    return bool(r and r.is_alive())


def resume_interrupted() -> list[str]:
    """Called at app startup: restart runners for exercises that were mid-flight when the app restarted."""
    resumed = []
    for e in store.list_exercises(20):
        xid = e["PartitionKey"]
        full = store.get_exercise(xid) or {}
        if full.get("status") in ("running", "waiting_human") and not is_running(xid):
            store.add_event(xid, "info", "Console restarted: resuming exercise")
            start(xid)
            resumed.append(xid)
    return resumed
