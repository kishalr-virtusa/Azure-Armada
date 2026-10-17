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
