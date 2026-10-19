import json
import threading

import pytest
from pydantic import BaseModel

from dr_agent.llm import run_agent


class Out(BaseModel):
    answer: str


class FakeLLM:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def structured(self, system_prompt, payload, schema):
        if self.exc:
            raise self.exc
        return self.result


def test_llm_path():
    r = run_agent("t", "sys", {"q": 1}, Out, lambda: Out(answer="fb"), llm=FakeLLM(Out(answer="llm")))
    assert r.source == "llm" and r.output.answer == "llm" and r.error is None


def test_fallback_on_error():
    r = run_agent("t", "sys", {}, Out, lambda: Out(answer="fb"), llm=FakeLLM(exc=TimeoutError("slow")))
    assert r.source == "fallback" and r.output.answer == "fb" and "TimeoutError" in r.error


def test_fallback_on_validation_reject():
    def reject(o):
        raise ValueError("bad plan")
    r = run_agent("t", "sys", {}, Out, lambda: Out(answer="fb"), llm=FakeLLM(Out(answer="x")), validate=reject)
    assert r.source == "fallback" and "bad plan" in r.error


def test_fallback_when_not_configured(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    r = run_agent("t", "sys", {}, Out, lambda: Out(answer="fb"))
    assert r.source == "fallback" and r.error == "LLM not configured"


def test_foundry_agent_preferred_then_direct_then_fallback(monkeypatch):
    from pydantic import BaseModel
    from dr_agent import foundry, llm as llm_mod

    class Out(BaseModel):
        x: int

    class FakeAgents:
        def __init__(self, ok): self.ok = ok
        def invoke(self, name, instr, payload, schema, require_tool=False, deadline_s=None, meta=None):
            if not self.ok:
                raise RuntimeError("agent down")
            return schema(x=1)

    class FakeLLM:
        def structured(self, *a):
            return Out(x=2)

    monkeypatch.setattr(foundry, "get_agents", lambda: FakeAgents(True))
    monkeypatch.setattr(llm_mod, "get_llm", lambda: FakeLLM())
    r = llm_mod.run_agent("planner-retry", "sys", {}, Out, lambda: Out(x=3))
    assert (r.output.x, r.source, r.via) == (1, "llm", "foundry-agent")

    monkeypatch.setattr(foundry, "get_agents", lambda: FakeAgents(False))
    r = llm_mod.run_agent("planner", "sys", {}, Out, lambda: Out(x=3))
    assert (r.output.x, r.via) == (2, "azure-openai") and "agent down" in r.error

    monkeypatch.setattr(llm_mod, "get_llm", lambda: None)
    r = llm_mod.run_agent("planner", "sys", {}, Out, lambda: Out(x=3))
    assert (r.output.x, r.source, r.via) == (3, "fallback", "fallback")


def test_agent_id_and_strict_schema(monkeypatch):
    from dr_agent.foundry import agent_id, strict_schema
    monkeypatch.delenv("DRA_AGENT_PREFIX", raising=False)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "app-dra-console")
    from dr_agent.models import ExercisePlan
    assert agent_id("planner-retry") == "dra-planner"
    sch = strict_schema(ExercisePlan)
    assert sch["additionalProperties"] is False


# ---------------------------------------------------------------- Day-0 agent contract

def test_new_result_fields_default_and_old_positional_callers_still_work():
    from dr_agent.llm import AgentResult
    a, b = AgentResult(Out(answer="x"), "llm", 1, None, "foundry-agent"), AgentResult(Out(answer="y"), "fallback", 2)
    assert (a.agent_version, a.model, a.evidence, a.blocked, a.grounding) == ("", "", [], False, {})
    assert a.usage == {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0, "rounds": 0}
    a.usage["rounds"] += 1
    a.evidence.append({})
    assert b.usage["rounds"] == 0 and b.evidence == []  # mutable defaults are not shared


def test_direct_path_fills_model_and_rounds_and_accepts_deadline():
    llm = FakeLLM(Out(answer="llm"))
    llm.deployment = "gpt-4.1-mini"
    from dr_agent import trace
    r = trace.run("t0", run_agent, "t", "sys", {}, Out, lambda: Out(answer="fb"), llm=llm, deadline_s=25)
    assert (r.via, r.model, r.agent_version, r.usage["rounds"]) == ("azure-openai", "gpt-4.1-mini", "", 1)
    data = next(e for e in trace.get("t0")["events"] if e["kind"] == "agent")["data"]
    assert set(data) == {"agent", "version", "model", "usage", "response_id", "rounds", "via"}
    assert (data["model"], data["rounds"], data["via"]) == ("gpt-4.1-mini", 1, "azure-openai")


