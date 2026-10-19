"""Live agent activity trace."""
from pydantic import BaseModel

from dr_agent import trace
from dr_agent.llm import run_agent


class Out(BaseModel):
    answer: str


class FakeLLM:
    def structured(self, system_prompt, payload, schema):
        return schema(answer="llm")


def test_events_recorded_only_inside_a_traced_run():
    assert trace.emit("tool", "outside") is None
    trace.run("run-0001", lambda: trace.step("tool", "read", lambda: {"n": 3}, summary=lambda r: f"{r['n']} items"))
    t = trace.get("run-0001")
    assert t["done"] and t["events"][0]["status"] == "ok" and t["events"][0]["detail"] == "3 items"


def test_agent_run_shows_call_check_and_fallback():
    def reject(_):
        raise ValueError("does not meet targets")
    trace.run("run-0002", run_agent, "t", "sys", {}, Out, lambda: Out(answer="fb"), llm=FakeLLM(), validate=reject)
    kinds = [(e["kind"], e["status"]) for e in trace.get("run-0002")["events"]]
    assert ("agent", "ok") in kinds and ("check", "fail") in kinds and ("fallback", "warn") in kinds


def test_unknown_run_is_empty():
    assert trace.get("nope-0000") == {"events": [], "done": False, "known": False}


def test_snapshot_drops_biggest_details_to_fit_storage():
    import json
    def run():
        trace.emit("input", "small", data={"a": 1})
        trace.emit("check", "huge", data={"blob": "x" * 5000})
        trace.emit("check", "huger", data={"blob": "y" * 5900})
    trace.run("run-0003", run)
    snap = json.loads(trace.snapshot("run-0003", limit=7000))
    by = {e["title"]: e for e in snap["events"]}
    assert by["huger"]["data"] is None and by["small"]["data"] == {"a": 1} and len(snap["events"]) == 3
    assert trace.get("run-0003")["events"][2]["data"] is not None   # the live trace itself is untouched


def test_agent_event_shows_cost_and_snapshot_totals():
    import json
    usage = {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 400, "reasoning_tokens": 0, "rounds": 2}

    def run():
        idx = trace.emit("agent", "Azure AI Foundry agent dra-blackbox", status="running")
        trace.update(idx, "ok", "Answered in 1.0s", data={"agent": "dra-blackbox", "model": "gpt-4.1-mini",
                                                         "usage": usage, "via": "foundry-agent"}, ms=1000)
        idx = trace.emit("agent", "Azure OpenAI deployment gpt-4.1-mini (direct)", status="running")
        trace.update(idx, "ok", data={"model": "gpt-4.1-mini", "usage": {**usage, "input_tokens": 0, "output_tokens": 0}})
    trace.run("run-0004", run)
    ev = trace.get("run-0004")["events"]
    cost = (600 * 0.40 + 400 * 0.10 + 100 * 1.60) / 1e6
    assert ev[0]["detail"] == f"Answered in 1.0s · ${cost:.4f}" and ev[0]["tokens"] == 1100
    assert "cost_usd" not in ev[0]["data"]  # the agent's data is recorded exactly as given
    assert "tokens" not in ev[1] and "$" not in ev[1]["detail"]  # nothing to bill
    snap = json.loads(trace.snapshot("run-0004"))
    assert snap["tokens"] == 1100 and abs(snap["cost_usd"] - cost) < 1e-9
