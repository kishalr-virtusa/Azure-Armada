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
from datetime import datetime
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
    t_failover: datetime,
    newest_present_ts: Optional[datetime],
    present_seqs: Optional[set[int]] = None,
) -> RpoResult:
    """Measured data loss window in seconds.

    records           heartbeat writes recorded by the writer (acked or not)
    t_failover        moment the DB failover was triggered
    newest_present_ts MAX(client_ts) in the NEW primary WHERE client_ts <= t_failover
    present_seqs      optional: seqs found in the new primary (client_ts <= t_failover), to list lost writes
    """
    records = list(records)
    last = last_acked_before(records, t_failover)
    if last is None:
        return RpoResult(rpo_s=None, note="no acknowledged heartbeat before failover; cannot measure RPO")
    if newest_present_ts is None:
        return RpoResult(rpo_s=None, last_acked_seq=last.seq, last_acked_ts=last.client_ts,
                         note="no heartbeat rows found in the new primary; data missing or table not replicated")
    rpo = max(0.0, (last.client_ts - newest_present_ts).total_seconds())
    lost: list[int] = []
    if present_seqs is not None:
        lost = sorted(r.seq for r in records
                      if r.acked and r.client_ts <= t_failover and r.seq not in present_seqs)
    note = "no acknowledged writes lost" if rpo == 0 and not lost else f"{len(lost)} acknowledged write(s) lost"
    return RpoResult(rpo_s=rpo, last_acked_seq=last.seq, last_acked_ts=last.client_ts,
                     newest_present_ts=newest_present_ts, lost_seqs=lost, note=note)


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
