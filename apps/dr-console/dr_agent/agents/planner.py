"""Exercise Planner agent: produces the ordered DR exercise plan (requirements R4).

The model's plan must pass `validate_plan`; otherwise the problems are fed back once for a retry,
and if it still fails the canonical default plan is used.
"""
from __future__ import annotations

from dr_agent.agents import prompt
from dr_agent.engine.plan import allowlist_for_prompt, default_plan, sequence_for_prompt, validate_plan, PlanError
from dr_agent.llm import AgentResult, run_agent
from dr_agent.models import AppProfile, ExercisePlan, ReadinessReport, StrategyRec


def run(profile: AppProfile, strategy: StrategyRec | None, readiness: ReadinessReport | None,
        inventory_summary: dict) -> AgentResult[ExercisePlan]:
    base = {
        "profile": profile.model_dump(),
        "strategy": strategy.recommended if strategy else "warm-standby",
        "readiness_risks": readiness.risks if readiness else [],
        "inventory": inventory_summary,
        "allowlist": allowlist_for_prompt(),
        "mandatory_sequence": sequence_for_prompt(),
        "example_plan": default_plan().model_dump(),
    }
    first = run_agent("planner", prompt("planner"), base, ExercisePlan, default_plan, validate=validate_plan)
    if first.source == "llm" or not (first.error and "PlanError" in first.error):
        return first
    # one corrective retry with the validator's problems
    retry = dict(base, previous_attempt_problems=first.error)
    second = run_agent("planner-retry", prompt("planner"), retry, ExercisePlan, default_plan, validate=validate_plan)
    second.latency_ms += first.latency_ms
    return second


__all__ = ["run", "PlanError"]