def test_validate_arity_is_inspected():
    from functools import partial
    from dr_agent.llm import _takes_ctx
    seen = []
    run_agent("t", "sys", {"q": 1}, Out, lambda: Out(answer="fb"), llm=FakeLLM(Out(answer="x")),
              validate=lambda out: seen.append(("one", out.answer)))
    run_agent("t", "sys", {"q": 1}, Out, lambda: Out(answer="fb"), llm=FakeLLM(Out(answer="x")),
              validate=lambda out, ctx: seen.append(("two", ctx)))
    assert seen == [("one", "x"), ("two", {"payload": {"q": 1}, "evidence": []})]
    assert _takes_ctx(lambda *a: None) and not _takes_ctx(lambda out, *, strict=True: None)
    assert not _takes_ctx(partial(lambda rules, out: None, {}))


class SeqLLM:
    """Returns the queued outputs in order and records each payload it was sent."""

    def __init__(self, *outs):
        self.outs, self.payloads = list(outs), []

    def structured(self, system_prompt, payload, schema):
        self.payloads.append(payload)
        return self.outs.pop(0)


def test_repairable_error_gets_one_repair_round():
    from dr_agent.llm import RepairableError

    def validate(out):
        if out.answer == "bad":
            raise RepairableError(["answer is bad"])
    llm = SeqLLM(Out(answer="bad"), Out(answer="good"))
    r = run_agent("t", "sys", {"q": 1}, Out, lambda: Out(answer="fb"), llm=llm, validate=validate)
    assert (r.source, r.output.answer, r.usage["rounds"]) == ("llm", "good", 2)
    assert llm.payloads == [{"q": 1}, {"q": 1, "previous_attempt_problems": ["answer is bad"]}]


def test_second_repairable_failure_falls_back_without_another_round():
    from dr_agent.llm import RepairableError

    def validate(out):
        raise RepairableError(["still wrong"])
    llm = SeqLLM(Out(answer="a"), Out(answer="b"), Out(answer="c"))
    r = run_agent("t", "sys", "plain text", Out, lambda: Out(answer="fb"), llm=llm, validate=validate)
    assert (r.source, r.output.answer) == ("fallback", "fb") and "still wrong" in r.error
    assert llm.payloads == ["plain text", {"request": "plain text", "previous_attempt_problems": ["still wrong"]}]


def test_repair_round_is_spent_once_per_call_across_paths(monkeypatch):
    from dr_agent import foundry, llm as llm_mod
    from dr_agent.llm import RepairableError
    calls = []

    class Agents:
        def invoke(self, name, instr, payload, schema, require_tool=False, deadline_s=None, meta=None):
            calls.append(("foundry", payload))
            return schema(answer="bad")

    def validate(out):
        raise RepairableError(["nope"])
    direct = SeqLLM(Out(answer="bad"))
    monkeypatch.setattr(foundry, "get_agents", lambda: Agents())
    monkeypatch.setattr(llm_mod, "get_llm", lambda: direct)
    r = llm_mod.run_agent("t", "sys", {}, Out, lambda: Out(answer="fb"), validate=validate)
    assert r.source == "fallback" and len(calls) == 2 and direct.payloads == [{}]


@pytest.mark.parametrize("site, explicit, expected", [("app-dra-console", None, "dra-"), (None, None, "dra-dev-"),
                                                      (None, "dra-eval-", "dra-eval-"), ("x", "dra-eval-", "dra-eval-")])
def test_agent_prefix(monkeypatch, site, explicit, expected):
    from dr_agent.foundry import agent_id, agent_prefix
    for var, val in (("WEBSITE_SITE_NAME", site), ("DRA_AGENT_PREFIX", explicit)):
        if val is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, val)
    assert agent_prefix() == expected and agent_id("planner-retry") == expected + "planner"


