"""Chaos remediation allowlist, Validator fix proposal and the public live feed state machine."""
import json
from datetime import datetime, timedelta, timezone

from dr_agent.agents import validator
from dr_agent.engine.plan import default_plan
from dr_agent.engine.runner import check_fix


def test_check_fix_allowlist():
    assert check_fix("tm_set_endpoint", {"region": "b", "enabled": "true"}, "validation") is None
    assert check_fix("start_webapp", {"region": "B"}, "failover") is None
    assert "Region A" in check_fix("start_webapp", {"region": "A"}, "validation")
    assert check_fix("start_webapp", {"region": "A"}, "failback") is None
    assert "not allowed" in check_fix("sql_failover", {"target_region": "B"}, "failover")
    assert "params" in check_fix("tm_set_endpoint", {"region": "B"}, "failover")


def test_validator_fallback_proposes_endpoint_fix():
    step = next(s for s in default_plan().steps if s.id == "val-1")
    d = validator.fallback(step, "StepFailed: Traffic Manager endpoint endpoint-b (Region B) is Disabled, so ...")
    assert d.recommended_action == "retry" and d.fix_action == "tm_set_endpoint"
    assert {p.key: p.value for p in d.fix_params} == {"region": "B", "enabled": "true"}
    d2 = validator.fallback(step, "TimeoutError: timed out")
    assert d2.fix_action == "none" and d2.recommended_action == "retry"


def _fake_store(monkeypatch, meta, events=()):
    from dr_agent import service
    monkeypatch.setattr(service.store, "list_exercises", lambda n=50: [{"PartitionKey": "ex-20261017-100000"}])
    monkeypatch.setattr(service.store, "get_exercise", lambda xid: meta)
    monkeypatch.setattr(service.store, "get_events", lambda xid, after=0: list(events))
    monkeypatch.setattr(service, "_serving_region", lambda: "Southeast Asia")
    service._live_cache.update(t=0.0, v=None)
    return service


def test_public_live_outage_then_recovered(monkeypatch):
    now = datetime.now(timezone.utc)
    meta = {"status": "running", "t0": (now - timedelta(seconds=10)).isoformat(),
            "plan_json": default_plan().model_dump_json(), "completed_steps_json": json.dumps(["pre-1", "pre-2"]),
            "current_step": "fo-1"}
    svc = _fake_store(monkeypatch, meta, [{"kind": "info", "message": "hi", "ts": now.isoformat()},
                                          {"kind": "step_start", "message": "hidden"}])
    out = svc.public_live()
    assert out["state"] == "outage" and 9 <= out["elapsed_s"] <= 12
    assert out["eta_s"] is not None and out["steps_total"] == 21 and out["steps_done"] == 2
    assert out["current_step"].startswith("Forced SQL failover") and [e["message"] for e in out["timeline"]] == ["hi"]
    assert out["rto_target_s"] == 30 * 60

    meta.update(t_recovered=(now - timedelta(seconds=2)).isoformat(), rto_s="8", rto_met="true")
    svc._live_cache.update(t=0.0, v=None)
    out = svc.public_live()
    assert out["state"] == "recovered" and out["rto_s"] == 8 and out["rto_met"] is True and out["eta_s"] is None


