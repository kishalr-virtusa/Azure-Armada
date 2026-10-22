"""RTO / RPO calculations. Pure functions: no Azure or network calls, fully unit-tested.

Definitions (requirements R6):
  RTO = t_recovered - t0
        t0          = moment the outage was injected
        t_recovered = first successful probe through Traffic Manager served by the DR region with db_ok
  RPO = (last heartbeat ACKNOWLEDGED before the DB failover was triggered)
        - (newest heartbeat PRESENT in the new primary with client_ts <= t_failover)
        clamped at 0. Only rows written before t_failover are compared; rows written to the new primary
        after failover must not hide data loss.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional


@dataclass(frozen=True)
class HeartbeatRecord:
    seq: int
    client_ts: datetime
    acked: bool


@dataclass
class RpoResult:
    rpo_s: Optional[float]
    last_acked_seq: Optional[int] = None
    last_acked_ts: Optional[datetime] = None
    newest_present_ts: Optional[datetime] = None
    lost_seqs: list[int] = field(default_factory=list)
    note: str = ""
    checked: int = 0                 # acknowledged writes checked against the new primary
    acked_during_failover: int = 0   # of which acknowledged while the forced failover was running


def compute_rto(t0: Optional[datetime], t_recovered: Optional[datetime]) -> Optional[float]:
    """Seconds from outage injection to verified recovery; None if either timestamp is missing."""
    if t0 is None or t_recovered is None:
        return None
    return max(0.0, (t_recovered - t0).total_seconds())


def last_acked_before(records: Iterable[HeartbeatRecord], t: datetime) -> Optional[HeartbeatRecord]:
    best: Optional[HeartbeatRecord] = None
    for r in records:
        if r.acked and r.client_ts <= t and (best is None or r.client_ts > best.client_ts):
            best = r
    return best


def compute_rpo(
    records: Iterable[HeartbeatRecord],
    window_end: datetime,
    present_seqs: Optional[set[int]],
    t_failover: Optional[datetime] = None,
) -> RpoResult:
    """Measured data loss, write by write.

    Every heartbeat the database ACKNOWLEDGED up to `window_end` (the moment the forced failover completed, so
    writes acknowledged while it was running are included) must exist in the new primary. RPO is 0 only if none
    is missing; otherwise it is the time from the last write that survived to the last write that was lost.

    records       heartbeat writes recorded by the writer (acked or not)
    window_end    end of the forced failover (writes after it go to the new primary by definition)
    present_seqs  seqs found in the new primary
    t_failover    start of the forced failover (only used to count writes acknowledged during it)
    """
    acked = sorted((r for r in records if r.acked and r.client_ts <= window_end), key=lambda r: r.client_ts)
    if not acked:
        return RpoResult(rpo_s=None, note="no acknowledged heartbeat before the failover completed; cannot measure RPO")
    if present_seqs is None:
        return RpoResult(rpo_s=None, last_acked_seq=acked[-1].seq, last_acked_ts=acked[-1].client_ts,
                         note="could not read heartbeats from the new primary")
    during = sum(1 for r in acked if t_failover is not None and r.client_ts >= t_failover)
    lost = [r for r in acked if r.seq not in present_seqs]
    survived = [r for r in acked if r.seq in present_seqs]
    newest_present = survived[-1].client_ts if survived else None
    if not lost:
        rpo, note = 0.0, f"all {len(acked)} acknowledged writes found in the new primary ({during} acknowledged during the failover)"
    else:
        anchor = max((r.client_ts for r in survived if r.client_ts < lost[0].client_ts), default=lost[0].client_ts)
        rpo = max(0.0, (lost[-1].client_ts - anchor).total_seconds())
        note = f"{len(lost)} of {len(acked)} acknowledged write(s) missing in the new primary"
    return RpoResult(rpo_s=rpo, last_acked_seq=acked[-1].seq, last_acked_ts=acked[-1].client_ts,
                     newest_present_ts=newest_present, lost_seqs=[r.seq for r in lost], note=note,
                     checked=len(acked), acked_during_failover=during)


def write_series(records: Iterable[HeartbeatRecord], until: Optional[datetime] = None) -> list[dict]:
    """Per-second counts of confirmed and failed writes (for the live chart and the report)."""
    buckets: dict[int, list[int]] = {}
    for r in records:
        t = r.client_ts if r.client_ts.tzinfo else r.client_ts.replace(tzinfo=timezone.utc)
        if until and t > (until if until.tzinfo else until.replace(tzinfo=timezone.utc)):
            continue
        b = buckets.setdefault(int(t.timestamp()), [0, 0])
        b[0 if r.acked else 1] += 1
    if not buckets:
        return []
    lo, hi = min(buckets), max(buckets)
    return [{"t": s, "ok": buckets.get(s, [0, 0])[0], "fail": buckets.get(s, [0, 0])[1]} for s in range(lo, hi + 1)]


def evaluate(rto_s: Optional[float], rpo_s: Optional[float], rto_target_min: int, rpo_target_min: int) -> dict:
    """Compare measured values against targets. A missing measurement counts as NOT met."""
    rto_target_s, rpo_target_s = rto_target_min * 60, rpo_target_min * 60
    return {
        "rto_s": rto_s, "rpo_s": rpo_s,
        "rto_target_s": rto_target_s, "rpo_target_s": rpo_target_s,
        "rto_met": rto_s is not None and rto_s <= rto_target_s,
        "rpo_met": rpo_s is not None and rpo_s <= rpo_target_s,
    }


def fmt_duration(seconds: Optional[float]) -> str:
    """'4m 12s', '6s', '1h 02m' or 'n/a'."""
    if seconds is None:
        return "n/a"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


# ---------------------------------------------------------------- RTO accounting
ACTION_LABELS = {
    "inject_outage": "Outage injected: East Asia app stopped",
    "sql_failover": "SQL forced failover to Southeast Asia",
    "tm_set_endpoint": "Traffic Manager stops routing to East Asia",
    "wait_healthy": "DNS and health checks until users reach Southeast Asia",
    "assert": "Checks",
}
GAP_LABEL = "Engine between steps (bookkeeping, status writes)"
# Inside a gap (a step failed, the drill paused, then the step was retried) these events mark who held the clock:
# the part of the gap that ends at the marker gets the marker's label.
GAP_MARKERS = {
    "diagnosis": "Validator agent diagnoses the failure",
    "approval": "Waiting for a human to approve the fix",
    "remediation": "Approved fix applied",
}


def rto_breakdown(t0: Optional[datetime], t_recovered: Optional[datetime], events: list[dict]) -> list[dict]:
    """Account for every second of the RTO window [t0, t_recovered] using the engine's step timestamps.

    events: exercise events with kind step_start/step_end, ts (ISO, same clock as t0), step_id and data.action.
    Returns ordered segments {label, start, end, seconds}; consecutive, non-overlapping, and their seconds sum to
    exactly t_recovered - t0 (gaps between steps are listed as their own segment, never hidden).
    """
    if not t0 or not t_recovered or t_recovered <= t0:
        return []

    def ts(e):
        d = datetime.fromisoformat(str(e["ts"]).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    t0 = t0 if t0.tzinfo else t0.replace(tzinfo=timezone.utc)
    t_recovered = t_recovered if t_recovered.tzinfo else t_recovered.replace(tzinfo=timezone.utc)
    starts: dict[str, tuple[datetime, str]] = {}
    spans: list[tuple[datetime, datetime, str]] = []
    for e in events:
        sid = e.get("step_id")
        if not sid:
            continue
        if e.get("kind") == "step_start":
            action = ((e.get("data") or {}).get("action")) or ""
            starts[sid] = (ts(e), ACTION_LABELS.get(action, e.get("message") or action or sid))
        elif e.get("kind") == "step_end" and sid in starts:
            s, label = starts.pop(sid)
            spans.append((s, ts(e), label))
    # clip to the window, order by start, drop empties
    clipped = sorted(((max(s, t0), min(en, t_recovered), lb) for s, en, lb in spans if en > t0 and s < t_recovered),
                     key=lambda x: x[0])
    markers = sorted((ts(e), GAP_MARKERS[e["kind"]]) for e in events
                     if e.get("kind") in GAP_MARKERS and e.get("ts") and t0 < ts(e) < t_recovered)
    out: list[dict] = []

    def gap(start: datetime, end: datetime) -> None:
        """A gap between steps, split where a diagnosis, a human approval or an applied fix ended a part of it."""
        for t, label in markers:
            if start < t < end:
                out.append({"label": label, "start": start, "end": t})
                start = t
        out.append({"label": GAP_LABEL, "start": start, "end": end})

    cursor = t0
    for s, en, lb in clipped:
        s = max(s, cursor)
        if en <= s:
            continue
        if s > cursor:
            gap(cursor, s)
        out.append({"label": lb, "start": s, "end": en})
        cursor = en
    if cursor < t_recovered:  # the measuring step ends after recovery is proven; anything left is still a gap
        gap(cursor, t_recovered)
    out = [seg for seg in out if seg["end"] > seg["start"]]
    for seg in out:
        seg["seconds"] = round((seg["end"] - seg["start"]).total_seconds(), 3)
        seg["start"], seg["end"] = seg["start"].isoformat(), seg["end"].isoformat()
    return out
