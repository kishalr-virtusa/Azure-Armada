"""Black box agent: its read-only record tools, strict drill id checks, the fallback answer and the ask API."""
import json

import pytest

from dr_agent.agents import blackbox, tools
from dr_agent.models import BlackboxAnswer
from dr_agent.tools import store

DRILL = {"PartitionKey": "ex-20261019-105207", "status": "completed", "t0": "2026-10-19T10:52:07+00:00",
         "rto_s": "41.3712", "rpo_s": 0.5, "failback_s": "88.1", "load_writes_per_s": 20, "chaos_injected": "",
         "rpo_evidence_json": json.dumps({"checked_writes": 900, "lost_writes": 2,
                                          "lost_write_times": ["2026-10-19T10:52:30+00:00"]}),
         "approvals_json": json.dumps({"plan": {"by": "kishal", "at": "2026-10-19T10:52:00+00:00"}}),
         "confirmations_json": json.dumps({"s7": {"by": "arasan", "at": "2026-10-19T10:55:00+00:00"}}),
         "plan_json": json.dumps({"steps": [{"id": "s7", "title": "Fail back to East Asia", "phase": "failback"}]}),
         "rto_breakdown_json": json.dumps({"dns": 30.123, "sql": 11.25})}


def test_blackbox_tools_only_for_blackbox():
    names = {d["name"] for d in tools.definitions("blackbox")}
    assert names == set(tools.BLACKBOX_TOOLS)
    assert "probe" not in names                                  # no live Azure probing from the black box
    for agent in ("validator", "readiness", "architect", "planner", "reporter", "runbook"):
        assert not names & {d["name"] for d in tools.definitions(agent)}
    assert "not available" in json.loads(tools.call("validator", "get_drill", '{"drill_id": "ex-20261019-105207"}'))["error"]
    assert "not available" in json.loads(tools.call("blackbox", "probe", '{"target": "A"}'))["error"]
    for d in tools.definitions("blackbox"):
        p = d["parameters"]
        assert d["strict"] and p["additionalProperties"] is False and sorted(p["required"]) == sorted(p["properties"])


@pytest.mark.parametrize("bad", ["ex-1", "ex-20261019-10520", "EX-20261019-105207", "ex-20261019-105207 ",
                                 "ex-20261019-105207' or 1 eq 1", "autopilot", "settings", ""])
def test_drill_id_is_validated_before_any_storage_read(monkeypatch, bad):
    def boom(*a, **k):
        raise AssertionError("storage must not be read for an invalid id")
    monkeypatch.setattr(store, "get_exercise", boom)
    monkeypatch.setattr(store, "get_events", boom)
    assert "ex-YYYYMMDD-HHMMSS" in json.loads(tools.call("blackbox", "get_drill", json.dumps({"drill_id": bad})))["error"]
    out = json.loads(tools.call("blackbox", "get_drill_timeline", json.dumps({"drill_id": bad, "kind": "all"})))
    assert "ex-YYYYMMDD-HHMMSS" in out["error"]


def test_get_drill_and_timeline_from_records(monkeypatch):
    monkeypatch.setattr(store, "get_exercise", lambda xid: DRILL if xid == DRILL["PartitionKey"] else None)
    monkeypatch.setattr(store, "get_events", lambda xid, after=0: [
        {"ts": f"t{i}", "kind": "approval" if i % 2 else "info", "step_id": "s7", "message": "m" * 500} for i in range(100)])
    d = json.loads(tools.call("blackbox", "get_drill", '{"drill_id": "ex-20261019-105207"}'))
    assert d["rto_s"] == 41.37 and d["rto_breakdown_s"] == {"dns": 30.12, "sql": 11.25}  # 2 decimals, as recorded
    assert d["rpo_evidence"]["lost_writes"] == 2 and d["link"] == "/results/ex-20261019-105207"
    assert d["step_confirmations"] == [{"step_id": "s7", "step": "Fail back to East Asia", "by": "arasan",
                                        "at": "2026-10-19T10:55:00+00:00"}]
    assert "no drill" in json.loads(tools.call("blackbox", "get_drill", '{"drill_id": "ex-20200101-000000"}'))["error"]
    raw = tools.call("blackbox", "get_drill_timeline", '{"drill_id": "ex-20261019-105207", "kind": "approval"}')
    t = json.loads(raw)                                          # small and still valid JSON
    assert len(raw) <= 4000 and t["events"] and all(e["kind"] == "approval" for e in t["events"])
    assert t["events"][-1]["ts"] == "t99" and len(t["events"][0]["message"]) == 160  # newest kept, messages cut
    assert "kind must be" in json.loads(tools.call("blackbox", "get_drill_timeline",
                                                   '{"drill_id": "ex-20261019-105207", "kind": "secrets"}'))["error"]


