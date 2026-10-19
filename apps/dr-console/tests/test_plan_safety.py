"""Plan safety rules: region-changing actions only where the safe sequence puts them, human gates, abort rules."""
import pytest

from dr_agent.engine.plan import PlanError, default_plan, validate_plan
from dr_agent.models import Param


def _without(plan, step_id):
    plan.steps = [s for s in plan.steps if s.id != step_id]
    for s in plan.steps:
        s.depends_on = [d for d in s.depends_on if d != step_id]
    return plan


def _problems(plan):
    with pytest.raises(PlanError) as e:
        validate_plan(plan)
    return " | ".join(e.value.problems)


def test_default_plan_is_safe():
    validate_plan(default_plan())


def test_extra_outage_in_dr_region_rejected():
    p = default_plan()
    i = next(i for i, s in enumerate(p.steps) if s.action == "inject_outage")
    extra = p.steps[i].model_copy(update={"id": "x-out-b", "params": [Param(key="region", value="B")], "depends_on": []})
    p.steps.insert(i + 1, extra)
    assert "not part of the safe sequence" in _problems(p)


def test_second_forced_failover_rejected():
    p = default_plan()
    i = next(i for i, s in enumerate(p.steps) if s.action == "sql_failover")
    p.steps.insert(i + 1, p.steps[i].model_copy(update={"id": "x-fo2", "depends_on": []}))
    assert "allowed once" in _problems(p)


def test_disabling_dr_endpoint_rejected():
    p = default_plan()
    i = next(i for i, s in enumerate(p.steps) if s.action == "tm_set_endpoint")
    bad = p.steps[i].model_copy(update={"id": "x-tm-b", "depends_on": [],
                                        "params": [Param(key="region", value="B"), Param(key="enabled", value="false")]})
    p.steps.insert(i + 1, bad)
    assert "not part of the safe sequence" in _problems(p)


def test_go_approval_required_before_outage():
    p = default_plan()
    human_before = [s.id for s in p.steps[:next(i for i, s in enumerate(p.steps) if s.action == "inject_outage")] if s.type == "human"]
    for sid in human_before:
        _without(p, sid)
    assert "before the outage" in _problems(p)


def test_failback_approval_required():
    p = default_plan()
    rpo = next(i for i, s in enumerate(p.steps) if s.action == "measure_rpo")
    fb = next(i for i, s in enumerate(p.steps) if s.action == "start_webapp")
    for sid in [s.id for s in p.steps[rpo:fb] if s.type == "human"]:
        _without(p, sid)
    assert "starting failback" in _problems(p)


def test_mandatory_failover_step_must_abort_on_failure():
    p = default_plan()
    next(s for s in p.steps if s.action == "sql_failover").abort_on_failure = False
    assert "abort_on_failure" in _problems(p)


def test_bad_parameter_values_rejected():
    p = default_plan()
    w = next(s for s in p.steps if s.action == "wait_healthy")
    w.params = [Param(key=x.key, value="abc" if x.key == "timeout_s" else x.value) for x in w.params]
    assert "timeout_s" in _problems(p)
