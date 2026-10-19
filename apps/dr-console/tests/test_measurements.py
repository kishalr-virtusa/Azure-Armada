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


def test_rpo_no_loss_counts_writes_during_failover():
    recs = [hb(i) for i in range(10)]                       # +0 .. +18 s
    t_fo, t_end = T0 + timedelta(seconds=10), T0 + timedelta(seconds=19)
    r = compute_rpo(recs, t_end, {x.seq for x in recs}, t_fo)
    assert r.rpo_s == 0 and r.lost_seqs == [] and r.checked == 10 and r.acked_during_failover == 5


def test_rpo_counts_writes_lost_during_the_failover():
    # the old primary acknowledged +12..+18 while the forced failover ran; they never reached the new primary
    recs = [hb(i) for i in range(10)]
    t_fo, t_end = T0 + timedelta(seconds=11), T0 + timedelta(seconds=19)
    present = {x.seq for x in recs[:6]}                        # +0..+10 survived
    r = compute_rpo(recs, t_end, present, t_fo)
    assert r.lost_seqs == [1006, 1007, 1008, 1009] and r.rpo_s == 8   # +18 lost, +10 last survivor


def test_rpo_ignores_unacknowledged_and_later_writes():
    recs = [hb(0), hb(1), hb(2, acked=False), hb(3)]
    r = compute_rpo(recs, T0 + timedelta(seconds=5), {1000, 1001})  # +6 is after the window
    assert r.rpo_s == 0 and r.checked == 2


def test_rpo_unmeasurable():
    assert compute_rpo([], T0, set()).rpo_s is None
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


def test_rto_breakdown_accounts_for_every_second():
    from datetime import datetime, timedelta, timezone
    from dr_agent.engine.measurements import rto_breakdown, GAP_LABEL
    t0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)
    at = lambda s: (t0 + timedelta(seconds=s)).isoformat()
    ev = [
        {"kind": "step_start", "step_id": "out-1", "ts": at(-0.4), "data": {"action": "inject_outage"}},
        {"kind": "step_end", "step_id": "out-1", "ts": at(1.0)},
        {"kind": "step_start", "step_id": "fo-1", "ts": at(1.5), "data": {"action": "sql_failover"}},
        {"kind": "step_end", "step_id": "fo-1", "ts": at(14.0)},
        {"kind": "step_start", "step_id": "fo-2", "ts": at(14.2), "data": {"action": "tm_set_endpoint"}},
        {"kind": "step_end", "step_id": "fo-2", "ts": at(18.0)},
        {"kind": "step_start", "step_id": "val-1", "ts": at(18.3), "data": {"action": "wait_healthy"}},
        {"kind": "step_end", "step_id": "val-1", "ts": at(33.0)},   # ends after recovery is proven
    ]
    t_rec = t0 + timedelta(seconds=32.4)
    segs = rto_breakdown(t0, t_rec, ev)
    assert abs(sum(s["seconds"] for s in segs) - 32.4) < 1e-6           # every second accounted
    assert segs[0]["label"].startswith("Outage injected") and segs[0]["seconds"] == 1.0   # clipped at t0
    assert [s["label"] for s in segs].count(GAP_LABEL) == 3            # gaps 1.0-1.5, 14.0-14.2, 18.0-18.3
    assert segs[-1]["label"].startswith("DNS") and abs(segs[-1]["seconds"] - 14.1) < 1e-6
    for a, b in zip(segs, segs[1:]):
        assert a["end"] == b["start"]                                 # contiguous, no overlaps


def test_write_series_buckets_per_second():
    from dr_agent.engine.measurements import write_series
    recs = [HeartbeatRecord(1, T0, True), HeartbeatRecord(2, T0 + timedelta(milliseconds=500), False),
            HeartbeatRecord(3, T0 + timedelta(seconds=2), True)]
    s = write_series(recs)
    assert [(x["ok"], x["fail"]) for x in s] == [(1, 1), (0, 0), (1, 0)]
