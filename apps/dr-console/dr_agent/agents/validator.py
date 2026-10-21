"""Validator agent: diagnoses a failed exercise step and recommends retry / rollback / continue."""
from __future__ import annotations

from typing import Optional

from dr_agent import trace
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


def _enabled(v: object) -> bool:
    return v is True or str(v).strip().lower() in ("enabled", "true")


def redundant_fix(diag: Diagnosis, evidence: list[dict], probes: dict) -> Optional[str]:
    """Why the proposed fix would change nothing, judged from live evidence; None if it may be needed (or the
    evidence does not say). The read-only tool results come first; the engine's own probe of Region B is used for
    start_webapp when the agent did not read the web app's state."""
    params = {p.key.strip().lower(): p.value.strip().lower() for p in diag.fix_params}
    if diag.fix_action == "start_webapp" and params.get("region") == "b":
        states = [e.get("output", {}).get("state") for e in evidence
                  if e.get("tool") == "web_app" and str((e.get("args") or {}).get("region", "")).upper() == "B"
                  and isinstance(e.get("output"), dict)]
        states = [x for x in states if x]
        if states:
            return f"web_app(B) reports state {states[-1]}" if states[-1] == "Running" else None
        probe_b = (probes or {}).get("B") or {}
        if probe_b.get("ok") is True:
            return f"Region B answers its health probe (HTTP {probe_b.get('status_code', 200)}), so its web app is running"
    if diag.fix_action == "tm_set_endpoint" and params.get("region") == "b" and params.get("enabled") == "true":
        for e in reversed(evidence):
            out = e.get("output")
            if e.get("tool") != "traffic_manager" or not isinstance(out, dict):
                continue
            ep = next((x for x in out.get("endpoints") or [] if x.get("name") == "endpoint-b"), None)
            if ep is not None:
                return f"traffic_manager reports endpoint-b {ep.get('enabled')}" if _enabled(ep.get("enabled")) else None
    return None


def run(step: PlanStep, error: str, recent_events: list[dict], probes: dict,
        deadline_s: float | None = None) -> AgentResult[Diagnosis]:
    payload = {"failed_step": step.model_dump(), "error": error,
               "recent_events": [{k: e.get(k) for k in ("ts", "kind", "step_id", "message")} for e in recent_events],
               "probes": probes}
    res = run_agent("validator", prompt("validator"), payload, Diagnosis, lambda: fallback(step, error), require_tool=True,
                    deadline_s=deadline_s)
    # Engine guard: a remediation the live evidence shows to be a no-op (start an app that is Running, enable an
    # endpoint that is Enabled) would cost a human approval and hide the real cause, so it is not offered. The
    # diagnosis and recommended action stay the agent's; only the fix is withdrawn, with the reason in the evidence.
    why = redundant_fix(res.output, res.evidence, probes)
    if why:
        trace.emit("check", f"Proposed fix {res.output.fix_action} withdrawn: it would change nothing", why, status="warn")
        res.output = res.output.model_copy(update={
            "fix_action": "none", "fix_params": [],
            "evidence": [*res.output.evidence, f"Engine: proposed fix {res.output.fix_action} not offered: {why}."]})
    return res