def test_public_live_normal_when_last_drill_is_old(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    svc = _fake_store(monkeypatch, {"status": "completed", "finished_at": old})
    assert svc.public_live()["state"] == "normal"


def test_access_mode_is_time_boxed_and_fails_closed(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from dr_agent import service
    rows = {}
    monkeypatch.setattr(service.store, "upsert_exercise", lambda pk, d: rows.setdefault(pk, {}).update(d))
    monkeypatch.setattr(service.store, "get_exercise", lambda pk: rows.get(pk))
    import dr_agent.tools.notify as n
    monkeypatch.setattr(n, "notify", lambda *a, **k: None)
    service._access_cache.update(t=0.0, v=None)
    assert service.get_access()["public"] is False                      # default private
    assert service.set_access(True, "kishal", 30)["public"] is True
    assert rows["settings"]["changed_by"] == "kishal"
    rows["settings"]["public_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    service._access_cache.update(t=0.0, v=None)
    assert service.get_access()["public"] is False                      # lapsed -> private
    assert service.set_access(False, "kishal")["public"] is False


def test_entra_identity_only_trusted_when_enabled(monkeypatch):
    from fastapi.testclient import TestClient
    from web import main
    c = TestClient(main.app)
    spoof = {"X-MS-CLIENT-PRINCIPAL-NAME": "attacker@evil.com"}
    monkeypatch.delenv("ENTRA_AUTH_ENABLED", raising=False)
    assert c.get("/api/me", headers=spoof).status_code == 401          # header ignored without Entra
    monkeypatch.setenv("ENTRA_AUTH_ENABLED", "true")
    assert c.get("/api/me", headers=spoof).status_code == 401          # still ignored: platform auth is off
    monkeypatch.setenv("WEBSITE_AUTH_ENABLED", "True")
    r = c.get("/api/me", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "kishalr@virtusacsp.onmicrosoft.com"})
    assert r.status_code == 200 and r.json() == {"user": "kishalr@virtusacsp.onmicrosoft.com", "auth": "entra"}


def test_asr_readiness_check():
    from dr_agent.agents.readiness import asr_check
    assert asr_check({"protected": True, "health": "Normal", "rpo_s": 42}).status == "pass"
    c = asr_check({"protected": False, "state": "Enabling protection", "initial_replication_pct": 37})
    assert c.status == "warn" and "37%" in c.detail


def test_default_plan_includes_non_blocking_iaas_steps():
    from dr_agent.engine.plan import default_plan, validate_plan
    p = default_plan()
    validate_plan(p)
    iaas = [s for s in p.steps if s.action and s.action.startswith("asr_")]
    assert [s.action for s in iaas] == ["asr_test_failover", "asr_verify_recovery", "asr_test_cleanup"]
    assert all(not s.abort_on_failure for s in iaas)


def test_runner_sleep_never_negative(monkeypatch):
    import time as _t
    from dr_agent.engine.runner import ExerciseRunner
    r = ExerciseRunner("ex-test")
    monkeypatch.setattr(r, "check_kill", lambda: _t.sleep(0.05))  # slow kill check outlasts the remaining time
    r.sleep(0.01)


def test_results_page_gets_the_same_remediation_items_as_the_report(monkeypatch):
    import json
    from dr_agent import service
    items = [{"item": "Update runbook", "owner": "app", "priority": "high"}]
    monkeypatch.setattr(service.store, "get_exercise", lambda xid: {"status": "completed", "remediation_json": json.dumps(items)})
    monkeypatch.setattr(service.store, "get_events", lambda xid, after=0: [])
    ctx = service.results_context("ex-20261018-000000")
    assert ctx["remediation_items"] == items


def test_overview_drill_stats_median_excludes_chaos(monkeypatch):
    from dr_agent import service
    rows = {"ex-20261018-000003": {"status": "completed", "rto_s": "34.35", "rpo_s": "0", "iaas_recovered_s": "345"},
            "ex-20261018-000002": {"status": "completed", "rto_s": "57.36", "rpo_s": "0", "chaos_injected": "endpoint-b"},
            "ex-20261018-000001": {"status": "completed", "rto_s": "29.76", "rpo_s": "0"},
            "ex-20261017-000001": {"status": "completed", "rto_s": "40.43", "rpo_s": "0"},
            "ex-20261017-000000": {"status": "failed", "rto_s": ""}}
    monkeypatch.setattr(service, "list_exercises", lambda: [{"PartitionKey": k} for k in sorted(rows, reverse=True)])
    monkeypatch.setattr(service.store, "get_exercise", lambda k: rows.get(k))
    st = service._drill_stats()
    assert st["count"] == 4 and st["count_plain"] == 3 and st["median_rto_s"] == 34.35
    assert st["best_rto_s"] == 29.76 and st["chaos_ids"] == ["ex-20261018-000002"] and st["latest_iaas_s"] == 345
    assert st["latest"]["id"] == "ex-20261018-000003"


def test_fix_may_never_disable_the_dr_region_during_the_outage():
    assert "disabling" in check_fix("tm_set_endpoint", {"region": "b", "enabled": "false"}, "validation")
    assert check_fix("tm_set_endpoint", {"region": "b", "enabled": "true"}, "validation") is None


def test_commands_only_in_the_right_state(monkeypatch):
    from dr_agent import service
    rows = {"ex-1": {"status": "running"}, "ex-2": {"status": "paused_failed", "diagnosis_json": ""}}
    writes = []
    monkeypatch.setattr(service.store, "get_exercise", lambda k: rows.get(k))
    monkeypatch.setattr(service.store, "upsert_exercise", lambda k, d: writes.append((k, d)))
    monkeypatch.setattr(service.store, "add_event", lambda *a, **k: None)
    monkeypatch.setattr(service.runner, "is_running", lambda k: True)
    assert "not possible" in service.send_command("ex-1", "retry")["error"]        # stale retry while running
    assert "not found" in service.send_command("ex-9", "rollback")["error"]        # unknown drill
    assert service.send_command("ex-2", "retry", "Kishal") == {"ok": True}
    assert writes == [("ex-2", {"command": "retry"})]


def test_only_the_waiting_step_can_be_confirmed(monkeypatch):
    from dr_agent import service
    monkeypatch.setattr(service.store, "get_exercise", lambda k: {"status": "waiting_human", "waiting_step": "pre-4"})
    monkeypatch.setattr(service.store, "upsert_exercise", lambda k, d: None)
    assert "not waiting" in service.confirm_step("ex-1", "fb-1", "Kishal")["error"]   # no approving failback early
    assert service.confirm_step("ex-1", "pre-4", "Kishal") == {"ok": True}


def test_public_feed_hides_approver_identities():
    from dr_agent import service
    assert "kishal" not in service._public_message({"kind": "approval", "message": "Confirmed by kishalr@virtusa.com"}).lower()
    assert service._public_message({"kind": "diagnosis", "message": "SQL error 40613 on server x"}).startswith("Validator")


def test_login_lockout_after_repeated_failures(monkeypatch):
    from web import auth
    monkeypatch.setattr(auth.time, "sleep", lambda s: None)
    auth._fails.clear()
    for _ in range(auth.PER_IP):
        auth.login_failed("10.0.0.9")
    assert auth.login_blocked("10.0.0.9") > 0 and auth.login_blocked("10.0.0.10") == 0
    auth._fails.clear()


def _diag(fix, params):
    from dr_agent.models import Diagnosis, Param
    return Diagnosis(likely_cause="c", evidence=["e"], recommended_action="retry", fix_action=fix,
                     fix_params=[Param(key=k, value=v) for k, v in params.items()])


def test_redundant_fix_judged_from_live_evidence():
    tm = {"tool": "traffic_manager", "args": {}, "output": {"endpoints": [
        {"name": "endpoint-a", "enabled": "Disabled"}, {"name": "endpoint-b", "enabled": "Enabled", "health": "Degraded"}]}}
    enable_b = _diag("tm_set_endpoint", {"region": "B", "enabled": "true"})
    assert "Enabled" in validator.redundant_fix(enable_b, [tm], {})
    tm_off = {**tm, "output": {"endpoints": [{"name": "endpoint-b", "enabled": "Disabled"}]}}
    assert validator.redundant_fix(enable_b, [tm_off], {}) is None
    assert validator.redundant_fix(enable_b, [], {}) is None  # no evidence: the fix stays for the human to judge

    start_b = _diag("start_webapp", {"region": "B"})
    running = {"tool": "web_app", "args": {"region": "B"}, "output": {"state": "Running"}}
    stopped = {"tool": "web_app", "args": {"region": "B"}, "output": {"state": "Stopped"}}
    assert "Running" in validator.redundant_fix(start_b, [running], {"B": {"ok": False}})
    assert validator.redundant_fix(start_b, [stopped], {"B": {"ok": True}}) is None  # the tool beats the probe
    assert "health probe" in validator.redundant_fix(start_b, [], {"B": {"ok": True, "status_code": 200}})
    assert validator.redundant_fix(start_b, [], {"B": {"ok": False}}) is None
    assert validator.redundant_fix(_diag("none", {}), [running, tm], {"B": {"ok": True}}) is None


def test_validator_withdraws_a_no_op_fix_but_keeps_the_diagnosis(monkeypatch):
    from dr_agent.llm import AgentResult
    from dr_agent.models import PlanStep
    step = PlanStep(id="v-1", phase="failover", title="t", owner_team="app", type="automated", action="wait_healthy",
                    params=[], depends_on=[], success_criteria="", abort_on_failure=False)
    running = {"tool": "web_app", "args": {"region": "B"}, "output": {"state": "Running"}}
    monkeypatch.setattr(validator, "run_agent", lambda *a, **k: AgentResult(
        _diag("start_webapp", {"region": "B"}), "llm", 1, None, "foundry-agent", evidence=[running]))
    d = validator.run(step, "HTTP 503", [], {"B": {"ok": True}}).output
    assert (d.fix_action, d.fix_params, d.recommended_action, d.likely_cause) == ("none", [], "retry", "c")
    assert "not offered" in d.evidence[-1]
    stopped = {**running, "output": {"state": "Stopped"}}
    monkeypatch.setattr(validator, "run_agent", lambda *a, **k: AgentResult(
        _diag("start_webapp", {"region": "B"}), "llm", 1, None, "foundry-agent", evidence=[stopped]))
    assert validator.run(step, "HTTP 403", [], {"B": {"ok": False}}).output.fix_action == "start_webapp"
