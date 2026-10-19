"""Black box agent: answers questions about the recorded DR history, only from the records, with citations."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from dr_agent.agents import prompt
from dr_agent.agents.tools import _list_drills
from dr_agent.llm import AgentResult, run_agent
from dr_agent.models import BlackboxAnswer, Citation

log = logging.getLogger(__name__)
MAX_HISTORY = 6
UNAVAILABLE = "The AI service was unavailable, so this is only the latest recorded drill, not an answer to your question."


def _secs(v) -> str:
    return "not recorded" if v is None else f"{v:.2f}s"


def fallback(question: str, drills: list[dict]) -> BlackboxAnswer:
    if not drills:
        return BlackboxAnswer(answer=f"No drills are in the records yet. {UNAVAILABLE}", citations=[],
                              confidence="low", found_in_records=False)
    d = drills[0]
    facts = (f"The latest drill {d['id']} ({d.get('status') or 'unknown status'}) started {d.get('started') or 'at an unrecorded time'} UTC "
             f"and measured RTO {_secs(d.get('rto_s'))}, RPO {_secs(d.get('rpo_s'))} and failback {_secs(d.get('failback_s'))}"
             + (f", with {d['lost_writes']} lost write(s)" if d.get("lost_writes") is not None else "")
             + (" under a surprise fault" if d.get("chaos") else "") + ".")
    return BlackboxAnswer(answer=f"{facts} {UNAVAILABLE}",
                          citations=[Citation(source=f"drill {d['id']}", detail=facts, link=f"/results/{d['id']}")],
                          confidence="low", found_in_records=True)


def _history(history: list | None) -> list[dict]:
    """Last few Q/A pairs, trimmed: they only resolve follow-ups ("and the one before?"), never serve as facts."""
    out = []
    for h in (history or [])[-MAX_HISTORY:]:
        if isinstance(h, dict) and h.get("question"):
            out.append({"question": str(h["question"])[:500], "answer": str(h.get("answer") or "")[:800]})
    return out


def run(question: str, history: list | None = None) -> AgentResult[BlackboxAnswer]:
    try:
        drills = _list_drills(5)["drills"]
    except Exception as e:  # noqa: BLE001 - the agent can still try its tools; the fallback says nothing is recorded
        log.warning("black box could not list drills: %s", e)
        drills = []
    payload = {"question": question, "history": _history(history),
               "today": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               # same records as list_drills: lets the direct-model path (which has no tools) stay grounded too
               "recent_drills": drills}
    return run_agent("blackbox", prompt("blackbox"), payload, BlackboxAnswer, lambda: fallback(question, drills),
                     require_tool=True)