def test_prod_prefix_is_never_registered_off_app_service(monkeypatch):
    from dr_agent.foundry import may_register
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("DRA_REGISTER_AGENTS", raising=False)
    assert not may_register("dra-") and may_register("dra-dev-")
    monkeypatch.setenv("DRA_REGISTER_AGENTS", "false")
    assert not may_register("dra-dev-")
    monkeypatch.setenv("WEBSITE_SITE_NAME", "app-dra-console")
    monkeypatch.delenv("DRA_REGISTER_AGENTS")
    assert may_register("dra-")


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def _foundry_client(monkeypatch, responses, registered_hash=None):
    """A FoundryAgents with no Azure: requests are recorded and answered from `responses` (POST /responses)."""
    from dr_agent import foundry
    c = object.__new__(foundry.FoundryAgents)
    c.endpoint, c.model, c.reasoning_model = "https://p", "gpt-4.1-mini", ""
    c._ready, c._versions, c._lock, c.requests = {}, {}, threading.Lock(), []

    def req(method, path, body=None, params=None):
        c.requests.append((method, path, body))
        if method == "GET":
            return FakeResp(200, {"versions": {"latest": {"version": "7", "metadata": {"spec_hash": registered_hash}}}})
        if path.endswith("/versions"):
            return FakeResp(200, {"version": "8"})
        return FakeResp(200, responses.pop(0))
    c._req = req
    return c


def _answer(text, rid, usage):
    return {"id": rid, "model": "gpt-4.1-mini-2025", "status": "completed", "usage": usage,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def test_foundry_tool_loop_collects_evidence_usage_and_trace(monkeypatch):
    from dr_agent import llm as llm_mod, trace, foundry
    from dr_agent.agents import tools as agent_tools
    monkeypatch.setenv("WEBSITE_SITE_NAME", "app-dra-console")
    monkeypatch.delenv("DRA_AGENT_PREFIX", raising=False)
    monkeypatch.setattr(agent_tools, "call", lambda agent, name, args: json.dumps({"ok": True, "region": json.loads(args)["region"]}))
    u = {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 40},
         "output_tokens_details": {"reasoning_tokens": 3}}
    tool_round = {"id": "r1", "usage": u, "output": [
        {"type": "function_call", "call_id": "c1", "name": "web_app", "arguments": '{"region": "A"}'},
        {"type": "function_call", "call_id": "c2", "name": "web_app", "arguments": '{"region": "B"}'}]}
    client = _foundry_client(monkeypatch, [tool_round, _answer('{"answer": "ok"}', "r2", u)])
    monkeypatch.setattr(foundry, "get_agents", lambda: client)
    seen = {}

    def validate(out, ctx):
        seen.update(ctx)
    r = trace.run("t1", llm_mod.run_agent, "validator", "sys", {"q": 1}, Out, lambda: Out(answer="fb"),
                  validate=validate, deadline_s=25)
    assert (r.via, r.agent_version, r.model) == ("foundry-agent", "8", "gpt-4.1-mini-2025")
    assert r.usage == {"input_tokens": 200, "output_tokens": 20, "cached_tokens": 80, "reasoning_tokens": 6, "rounds": 2}
    assert r.evidence == [{"tool": "web_app", "args": {"region": "A"}, "output": {"ok": True, "region": "A"}},
                          {"tool": "web_app", "args": {"region": "B"}, "output": {"ok": True, "region": "B"}}]
    assert seen == {"payload": {"q": 1}, "evidence": r.evidence}
    assert all(path.split("/")[2].startswith("dra-validator") for _, path, _ in client.requests if path.startswith("/agents/"))
    agent_event = next(e for e in trace.get("t1")["events"] if e["title"].startswith("Azure AI Foundry agent"))
    assert {"agent", "version", "model", "usage", "response_id", "rounds", "via"} <= set(agent_event["data"])
    assert (agent_event["data"]["agent"], agent_event["data"]["response_id"], agent_event["data"]["rounds"],
            agent_event["data"]["via"]) == ("dra-validator", "r2", 2, "foundry-agent")


def test_dev_prefix_never_posts_to_prod_agents(monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("DRA_AGENT_PREFIX", raising=False)
    client = _foundry_client(monkeypatch, [_answer('{"answer": "ok"}', "r1", {})])
    client.invoke("reporter", "sys", {}, Out)
    agent_paths = [path for _, path, _ in client.requests if path.startswith("/agents/")]
    assert agent_paths and all(p.startswith("/agents/dra-dev-reporter") for p in agent_paths)


def test_registration_off_uses_latest_version_without_writing(monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "app-dra-console")
    monkeypatch.delenv("DRA_AGENT_PREFIX", raising=False)
    monkeypatch.setenv("DRA_REGISTER_AGENTS", "false")
    client = _foundry_client(monkeypatch, [_answer('{"answer": "ok"}', "r1", {})], registered_hash="old")
    meta = {}
    assert client.invoke("reporter", "sys", {}, Out, meta=meta).answer == "ok"
    assert not any(path.endswith("/versions") for _, path, _ in client.requests)
    assert (meta["version"], meta["rounds"], meta["evidence"]) == ("7", 1, [])
