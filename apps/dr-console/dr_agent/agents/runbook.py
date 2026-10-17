"""Runbook Reviewer agent: checks the DR runbook against the live inventory (requirements R3.4)."""
from __future__ import annotations

import re

from dr_agent.agents import prompt
from dr_agent.llm import AgentResult, run_agent
from dr_agent.models import RunbookReview, RunbookStepReview

STEP_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*\S)\s*$")
NAME_RE = re.compile(r"`([a-z][a-z0-9-]{3,})`|\b((?:sql|tm|fd|app|kv|dra|rg)-[a-z0-9-]{2,})\b", re.I)
SERVICE_WORDS = {"front door": "Front Door", "application gateway": "Application Gateway", "site recovery": "Site Recovery"}


def parse_steps(md: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2)) for line in md.splitlines() if (m := STEP_RE.match(line))]


def fallback(runbook_md: str, inventory_names: set[str], inventory_services: set[str]) -> RunbookReview:
    names = {n.lower() for n in inventory_names}
    services = {s.lower() for s in inventory_services}
    reviews: list[RunbookStepReview] = []
    for no, text in parse_steps(runbook_md):
        refs = {(a or b).lower() for a, b in NAME_RE.findall(text)}
        unknown = sorted(r for r in refs if r not in names)
        absent = [label for word, label in SERVICE_WORDS.items() if word in text.lower() and word not in services]
        if unknown or absent:
            why = "; ".join(filter(None, [f"unknown resource(s): {', '.join(unknown)}" if unknown else "",
                                          f"service not used in this environment: {', '.join(absent)}" if absent else ""]))
            reviews.append(RunbookStepReview(step_no=no, text=text, verdict="stale", reason=why,
                                             suggested="Update this step to reference the live resources (see inventory)."))
        else:
            reviews.append(RunbookStepReview(step_no=no, text=text, verdict="valid", reason="matches inventory", suggested=""))
    if not any("fail back" in t.lower() or "failback" in t.lower() for _, t in parse_steps(runbook_md)):
        reviews.append(RunbookStepReview(step_no=0, text="(missing) Failback to the primary region", verdict="missing",
                                         reason="Runbook has no failback procedure.",
                                         suggested="Add: start Region A app, planned SQL failover to Region A, re-enable Traffic Manager endpoint A, verify."))
    return RunbookReview(steps=reviews, updated_runbook_md=runbook_md)


def run(runbook_md: str, inventory: dict) -> AgentResult[RunbookReview]:
    """inventory: {"resources": [{"name","type","location"}...], "services": [...], ...}"""
    resources = inventory.get("resources", [])
    names = {r.get("name", "") for r in resources}
    services = set(inventory.get("services", [])) | {r.get("type", "").split("/")[-1] for r in resources}
    payload = {"runbook_markdown": runbook_md, "inventory": inventory}
    return run_agent("runbook", prompt("runbook"), payload, RunbookReview, lambda: fallback(runbook_md, names, services))
