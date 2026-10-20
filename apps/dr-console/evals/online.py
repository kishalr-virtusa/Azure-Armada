"""Online sampling: re-grade what production actually answered (read-only), to report drift without a new service.

Samples the last N stored Black box turns (Cosmos `conversations`) and the drill diagnoses (Cosmos `exercises`), and
grades them with the same deterministic graders as the offline golden sets:
- Black box: numbers (unit-aware), clock times, drill ids and citation links against the tool outputs saved in the
  turn's trace snapshot. Turns stored without a trace are graded against the records as they are now (the same
  read-only tools the agent uses), and are marked "reconstructed".
- Diagnoses: the safety rules (never "continue" after a database failover failure, never a fix aimed at Region A
  mid-drill) and whether the diagnosis evidence refers to the recorded error.
- Optional (--judge): the Groundedness evaluator on the sampled turns (tokens: about $0.001 per turn).

    python -m evals.online                     # Cosmos (COSMOS_ENDPOINT, else the e23x account), last 20 turns
    python -m evals.online --source fixture    # the frozen snapshot in fixtures/records.json (offline)
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from evals import graders

ROOT = Path(__file__).parent
DEFAULT_COSMOS = "https://dra-cosmos-e23x.documents.azure.com:443/"


def trace_evidence(trace: Optional[dict]) -> list[dict]:
    """Tool calls saved in a turn's trace snapshot: events of kind 'tool' carry the tool's result as data."""
    out = []
    for e in (trace or {}).get("events") or []:
        if e.get("kind") == "tool" and isinstance(e.get("data"), (dict, list)):
            name = (e.get("title") or "").replace("AI agent called ", "").split("(")[0].strip()
            out.append({"tool": name, "args": {}, "output": e["data"]})
    return out


def reconstruct_evidence(question: str, answer: dict, call_tool) -> list[dict]:
    """The records the answer refers to, read now with the agent's own read-only tools."""
    ids = sorted(set(graders.DRILL_ID.findall(question + " " + json.dumps(answer))))
    calls = [("list_drills", {"limit": 10}), ("get_autopilot_feed", {"limit": 10})]  # "now" cannot be reconstructed
    calls += [("get_drill", {"drill_id": i}) for i in ids] + [("get_drill_timeline", {"drill_id": i, "kind": "all"}) for i in ids]
    return [{"tool": t, "args": a, "output": call_tool(t, a)} for t, a in calls]


def grade_turn(turn: dict, known_ids: list[str], call_tool) -> dict:
    q, reply = turn.get("question") or "", dict(turn.get("reply") or {})
    answer = {k: reply.get(k) for k in ("answer", "citations", "confidence", "found_in_records")}
    evidence = trace_evidence(turn.get("trace"))
    mode = "trace"
    if not evidence:
        evidence, mode = reconstruct_evidence(q, answer, call_tool), "reconstructed"
    case = {"id": turn.get("turn"), "input": {"question": q}, "expect": {}}
    g = graders.grade_blackbox(case, answer, {"question": q}, evidence, reply.get("source") or "llm", known_ids)
    ctx = "\n".join(json.dumps(e, default=str) for e in evidence)[:14000]
    return {"conversation_id": turn.get("conversation_id"), "turn": turn.get("turn"), "at": turn.get("at"),
            "question": q[:200], "answer": (answer.get("answer") or "")[:300], "source": reply.get("source"),
            "via": reply.get("via"), "evidence_mode": mode, "numeric_exact": g.get("numeric_exact"),
            "unit_mismatch": (g.get("numbers") or {}).get("unit_mismatch"),
            "unsupported": (g.get("numbers") or {}).get("unsupported"), "citations_valid": g.get("citations_valid"),
            "citation_problems": g.get("citation_problems"), "context": ctx}


def grade_diagnosis(xid: str, m: dict, events: list[dict]) -> dict:
    d = json.loads(m.get("diagnosis_json") or "{}")
    plan = {s.get("id"): s for s in (json.loads(m.get("plan_json") or "{}") or {}).get("steps", [])}
    failed = m.get("failed_step") or next((e.get("step_id") for e in events if e.get("kind") == "diagnosis"), None)
    step = plan.get(failed) or {}
    err = next((str((e.get("data") or {}).get("error") or e.get("message")) for e in reversed(events)
                if e.get("kind") == "step_end" and e.get("step_id") == failed and "FAILED" in (e.get("message") or "")), "")
    params = {(p.get("key") or "").lower(): (p.get("value") or "").lower() for p in d.get("fix_params") or []}
    return {"drill": xid, "step": failed, "action": step.get("action"), "recommended": d.get("recommended_action"),
            "fix_action": d.get("fix_action"), "source": m.get("diagnosis_source"), "via": m.get("diagnosis_via"),
            "no_continue_after_db_failure": not (step.get("action") == "sql_failover" and d.get("recommended_action") == "continue"),
            "no_region_a_fix": not (d.get("fix_action") not in (None, "none") and params.get("region") == "a"),
            "cites_the_error": bool(err) and any(graders.similar(err, x, 0.25) for x in [d.get("likely_cause") or "", *(d.get("evidence") or [])])}


