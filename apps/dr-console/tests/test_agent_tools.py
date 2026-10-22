"""Agents' read-only tools: per-agent allowlist, strict schemas, errors returned to the agent instead of raised."""
import json

from dr_agent.agents import tools


def test_definitions_are_strict_and_per_agent():
    v = {d["name"] for d in tools.definitions("validator")}
    assert "probe" in v and "site_recovery" not in v
    assert all(d["strict"] and d["parameters"]["additionalProperties"] is False for d in tools.definitions("readiness"))
    assert tools.definitions("architect") == []          # agents without tools stay single-shot


def test_tool_outside_the_agents_set_is_refused():
    out = json.loads(tools.call("validator", "site_recovery", "{}"))
    assert "not available" in out["error"]
    out = json.loads(tools.call("architect", "probe", '{"target": "A"}'))
    assert "not available" in out["error"]


def test_tool_failures_come_back_as_data(monkeypatch):
    def boom(target):
        raise RuntimeError("network down")
    monkeypatch.setitem(tools.TOOLS, "probe", (tools.TOOLS["probe"][0], tools.TOOLS["probe"][1], boom))
    assert "network down" in json.loads(tools.call("validator", "probe", '{"target": "B"}'))["error"]
