"""A drill may only start when the newest readiness result is a recent GO."""
import json
from datetime import datetime, timedelta, timezone

from dr_agent import autopilot, service
from dr_agent.models import ReadinessReport


def _setup(monkeypatch, manual=None, auto=None):
    """manual / auto: (minutes ago, go) or None."""
    now = datetime.now(timezone.utc)
    p1 = {}
    if manual:
        p1 = {"readiness_json": ReadinessReport(checks=[], go=manual[1], summary="ok" if manual[1] else "SQL lag").model_dump_json(),
              "readiness_meta_json": json.dumps({"at": (now - timedelta(minutes=manual[0])).isoformat()})}
    last = {"at": (now - timedelta(minutes=auto[0])).isoformat(), "go": auto[1],
            "failing": {} if auto[1] else {"Traffic Manager endpoints": "B disabled"}} if auto else {}
    monkeypatch.setattr(service, "_phase1", lambda: p1)
    monkeypatch.setattr(autopilot, "get_state", lambda: {"last": last})


def test_no_readiness_result_blocks(monkeypatch):
    _setup(monkeypatch)
    g = service.readiness_gate()
    assert not g["ok"] and "run the readiness checks" in g["reason"]


def test_recent_go_allows(monkeypatch):
    _setup(monkeypatch, auto=(5, True))
    assert service.readiness_gate()["ok"]


def test_newest_result_wins(monkeypatch):
    _setup(monkeypatch, manual=(30, True), auto=(2, False))  # Autopilot found a problem after the manual GO
    g = service.readiness_gate()
    assert not g["ok"] and g["source"] == "Autopilot" and "Traffic Manager endpoints" in g["reason"]
    _setup(monkeypatch, manual=(1, True), auto=(8, False))  # a fresh manual GO after the fix
    assert service.readiness_gate()["ok"]


def test_stale_go_blocks(monkeypatch):
    _setup(monkeypatch, manual=(service.READINESS_MAX_AGE_MIN + 5, True))
    g = service.readiness_gate()
    assert not g["ok"] and "min old" in g["reason"]


def test_approve_refuses_on_no_go_before_writing_anything(monkeypatch):
    _setup(monkeypatch, auto=(3, False))
    monkeypatch.setattr(service.runner, "any_running", lambda: False)
    monkeypatch.setattr(service, "get_draft_plan", lambda: None)
    writes = []
    monkeypatch.setattr(service.store, "upsert_exercise", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(service, "validate_plan", lambda plan: None)
    monkeypatch.setattr(service.ExercisePlan, "model_validate", classmethod(lambda cls, d: object()))
    r = service.approve_and_start("Kishal R")
    assert "NO-GO" in r["error"] and writes == []
