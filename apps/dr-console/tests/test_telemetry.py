"""OpenTelemetry spans (GenAI semantic conventions) bridged from the live trace, cost math and exporter setup."""
import json
import sys
import threading

import pytest
from opentelemetry import trace as ot
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

from dr_agent import telemetry, trace
from dr_agent.llm import RepairableError, run_agent


class Out(BaseModel):
    answer: str


class StrategyRec(BaseModel):  # same name as the Architect's schema: the rules fallback is attributed by schema
    answer: str


class FakeLLM:
    deployment = "gpt-4.1-mini"

    def __init__(self, *results):
        self.results = list(results)

    def structured(self, system_prompt, payload, schema):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def spans(monkeypatch):
    """Route dr_agent.trace spans to an in-memory exporter; returns a function that lists finished spans."""
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("DRA_AGENT_PREFIX", raising=False)
    monkeypatch.delenv("DRA_MODEL_PRICES", raising=False)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.use_tracer_provider(provider)
    yield exporter.get_finished_spans
    trace._close_all("test ended")
    trace._st().flags.clear()
    telemetry.use_tracer_provider(None)


def _usage(inp, out, cached=0, reasoning=0):
    return {"input_tokens": inp, "output_tokens": out, "input_tokens_details": {"cached_tokens": cached},
            "output_tokens_details": {"reasoning_tokens": reasoning}}


def _foundry(monkeypatch, responses):
    """A FoundryAgents with no Azure: POST /responses is answered from `responses` (an Exception is raised)."""
    from dr_agent import foundry
    from dr_agent.agents import tools as agent_tools
    c = object.__new__(foundry.FoundryAgents)
    c.endpoint, c.model, c.reasoning_model = "https://p", "gpt-4.1-mini", ""
    c._ready, c._versions, c._hashes, c._resolved, c._pin, c._lock = {}, {}, {}, {}, True, threading.Lock()

    def req(method, path, body=None, params=None, deadline=None):
        if method == "GET":
            return FakeResp(200, {"versions": {"latest": {"version": "8", "metadata": {}}}})
        if path.endswith("/versions"):
            return FakeResp(200, {"version": "8"})
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResp(200, r)
    c._req = req
    monkeypatch.setattr(foundry, "get_agents", lambda: c)
    monkeypatch.setattr(agent_tools, "call", lambda agent, name, args: json.dumps({"ok": True}))
    return c


def _tool_round(rid, name, region):
    return {"id": rid, "usage": _usage(100, 10, cached=40), "output": [
        {"type": "function_call", "call_id": f"c-{rid}", "name": name, "arguments": json.dumps({"region": region})}]}


def _answer(rid, text='{"answer": "ok"}'):
    return {"id": rid, "model": "gpt-4.1-mini-2025-04-14", "status": "completed", "usage": _usage(100, 10, cached=40),
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def _by_name(spans, prefix):
    return [s for s in spans if s.name.startswith(prefix)]


def test_foundry_call_with_two_tool_rounds_is_one_invoke_agent_span_with_tool_children(monkeypatch, spans):
    _foundry(monkeypatch, [_tool_round("r1", "web_app", "A"), _tool_round("r2", "sql_status", "B"), _answer("r3")])
    r = trace.run("ot-0001", run_agent, "blackbox", "sys", {"q": 1}, Out, lambda: Out(answer="fb"))
    assert r.via == "foundry-agent" and r.usage["input_tokens"] == 300
    done = spans()
    [agent] = _by_name(done, "invoke_agent")
    [root] = _by_name(done, "dr.run")
    tools = _by_name(done, "execute_tool")
    [check] = _by_name(done, "validate")
    a = agent.attributes
    assert agent.name == "invoke_agent dra-dev-blackbox" and agent.kind == ot.SpanKind.CLIENT
    assert (a["gen_ai.operation.name"], a["gen_ai.agent.name"], a["gen_ai.agent.id"], a["gen_ai.agent.version"]) == \
        ("invoke_agent", "dra-dev-blackbox", "dra-dev-blackbox:8", "8")
    assert (a["gen_ai.request.model"], a["gen_ai.response.model"], a["gen_ai.response.id"]) == \
        ("gpt-4.1-mini", "gpt-4.1-mini-2025-04-14", "r3")
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"],
            a["gen_ai.usage.cache_read.input_tokens"], a["dr.rounds"]) == (300, 30, 120, 3)
    assert a["dr.cost_usd"] == pytest.approx(telemetry.cost_usd("gpt-4.1-mini", r.usage)) and a["dr.cost_usd"] > 0
    assert (a["dr.via"], a["dr.source"], a["dr.outcome"], a["dr.validation"], a["dr.run_id"]) == \
        ("foundry-agent", "llm", "accepted", "ok", "ot-0001")
    assert [t.attributes["gen_ai.tool.name"] for t in tools] == ["web_app", "sql_status"]
    assert all(t.parent.span_id == agent.context.span_id and t.attributes["gen_ai.operation.name"] == "execute_tool"
               and t.attributes["dr.status"] == "ok" for t in tools)
    assert check.parent.span_id == agent.context.span_id
    assert agent.parent.span_id == root.context.span_id and root.attributes["dr.run_id"] == "ot-0001"
    assert ot.get_current_span() is ot.INVALID_SPAN  # every context the bridge attached was detached


