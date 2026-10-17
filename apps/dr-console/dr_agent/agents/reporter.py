"""Reporter agent: narrative (summary, issues, remediation) for the exercise report."""
from __future__ import annotations

from dr_agent.agents import prompt
from dr_agent.llm import AgentResult, run_agent
from dr_agent.models import RemediationItem, ReportNarrative


def fallback(facts: dict) -> ReportNarrative:
    r = facts.get("results", {})
    rto_ok, rpo_ok = r.get("rto_met"), r.get("rpo_met")
    verdict = "PASSED" if rto_ok and rpo_ok else "FAILED"
    summary = (f"A DR exercise was executed for {facts.get('profile', {}).get('name', 'the application')}: "
               f"outage injected in Central India, failover to Southeast Asia, validation and failback. "
               f"Measured RTO {r.get('rto')} (target {r.get('rto_target')}) and RPO {r.get('rpo')} "
               f"(target {r.get('rpo_target')}). Overall result: {verdict}.")
    remediation = [RemediationItem(item=x, owner="app", priority="medium") for x in facts.get("open_findings", [])[:6]]
    issues = [e for e in facts.get("errors", [])][:6]
    good = [x for x, ok in (("RTO target met", rto_ok), ("RPO target met", rpo_ok),
                            ("Failback to primary completed", bool(r.get("failback")))) if ok]
    return ReportNarrative(executive_summary=summary, what_went_well=good, issues=issues, remediation=remediation)


def run(facts: dict) -> AgentResult[ReportNarrative]:
    return run_agent("reporter", prompt("reporter"), facts, ReportNarrative, lambda: fallback(facts))
