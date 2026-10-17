from datetime import datetime, timedelta

from dr_agent.engine.measurements import (
    HeartbeatRecord, compute_rpo, compute_rto, evaluate, fmt_duration, last_acked_before,
)

T0 = datetime(2026, 10, 18, 10, 0, 0)


def hb(i: int, acked: bool = True) -> HeartbeatRecord:
    """Heartbeat i written at T0 + 2*i seconds."""
    return HeartbeatRecord(seq=1000 + i, client_ts=T0 + timedelta(seconds=2 * i), acked=acked)


def test_rto_basic_and_missing():
    assert compute_rto(T0, T0 + timedelta(minutes=4, seconds=12)) == 252
    assert compute_rto(None, T0) is None
    assert compute_rto(T0, None) is None
    assert compute_rto(T0 + timedelta(seconds=5), T0) == 0  # clock skew never negative


def test_last_acked_before_ignores_failed_and_later_writes():
    recs = [hb(0), hb(1), hb(2, acked=False), hb(3)]
    assert last_acked_before(recs, T0 + timedelta(seconds=5)).seq == 1001
    assert last_acked_before(recs, T0 - timedelta(seconds=1)) is None


def test_rpo_no_loss():
    recs = [hb(i) for i in range(10)]
    t_fo = T0 + timedelta(seconds=19)
    r = compute_rpo(recs, t_fo, newest_present_ts=hb(9).client_ts, present_seqs={x.seq for x in recs})
    assert r.rpo_s == 0 and r.lost_seqs == []


def test_rpo_with_lost_tail():
    recs = [hb(i) for i in range(10)]  # last acked at +18s
    t_fo = T0 + timedelta(seconds=19)
    present = {x.seq for x in recs[:7]}  # +0..+12 replicated, +14..+18 lost
    r = compute_rpo(recs, t_fo, newest_present_ts=hb(6).client_ts, present_seqs=present)
    assert r.rpo_s == 6
    assert r.lost_seqs == [1007, 1008, 1009]


def test_rpo_ignores_writes_after_failover():
    # writes after t_fo land on the new primary and must not mask earlier loss
    recs = [hb(i) for i in range(5)]
    t_fo = T0 + timedelta(seconds=9)
    r = compute_rpo(recs, t_fo, newest_present_ts=hb(2).client_ts)
    assert r.rpo_s == 4


def test_rpo_unmeasurable():
    assert compute_rpo([], T0, T0).rpo_s is None
    assert compute_rpo([hb(0)], T0 + timedelta(seconds=1), None).rpo_s is None


def test_evaluate_and_format():
    e = evaluate(252, 6, rto_target_min=30, rpo_target_min=15)
    assert e["rto_met"] and e["rpo_met"]
    assert not evaluate(None, 6, 30, 15)["rto_met"]
    assert not evaluate(2000, 6, 30, 15)["rto_met"]
    assert fmt_duration(252) == "4m 12s"
    assert fmt_duration(6) == "6s"
    assert fmt_duration(3720) == "1h 02m"
    assert fmt_duration(None) == "n/a"
