import pytest

from dr_agent.agents import architect, readiness, runbook
from dr_agent.engine.plan import PlanError, default_plan, validate_plan
from dr_agent.models import AppProfile, ExercisePlan


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)


def test_architect_fallback_picks_cheapest_fitting():
    rec = architect.run(AppProfile(tier=1, rto_min=30, rpo_min=15), {}).output
    assert rec.recommended == "warm-standby"
    assert len(rec.options) == 5
    assert architect.run(AppProfile(rto_min=600, rpo_min=1440), {}).output.recommended == "backup-restore"
    assert architect.run(AppProfile(rto_min=2, rpo_min=0), {}).output.recommended == "active-active"


def test_parity_detects_missing_setting():
    a = {"SQL_SERVER": "fog", "PAYMENT_GATEWAY_URL": "https://pg", "REGION_NAME": "Central India"}
    b = {"SQL_SERVER": "fog", "REGION_NAME": "South India"}
    c = readiness.parity_check(a, b)
    assert c.status == "fail" and c.evidence["missing_in_region_b"] == ["PAYMENT_GATEWAY_URL"]
    assert readiness.parity_check(a, dict(a, REGION_NAME="x")).status == "pass"


def test_readiness_fail_forces_no_go():
    checks = readiness.build_checks({
        "fog_status": {"primary_region": "A", "servers": {"A": {"state": "CATCH_UP"}, "B": {"state": "CATCH_UP"}}},
        "lag_s": 3, "settings_a": {"X": "1"}, "settings_b": {},
    })
    rep = readiness.run(checks).output
    assert rep.go is False and any("missing in Region B" in r for r in rep.risks)


def test_runbook_flags_stale_steps():
    md = "1. Check health of `dra-app-cin-x7k2`\n2. Switch the Front Door profile `fd-payments` to the DR origin\n3. Failover SQL server `sql-payments-old`\n"
    inv = {"resources": [{"name": "dra-app-cin-x7k2", "type": "Microsoft.Web/sites"}], "services": ["traffic manager"]}
    rev = runbook.run(md, inv).output
    verdicts = {s.step_no: s.verdict for s in rev.steps}
    assert verdicts[1] == "valid" and verdicts[2] == "stale" and verdicts[3] == "stale"
    assert any(s.verdict == "missing" for s in rev.steps)  # no failback


def test_default_plan_valid_and_validator_rejects_bad_plans():
    validate_plan(default_plan())
    p = default_plan()
    bad = ExercisePlan(steps=[s for s in p.steps if s.action != "measure_rpo"], assumptions=[])
    with pytest.raises(PlanError):
        validate_plan(bad)
    swapped = default_plan()
    swapped.steps[5].action = "delete_everything"
    with pytest.raises(PlanError, match="allowlist"):
        validate_plan(swapped)
    reordered = default_plan()
    reordered.steps[4], reordered.steps[5] = reordered.steps[5], reordered.steps[4]
    with pytest.raises(PlanError):
        validate_plan(reordered)


def test_planner_fallback_returns_default():
    from dr_agent.agents import planner
    res = planner.run(AppProfile(), None, None, {})
    assert res.source == "fallback" and len(res.output.steps) == len(default_plan().steps)