def test_fallback_answer_shape():
    empty = blackbox.fallback("anything", [])
    assert empty.found_in_records is False and empty.confidence == "low" and empty.citations == []
    d = {"id": "ex-20261019-105207", "status": "completed", "started": "2026-10-19T10:52:07+00:00", "rto_s": 41.37,
         "rpo_s": 0.5, "failback_s": 88.1, "chaos": False, "lost_writes": 2}
    a = blackbox.fallback("Summarise the latest drill", [d])
    BlackboxAnswer.model_validate(a.model_dump())
    assert a.confidence == "low" and a.found_in_records and "unavailable" in a.answer
    assert "41.37s" in a.answer and "0.50s" in a.answer and a.citations[0].link == "/results/ex-20261019-105207"


def test_run_uses_fallback_without_ai(monkeypatch):
    import dr_agent.foundry as foundry
    import dr_agent.llm as llm
    monkeypatch.setattr(foundry, "get_agents", lambda: None)
    monkeypatch.setattr(llm, "get_llm", lambda: None)
    monkeypatch.setattr(blackbox, "_list_drills", lambda limit: {"drills": []})
    res = blackbox.run("Summarise the latest drill", [{"question": "q", "answer": "a"}] * 20)
    assert res.source == "fallback" and isinstance(res.output, BlackboxAnswer)
    assert len(blackbox._history([{"question": "q", "answer": "a"}] * 20)) == blackbox.MAX_HISTORY


def test_answer_schema_is_strict():
    from dr_agent.foundry import AGENTS, strict_schema
    assert AGENTS["blackbox"][0] == "BlackboxAnswer"
    s = strict_schema(BlackboxAnswer)
    assert sorted(s["required"]) == sorted(s["properties"]) and s["additionalProperties"] is False


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from web import auth, main
    main.app.dependency_overrides[auth.require_auth] = lambda: None
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_ask_api_validates_question(client, monkeypatch):
    called = []
    monkeypatch.setattr(blackbox, "run", lambda q, h: called.append(q))
    for q in ("", "   ", "x" * 501):
        r = client.post("/api/blackbox/ask", json={"question": q, "history": []})
        assert r.status_code == 422 and "1-500" in r.json()["error"]
    assert client.post("/api/blackbox/ask", json={"history": []}).status_code == 422
    assert called == []                                          # the agent never runs for a rejected question


def test_ask_api_returns_answer(client, monkeypatch):
    from dr_agent.llm import AgentResult
    seen = {}

    def fake(q, h):
        seen.update(q=q, h=h)
        return AgentResult(blackbox.fallback(q, []), "fallback", 3, None, "fallback")
    monkeypatch.setattr(blackbox, "run", fake)
    r = client.post("/api/blackbox/ask", json={"question": "  Who approved?  ", "history": "not a list"})
    assert r.status_code == 200 and seen == {"q": "Who approved?", "h": []}
    body = r.json()
    assert body["found_in_records"] is False and body["source"] == "fallback" and "citations" in body


def test_ask_api_requires_auth():
    from fastapi.testclient import TestClient
    from web import main
    assert TestClient(main.app).post("/api/blackbox/ask", json={"question": "hi"}).status_code == 401