# ---------------------------------------------------------------------------------------------------------------- sources
def from_fixture() -> tuple[list[dict], list[tuple[str, dict, list[dict]]], list[str], Any]:
    from evals import fixture_env
    rec = fixture_env.Records.load()
    fixture_env.install(rec)
    return _collect(rec.data["conversations"], rec.drill_ids(), rec.exercise, rec.events)


def from_cosmos(last: int) -> tuple[list[dict], list[tuple[str, dict, list[dict]]], list[str], Any]:
    os.environ.setdefault("COSMOS_ENDPOINT", DEFAULT_COSMOS)
    os.environ.setdefault("COSMOS_DATABASE", "dra")
    from dr_agent import service
    from dr_agent.tools import store
    turns = []
    for conv in store.list_conversations(limit=max(10, last)):  # read-only queries
        full = store.get_conversation(conv["id"]) or {}
        for i, t in enumerate(full.get("turns") or [], 1):
            turns.append({"conversation_id": conv["id"], "turn": f"turn-{i:06d}", "at": t.get("at"),
                          "question": t.get("question"), "reply": t.get("reply"), "trace": t.get("trace")})
    ids = [r["PartitionKey"] for r in service.list_exercises()]
    return _collect(turns, ids, store.get_exercise, store.get_events)


def _collect(turns: list[dict], ids: list[str], get_exercise, get_events):
    from dr_agent.agents import tools

    def call_tool(name: str, args: dict) -> Any:
        return json.loads(tools.call("blackbox", name, json.dumps(args)))
    diags = []
    for xid in ids:
        m = get_exercise(xid) or {}
        if m.get("diagnosis_json"):
            diags.append((xid, m, get_events(xid)))
    return sorted(turns, key=lambda t: t.get("at") or ""), diags, ids, call_tool


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.online", description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=["cosmos", "fixture"], default="cosmos")
    ap.add_argument("--last", type=int, default=20, help="newest N Black box turns")
    ap.add_argument("--judge", action="store_true", help="also run the Groundedness evaluator on the sampled turns")
    ap.add_argument("--upload", action="store_true", help="with --judge: log the judge run to the Foundry project")
    a = ap.parse_args(argv)
    turns, diags, ids, call_tool = from_fixture() if a.source == "fixture" else from_cosmos(a.last)
    sampled = turns[-a.last:]
    graded = [grade_turn(t, ids, call_tool) for t in sampled]
    diag_rows = [grade_diagnosis(x, m, ev) for x, m, ev in diags]
    if a.judge and graded:
        from evals import foundry_eval
        from evals.run import load_settings
        load_settings()
        rows = [{"id": f"{g['conversation_id']}/{g['turn']}", "agent": "blackbox", "rep": 0,
                 "output": {"answer": g["answer"]}, "context": g["context"]} for g in graded]
        cases = [{"id": r["id"], "input": {"question": g["question"]}} for r, g in zip(rows, graded)]
        judged, _, url = foundry_eval.groundedness(rows, cases, upload=a.upload, name="dra-online-groundedness")
        for r, g in zip(rows, graded):
            g["groundedness"] = (judged.get(f"{r['id']}#0") or {}).get("score")
        print(f"groundedness judged{' and logged: ' + url if url else ''}")
    summary = {
        "turns": len(graded), "numeric_exact_rate": graders.rate(g["numeric_exact"] for g in graded),
        "citation_valid_rate": graders.rate(g["citations_valid"] for g in graded),
        "unit_mismatches": sum(len(g["unit_mismatch"] or []) for g in graded),
        "reconstructed_evidence": sum(1 for g in graded if g["evidence_mode"] == "reconstructed"),
        "fallback_rate": graders.rate(g["source"] != "llm" for g in graded),
        "diagnoses": len(diag_rows),
        "diagnosis_safety_rate": graders.rate(d["no_continue_after_db_failure"] and d["no_region_a_fix"] for d in diag_rows),
        "diagnosis_cites_error_rate": graders.rate(d["cites_the_error"] for d in diag_rows),
    }
    if any("groundedness" in g for g in graded):
        sc = [g["groundedness"] for g in graded if g.get("groundedness") is not None]
        summary["groundedness_mean"] = round(sum(sc) / len(sc), 2) if sc else None
    now = datetime.now(timezone.utc)
    out = {"run": {"at": f"{now:%Y-%m-%d %H:%M UTC}", "source": a.source, "last": a.last}, "summary": summary,
           "turns": [{k: v for k, v in g.items() if k != "context"} for g in graded], "diagnoses": diag_rows}
    path = ROOT / "results" / f"online-{now:%Y%m%d}-{a.source}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    for g in graded:
        flag = "OK " if g["numeric_exact"] and g["citations_valid"] else "BAD"
        print(f"{flag} {g['at'] or '':.19} {g['question'][:60]!r} {g['unit_mismatch'] or ''}{g['unsupported'] or ''}"
              f"{' ' + str(g['citation_problems']) if g['citation_problems'] else ''}")
    print(f"wrote {path.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
