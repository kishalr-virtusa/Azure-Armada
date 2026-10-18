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
    b = {"SQL_SERVER": "fog", "REGION_NAME": "Southeast Asia"}
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


def test_architect_rejects_option_that_misses_targets_and_fixes_numbers():
    from dr_agent.agents import architect
    from dr_agent.models import AppProfile, StrategyOption, StrategyRec
    prof = AppProfile(rto_min=30, rpo_min=15)
    wrong = StrategyRec(recommended="pilot-light", rationale="cheapest", design_mapping=[], options=[
        StrategyOption(name=n, expected_rto_min=1, expected_rpo_min=1, relative_cost=1.0, meets_targets=True, note="llm")
        for n, *_ in architect.STRATEGY_GUIDE])
    right = wrong.model_copy(update={"recommended": "warm-standby"})

    class FakeLLM:
        def __init__(self, out): self.out = out
        def structured(self, *a): return self.out.model_copy(deep=True)

    r = architect.run_agent  # noqa: F841 - keep import path
    from dr_agent import llm as llm_mod
    res = llm_mod.run_agent("architect", "p", {}, StrategyRec, lambda: architect.fallback(prof), llm=FakeLLM(wrong),
                            validate=None)
    assert res.output.recommended == "pilot-light"  # sanity: without validation the wrong answer would pass

    import pytest  # noqa: F401
    # through architect.run the wrong pick is rejected -> fallback picks warm-standby
    import dr_agent.agents.architect as A
    orig = A.run_agent
    A.run_agent = lambda name, sp, payload, schema, fb, validate=None: llm_mod.run_agent(
        name, sp, payload, schema, fb, llm=FakeLLM(wrong), validate=validate)
    try:
        out = A.run(prof, {}).output
        assert out.recommended == "warm-standby"
        A.run_agent = lambda name, sp, payload, schema, fb, validate=None: llm_mod.run_agent(
            name, sp, payload, schema, fb, llm=FakeLLM(right), validate=validate)
        out = A.run(prof, {}).output
        pl = next(o for o in out.options if o.name == "pilot-light")
        assert out.recommended == "warm-standby" and pl.expected_rto_min == 120 and pl.meets_targets is False
        assert pl.note == "llm"
    finally:
        A.run_agent = orig


def test_architect_costs_in_dollars_and_budget_never_overrides_targets():
    prof = AppProfile(rto_min=30, rpo_min=15, monthly_budget_usd=25)
    opts = {o.name: o for o in architect.options_for(prof, 19.5)}
    assert opts["warm-standby"].est_monthly_usd == 31.2 and not opts["warm-standby"].within_budget
    assert opts["backup-restore"].within_budget and not opts["backup-restore"].meets_targets  # $21.45, 8h RTO
    # cheap-but-too-slow is never recommended because of budget
    assert architect.fallback(prof, 19.5).recommended == "warm-standby"


def test_pricing_falls_back_when_api_unreachable(monkeypatch):
    from dr_agent.tools import pricing
    monkeypatch.setattr(pricing, "API", "http://127.0.0.1:9/none")
    pricing._cache.clear()
    p = pricing.region_monthly_usd(timeout_s=0.5)
    assert p["source"] == "fallback" and p["total_usd"] == 19.5
    pricing._cache.clear()
