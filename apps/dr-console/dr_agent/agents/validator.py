"""Validator agent: diagnoses a failed exercise step and recommends retry / rollback / continue."""
from __future__ import annotations

from dr_agent.agents import prompt
from dr_agent.llm import AgentResult, run_agent
from dr_agent.models import Diagnosis, Param, PlanStep

TRANSIENT = ("timeout", "timed out", "temporar", "dns", "connection", "503", "502", "not healthy")


def fallback(step: PlanStep, error: str) -> Diagnosis:
    low = error.lower()
    if "endpoint" in low and "region b" in low and "disabled" in low:
        return Diagnosis(likely_cause="The Region B Traffic Manager endpoint is Disabled, so users cannot be routed "
                                      "to the DR region.",
                         evidence=[error[:200]], recommended_action="retry", fix_action="tm_set_endpoint",
                         fix_params=[Param(key="region", value="B"), Param(key="enabled", value="true")])
    if "region b" in low and "stopped" in low:
        return Diagnosis(likely_cause="The Region B web app is stopped.", evidence=[error[:200]],
                         recommended_action="retry", fix_action="start_webapp", fix_params=[Param(key="region", value="B")])
    transient = any(t in error.lower() for t in TRANSIENT)
    action = "retry" if transient else "rollback"
    if step.action == "sql_failover" and not transient:
        action = "rollback"
    return Diagnosis(likely_cause=f"Step '{step.title}' failed: {error[:200]}",
                     evidence=[f"action={step.action}", f"error={error[:200]}"],
                     recommended_action=action, fix_action="none", fix_params=[])


def run(step: PlanStep, error: str, recent_events: list[dict], probes: dict) -> AgentResult[Diagnosis]:
    payload = {"failed_step": step.model_dump(), "error": error,
               "recent_events": [{k: e.get(k) for k in ("ts", "kind", "step_id", "message")} for e in recent_events],
               "probes": probes}
    return run_agent("validator", prompt("validator"), payload, Diagnosis, lambda: fallback(step, error), require_tool=True)