def test_calls_without_a_ui_run_still_emit_spans_tagged_with_the_drill(monkeypatch, spans):
    _foundry(monkeypatch, [_tool_round("r1", "web_app", "A"), _answer("r2")])
    out = {}
    t = threading.Thread(target=lambda: out.update(r=run_agent("validator", "sys", {}, Out, lambda: Out(answer="fb"))),
                         name="exercise-ex-20261019-01")
    t.start()
    t.join()
    assert out["r"].via == "foundry-agent" and trace.emit("tool", "outside a run, no span") is None
    done = spans()
    [agent] = _by_name(done, "invoke_agent")
    assert agent.parent is None and agent.attributes["dr.exercise_id"] == "ex-20261019-01"
    assert agent.attributes["dr.outcome"] == "accepted" and "dr.run_id" not in agent.attributes
    [tool] = _by_name(done, "execute_tool")
    assert tool.parent.span_id == agent.context.span_id and tool.attributes["dr.exercise_id"] == "ex-20261019-01"


def test_foundry_error_then_direct_answer(monkeypatch, spans):
    from dr_agent import llm as llm_mod
    _foundry(monkeypatch, [ConnectionError("network down")])
    monkeypatch.setattr(llm_mod, "get_llm", lambda: FakeLLM(Out(answer="direct")))
    r = run_agent("reporter", "sys", {}, Out, lambda: Out(answer="fb"))
    assert r.via == "azure-openai"
    foundry_span, direct_span = _by_name(spans(), "invoke_agent")
    assert foundry_span.status.status_code == StatusCode.ERROR and foundry_span.attributes["dr.outcome"] == "failed"
    assert "dr.source" not in foundry_span.attributes
    assert direct_span.name == "invoke_agent dra-dev-reporter" and direct_span.attributes["dr.via"] == "azure-openai"
    assert direct_span.attributes["dr.source"] == "llm" and direct_span.attributes["gen_ai.request.model"] == "gpt-4.1-mini"
    assert ot.get_current_span() is ot.INVALID_SPAN


def test_rejected_answer_then_rules_fallback_is_the_final_span(spans):
    def reject(_):
        raise ValueError("does not meet targets")
    r = run_agent("architect", "sys", {}, StrategyRec, lambda: StrategyRec(answer="fb"), llm=FakeLLM(StrategyRec(answer="x")),
                  validate=reject)
    assert r.source == "fallback"
    attempt, final = _by_name(spans(), "invoke_agent")
    assert attempt.attributes["dr.outcome"] == "rejected" and attempt.attributes["dr.validation"] == "fail"
    assert attempt.status.status_code == StatusCode.ERROR
    assert final.name == "invoke_agent dra-dev-architect"
    assert {k: final.attributes[k] for k in ("dr.via", "dr.source", "dr.outcome", "dr.cost_usd")} == \
        {"dr.via": "fallback", "dr.source": "fallback", "dr.outcome": "fallback", "dr.cost_usd": 0.0}
    assert final.start_time <= final.end_time


def test_repair_round_is_marked(spans):
    calls = []

    def validate(out):
        calls.append(out.answer)
        if len(calls) == 1:
            raise RepairableError(["cite the drill id"])
    r = run_agent("blackbox", "sys", {}, Out, lambda: Out(answer="fb"), llm=FakeLLM(Out(answer="a"), Out(answer="b")),
                  validate=validate)
    assert r.output.answer == "b"
    first, second = _by_name(spans(), "invoke_agent")
    assert first.attributes["dr.outcome"] == "rejected" and "dr.repair" not in first.attributes
    assert second.attributes["dr.repair"] is True and second.attributes["dr.outcome"] == "accepted"


def test_blocked_signal_marks_the_final_span(spans):
    trace.emit("agent", "Foundry agent", status="running", data={"agent": "dra-dev-blackbox"})
    trace.emit("blocked", "Content filter blocked the request", status="warn")
    trace.emit("fallback", "Using the deterministic rules fallback", status="warn")
    trace.emit("result", "Rules-based BlackboxAnswer produced")
    attempt, final = _by_name(spans(), "invoke_agent")
    assert attempt.attributes["dr.blocked"] is True and attempt.attributes["dr.outcome"] == "failed"
    assert final.name == "invoke_agent dra-dev-blackbox" and final.attributes["dr.blocked"] is True


def test_span_failures_never_break_the_agent_call(monkeypatch, spans):
    class Broken:
        def start_span(self, *a, **k):
            raise RuntimeError("exporter exploded")

        def start_as_current_span(self, *a, **k):
            raise RuntimeError("exporter exploded")
    monkeypatch.setattr(telemetry, "tracer", lambda: Broken())
    r = trace.run("ot-0002", run_agent, "t", "sys", {}, Out, lambda: Out(answer="fb"), llm=FakeLLM(Out(answer="llm")))
    assert r.source == "llm" and [e["kind"] for e in trace.get("ot-0002")["events"]][:2] == ["agent", "check"]


