"""Memory across drills: how this drill compares with earlier ones, and which findings keep coming back.

Computed by code from stored drill records, so the Reporter agent gets facts (not guesses) and the Results page and
Overview can chart the trend.
"""
from __future__ import annotations

import json
import re
from statistics import median
from typing import Any, Optional

from dr_agent.tools import store

_WORD = re.compile(r"[a-z0-9-]{4,}")
STOP = {"update", "runbook", "step", "steps", "with", "from", "that", "this", "should", "region", "references",
        "instead", "which", "does", "exist", "live", "environment", "uses", "using"}


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in STOP}


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.4


def drill_history(current_xid: str, limit: int = 12) -> dict:
    """Earlier completed drills (newest first) and how the current one compares."""
    ids = sorted((e["PartitionKey"] for e in store.list_exercises(1000)
                  if e.get("PartitionKey", "").startswith("ex-") and e["PartitionKey"] < current_xid), reverse=True)
    past = []
    for xid in ids[:limit * 2]:
        m = store.get_exercise(xid) or {}
        if m.get("status") != "completed" or _num(m.get("rto_s")) is None:
            continue
        past.append({"id": xid, "date": (m.get("t0") or "")[:16], "rto_s": _num(m.get("rto_s")), "rpo_s": _num(m.get("rpo_s")),
                     "failback_s": _num(m.get("failback_s")), "chaos": bool(m.get("chaos_injected")),
                     "segments": json.loads(m.get("rto_breakdown_json") or "{}"),
                     "remediation": [r.get("item", "") for r in json.loads(m.get("remediation_json") or "[]")]})
        if len(past) >= limit:
            break
    cur = store.get_exercise(current_xid) or {}
    rto = _num(cur.get("rto_s"))
    plain = [p["rto_s"] for p in past if not p["chaos"]]
    med = round(median(plain), 3) if plain else None
    out: dict[str, Any] = {
        "previous_drills": len(past), "median_rto_s": med,
        "rto_vs_median_pct": round(100 * (rto - med) / med, 1) if rto is not None and med else None,
        "trend": [{"id": p["id"], "rto_s": p["rto_s"], "chaos": p["chaos"]} for p in reversed(past)],
    }
    # which RTO segment moved most against the previous drill that has a breakdown
    seg_now = json.loads(cur.get("rto_breakdown_json") or "{}")
    prev = next((p for p in past if p["segments"] and not p["chaos"]), None)
    if seg_now and prev:
        deltas = {k: round(v - prev["segments"].get(k, 0.0), 3) for k, v in seg_now.items()}
        k = max(deltas, key=lambda x: abs(deltas[x]))
        out["biggest_segment_change"] = {"segment": k, "delta_s": deltas[k], "vs_drill": prev["id"]}
    out["recent_remediation"] = [{"drill": p["id"], "items": p["remediation"][:6]} for p in past[:5]]
    return out


def recurring(items: list[str], history: dict) -> list[dict]:
    """For each finding, in how many earlier drills a similar remediation item was already reported."""
    out = []
    for it in items:
        n = sum(1 for d in history.get("recent_remediation", []) if any(_similar(it, x) for x in d["items"]))
        if n:
            out.append({"item": it, "seen_in_previous_drills": n})
    return out