def test_cost_math():
    usage = {"input_tokens": 1_000_000, "cached_tokens": 400_000, "output_tokens": 100_000}
    assert telemetry.cost_usd("gpt-4.1-mini", usage) == pytest.approx(0.6 * 0.40 + 0.4 * 0.10 + 0.1 * 1.60)
    assert telemetry.cost_usd("gpt-5-mini-2025-08-07", usage) == pytest.approx(0.6 * 0.25 + 0.4 * 0.025 + 0.1 * 2.00)
    assert telemetry.cost_usd("GPT-4.1-MINI", {"input_tokens": 1000, "output_tokens": 0}) == pytest.approx(0.0004)
    assert telemetry.cost_usd("gpt-4.1", usage) is None and telemetry.cost_usd("", usage) is None
    assert telemetry.cost_usd("gpt-4.1-mini", {"input_tokens": 10, "cached_tokens": 50}) == pytest.approx(10 * 0.10 / 1e6)
    assert telemetry.cost_usd("gpt-4.1-mini", None) is None


def test_price_override(monkeypatch):
    monkeypatch.setenv("DRA_MODEL_PRICES", json.dumps({"gpt-4.1": {"input": 2, "output": 8}}))
    assert telemetry.cost_usd("gpt-4.1-2025-04-14", {"input_tokens": 1_000_000, "output_tokens": 0}) == 2.0
    assert telemetry.price_for("gpt-4.1-mini")["input"] == 0.40  # the longer, more specific name still wins
    monkeypatch.setenv("DRA_MODEL_PRICES", "not json")
    assert telemetry.price_for("gpt-4.1-mini") == telemetry.PRICES_USD_PER_1M["gpt-4.1-mini"]


def test_configure_is_a_no_op_without_connection_string(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert telemetry.configure() is False and not telemetry.enabled()
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("TELEMETRY_ENABLED", "false")
    assert telemetry.configure() is False and not telemetry.enabled()


def test_configure_never_raises(monkeypatch):
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=00000000-0000-0000-0000-000000000000")
    monkeypatch.delenv("TELEMETRY_ENABLED", raising=False)
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", None)  # import fails
    assert telemetry.configure() is False and not telemetry.enabled()
    telemetry.shutdown()


def test_polling_endpoints_are_excluded_but_actions_are_not():
    from opentelemetry.util.http import parse_excluded_urls
    ex = parse_excluded_urls(",".join(telemetry.POLLED_PATHS))
    for url in ("http://h/api/trace/abc", "http://h/api/live-map", "http://h/public/live", "http://h/health",
                "http://h/static/app.js", "http://h/api/notifications", "http://h/api/exercise/ex-1",
                "http://h/api/exercise/ex-1/events", "http://h/api/status", "http://h/api/autopilot"):
        assert ex.url_disabled(url), url
    for url in ("http://h/api/blackbox", "http://h/api/exercise/ex-1/approve", "http://h/api/autopilot/run",
                "http://h/api/notifications/n1/read", "http://h/api/phase1", "http://h/exercise/ex-1"):
        assert not ex.url_disabled(url), url


def test_httpx_spans_only_for_model_hosts():
    from opentelemetry.util.http import parse_excluded_urls
    ex = parse_excluded_urls(telemetry.HTTPX_EXCLUDED)
    assert not ex.url_disabled("https://dra-foundry-e23x.services.ai.azure.com/api/projects/dr-agent/openai/v1/responses")
    assert not ex.url_disabled("https://x.openai.azure.com/openai/deployments/gpt-4.1-mini/chat/completions")
    assert ex.url_disabled("https://dra-app-eas-e23x.azurewebsites.net/api/txn")
    assert ex.url_disabled("https://management.azure.com/subscriptions/x?next=https://a.services.ai.azure.com/")


def test_model_http_round_is_a_chat_child_of_invoke_agent(spans):
    import httpx
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    HTTPXClientInstrumentor.instrument_client(client, tracer_provider=telemetry._provider,
                                              request_hook=telemetry._httpx_request_hook)
    idx = trace.emit("agent", "Foundry agent", status="running", data={"agent": "dra-dev-blackbox", "model": "gpt-4.1-mini"})
    client.post("https://a.services.ai.azure.com/api/projects/p/openai/v1/responses", json={})
    client.get("https://a.services.ai.azure.com/api/projects/p/agents/x")
    trace.update(idx, "fail", "HTTP 500")
    done = spans()
    [agent] = _by_name(done, "invoke_agent")
    [chat] = _by_name(done, "chat")
    assert chat.name == "chat gpt-4.1-mini" and chat.parent.span_id == agent.context.span_id
    assert chat.attributes["gen_ai.operation.name"] == "chat"
    assert len(done) == 3  # the GET keeps its plain HTTP name
