"""WS1 runtime: version pinning, deadlines, retries, the tool loop cap, content-filter blocks and engine resume.

Foundry is an httpx.MockTransport, so the real FoundryAgents request code runs; nothing reaches Azure.
"""
import json
import time

import httpx
import pytest
from pydantic import BaseModel

from dr_agent import foundry, llm as llm_mod
from dr_agent.agents import tools as agent_tools


class Out(BaseModel):
    answer: str


def _answer(rid="r-final", text='{"answer": "ok"}', usage=None):
    return {"id": rid, "model": "gpt-4.1-mini", "status": "completed", "usage": usage or {},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def _calls(rid, *calls, usage=None):
    """A response asking for tools; each call is (name, arguments json)."""
    return {"id": rid, "status": "completed", "usage": usage or {},
            "output": [{"type": "function_call", "call_id": f"{rid}-c{i}", "name": n, "arguments": a}
                       for i, (n, a) in enumerate(calls)]}


class FakeFoundry:
    """Scripted Foundry project: the agent exists at version 7 with the spec this code registers (so nothing is
    written) unless `registered` is False or `spec_hash` says otherwise; POST /responses pops `responses` (an httpx.Response, a dict, or a callable
    taking the request)."""

    def __init__(self, responses=(), registered=True):
        self.responses, self.registered, self.requests = list(responses), registered, []
        self.spec_hash = None  # None: the registered version has exactly the spec this code registers

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/api")
        self.requests.append((request.method, path, body))
        if request.method == "GET":
            if not self.registered:
                return httpx.Response(404, json={"error": "not found"})
            spec_hash = self.spec_hash or self.client.spec(path.split("/")[2].split("-")[-1], "sys", Out)[1]
            return httpx.Response(200, json={"versions": {"latest": {"version": "7", "metadata": {"spec_hash": spec_hash}}}})
        if path.endswith("/versions"):
            return httpx.Response(200, json={"version": "8"})
        nxt = self.responses.pop(0)
        if callable(nxt):
            return nxt(request)
        return nxt if isinstance(nxt, httpx.Response) else httpx.Response(200, json=nxt)

    def posts(self, prefix="/openai/v1/responses"):
        return [b for m, p, b in self.requests if m == "POST" and p.startswith(prefix)]


@pytest.fixture
def fake(monkeypatch):
    """A FakeFoundry wired in as the process-wide Foundry client, on App Service (prefix dra-), no direct model."""
    monkeypatch.setenv("WEBSITE_SITE_NAME", "app-dra-console")
    for var in ("DRA_AGENT_PREFIX", "DRA_REGISTER_AGENTS", "REASONING_DEPLOYMENT"):
        monkeypatch.delenv(var, raising=False)
    ff = FakeFoundry()
    client = foundry.FoundryAgents("https://p/api", "gpt-4.1-mini")
    client._cred = type("Cred", (), {"get_token": lambda self, scope: type("T", (), {"token": "t"})()})()
    client._http = httpx.Client(transport=httpx.MockTransport(ff.handler))
    monkeypatch.setattr(foundry, "get_agents", lambda: client)
    monkeypatch.setattr(llm_mod, "get_llm", lambda: None)
    ff.client = client
    return ff


@pytest.fixture
def tools_run(monkeypatch):
    executed = []

    def call(agent, name, arguments):
        executed.append((name, json.loads(arguments)))
        return json.dumps({"ok": True, "n": len(executed)})
    monkeypatch.setattr(agent_tools, "call", call)
    return executed


def run(name="reporter", **kw):
    return llm_mod.run_agent(name, "sys", {"q": 1}, Out, lambda: Out(answer="fb"), **kw)


# ---------------------------------------------------------------- version pinning and environments

def test_invoke_pins_the_exact_agent_version(fake):
    fake.responses = [_answer()]
    r = run()
    assert r.source == "llm" and r.agent_version == "7"
    assert fake.posts()[0]["agent_reference"] == {"name": "dra-reporter", "version": "7", "type": "agent_reference"}
    assert not fake.posts("/agents/")  # spec unchanged: no new version


def test_tool_rounds_stay_on_the_pinned_version(fake, tools_run):
    fake.responses = [_calls("r1", ("probe", '{"target": "A"}')), _answer()]
    run("validator")
    assert [b["agent_reference"].get("version") for b in fake.posts()] == ["7", "7"]


def test_dev_prefix_never_posts_to_production_agents(fake, monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME")
    fake.registered = False  # dra-dev-reporter does not exist yet: it gets registered
    fake.responses = [_answer()]
    r = run()
    assert r.source == "llm" and r.agent_version == "8"
    paths = [p for m, p, _ in fake.requests if p.startswith("/agents/")]
    assert paths and all(p.startswith("/agents/dra-dev-") for p in paths)
    assert fake.posts()[0]["agent_reference"]["name"] == "dra-dev-reporter"


def test_prod_prefix_off_app_service_is_never_registered(fake, monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME")
    monkeypatch.setenv("DRA_AGENT_PREFIX", "dra-")
    fake.spec_hash = "changed-elsewhere"
    fake.responses = [_answer()]
    run()
    assert not fake.posts("/agents/")
    assert fake.posts()[0]["agent_reference"]["version"] == "7"


def test_endpoint_without_version_support_falls_back_to_spec_check(fake):
    rejected = httpx.Response(400, json={"error": {"message": "Unknown parameter: 'agent_reference.version'."}})
    fake.responses = [rejected, _answer("r1"), _answer("r2")]
    assert run().source == "llm" and run().source == "llm"
    first, retried, second = fake.posts()
    assert "version" in first["agent_reference"]
    assert "version" not in retried["agent_reference"] and "version" not in second["agent_reference"]
    # the resolved version's spec is checked with one GET, cached for 60 s
    assert sum(1 for m, p, _ in fake.requests if m == "GET") == 2


def test_spec_check_rejects_a_changed_latest_version(fake):
    fake.client._pin = False
    fake.client.ensure("reporter", "sys", Out)
    fake.spec_hash = "someone-else"
    fake.client._resolved.clear()
    r = run()
    assert r.source == "fallback" and "expected" in r.error and not fake.posts()


# ---------------------------------------------------------------- tool loop

def test_tool_cap_then_forced_answer(fake, tools_run):
    four = [("probe", json.dumps({"target": t})) for t in "ABAB"]
    three = [("web_app", json.dumps({"region": r})) for r in "ABA"]
    fake.responses = [_calls("r1", *four), _calls("r2", *three), _answer("r3")]
    r = run("validator")
    assert r.source == "llm" and r.output.answer == "ok"
    assert len(tools_run) == agent_tools.MAX_TOOL_CALLS == 6 and len(r.evidence) == 6
    final = fake.posts()[-1]
    assert final["tool_choice"] == "none" and final["previous_response_id"] == "r2"
    outputs = {o["call_id"]: json.loads(o["output"]) for o in final["input"]}
    assert len(outputs) == 3 and "limit" in outputs["r2-c2"]["error"]  # every call still gets an output


def test_malformed_tool_arguments_become_tool_error_output(fake, tools_run):
    fake.responses = [_calls("r1", ("probe", "{not json"), ("probe", '["A"]'), ("probe", '{"target": "B"}')), _answer()]
    r = run("validator")
    assert r.source == "llm" and tools_run == [("probe", {"target": "B"})]
    outs = [json.loads(o["output"]) for o in fake.posts()[1]["input"]]
    assert outs[:2] == [{"error": "invalid arguments"}] * 2
    assert r.evidence == [{"tool": "probe", "args": {"target": "B"}, "output": {"ok": True, "n": 1}}]


def test_usage_summed_over_three_rounds(fake, tools_run):
    u = {"input_tokens": 10, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 4},
         "output_tokens_details": {"reasoning_tokens": 1}}
    fake.responses = [_calls("r1", ("probe", '{"target": "A"}'), usage=u),
                      _calls("r2", ("probe", '{"target": "B"}'), usage=u), _answer("r3", usage=u)]
    r = run("validator")
    assert r.usage == {"input_tokens": 30, "output_tokens": 6, "cached_tokens": 12, "reasoning_tokens": 3, "rounds": 3}


# ---------------------------------------------------------------- retries and deadlines

def test_429_with_retry_after_succeeds_on_the_retry(fake):
    fake.responses = [httpx.Response(429, headers={"retry-after": "1"}, json={"error": "throttled"}), _answer()]
    t0 = time.monotonic()
    r = run()
    assert r.source == "llm" and len(fake.posts()) == 2 and time.monotonic() - t0 >= 1


def test_only_one_retry(fake):
    fake.responses = [httpx.Response(503, headers={"retry-after": "0"})] * 3
    r = run()
    assert r.source == "fallback" and len(fake.posts()) == 2 and "HTTP 503" in r.error


def test_no_retry_when_the_wait_would_pass_the_deadline(fake):
    fake.responses = [httpx.Response(429, headers={"retry-after": "5"})]
    r = run(deadline_s=3)
    assert r.source == "fallback" and len(fake.posts()) == 1


def test_hanging_transport_returns_fallback_within_the_deadline(fake, monkeypatch):
    direct = []
    monkeypatch.setattr(llm_mod, "get_llm", lambda: type("L", (), {"structured": lambda *a: direct.append(a)})())

    def hang(request):  # a real transport hangs until its read timeout fires
        time.sleep(min(60, request.extensions["timeout"]["read"]))
        raise httpx.ReadTimeout("read timed out", request=request)
    fake.handler = lambda request, h=fake.handler: hang(request)
    fake.client._http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    t0 = time.monotonic()
    r = run(deadline_s=2)
    assert r.source == "fallback" and time.monotonic() - t0 <= 3
    assert direct == [] and "direct model skipped" in r.error  # under 8 s left: no direct call


def test_default_deadline_per_agent(fake):
    seen = []
    fake.responses = [lambda req: seen.append(req.extensions["timeout"]["read"]) or httpx.Response(200, json=_answer())]
    run("validator")
    assert 24 < seen[0] <= 25  # Validator: 25 s, so its first request may take up to that
    assert llm_mod.DEADLINES["planner"] == 90 and llm_mod.DEADLINES["validator"] == 25


def test_direct_path_retries_once_on_429(monkeypatch):
    class Throttled(Exception):
        status_code = 429
        response = type("R", (), {"headers": {"retry-after": "0"}})()

    class Flaky:
        calls = 0

        def structured(self, *a):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise Throttled("429")
            return Out(answer="direct")
    r = llm_mod.run_agent("t", "sys", {}, Out, lambda: Out(answer="fb"), llm=Flaky())
    assert (r.source, r.output.answer, Flaky.calls) == ("llm", "direct", 2)


# ---------------------------------------------------------------- content filter

def test_content_filter_400_blocks_without_a_direct_call(fake, monkeypatch):
    direct = []
    monkeypatch.setattr(llm_mod, "get_llm", lambda: type("L", (), {"structured": lambda *a: direct.append(a)})())
    fake.responses = [httpx.Response(400, json={"error": {"code": "content_filter", "innererror": {
        "code": "ResponsibleAIPolicyViolation", "content_filter_result": {"jailbreak": {"filtered": True}}}}})]
    from dr_agent import trace
    r = trace.run("cf", run)
    assert (r.source, r.blocked, direct) == ("fallback", True, [])
    assert len(fake.posts()) == 1  # not retried either
    assert any(e["title"] == "Blocked by Azure AI Content Safety (Prompt Shields)" for e in trace.get("cf")["events"])


def test_incomplete_content_filter_response_after_a_tool_round_blocks(fake, tools_run, monkeypatch):
    direct = []
    monkeypatch.setattr(llm_mod, "get_llm", lambda: type("L", (), {"structured": lambda *a: direct.append(a)})())
    fake.responses = [_calls("r1", ("probe", '{"target": "A"}')),
                      {"id": "r2", "status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": []}]
    r = run("validator")
    assert (r.source, r.blocked, direct) == ("fallback", True, [])


# ---------------------------------------------------------------- routing

def test_only_the_planner_runs_on_the_reasoning_model(monkeypatch):
    assert foundry.REASONING_AGENTS == {"planner"}
    monkeypatch.setenv("REASONING_DEPLOYMENT", "gpt-5-mini")
    seen = []

    class Recording(llm_mod.LLM):
        def __init__(self):
            self.deployment = "gpt-4.1-mini"

        def structured(self, system_prompt, payload, schema, timeout_s=None, deployment=None):
            seen.append((deployment, timeout_s))
            return Out(answer="x")
    assert llm_mod.run_agent("planner-retry", "sys", {}, Out, lambda: Out(answer="fb"), llm=Recording()).model == "gpt-5-mini"
    assert llm_mod.run_agent("validator", "sys", {}, Out, lambda: Out(answer="fb"), llm=Recording()).model == "gpt-4.1-mini"
    assert seen[0][0] == "gpt-5-mini" and seen[1][0] is None and 0 < seen[1][1] <= 25


# ---------------------------------------------------------------- engine

def _asr_step():
    from dr_agent.models import PlanStep
    return PlanStep(id="iaas-1", phase="pre-checks", title="Start ASR test failover", owner_team="infra",
                    type="automated", action="asr_test_failover", params=[], depends_on=[], success_criteria="",
                    abort_on_failure=False)


class FakeAsr:
    started, watched = 0, []

    def start(self):
        FakeAsr.started += 1
        return {"operation": "op-new", "recovery_point": "rp"}

    def wait_done(self, operation, timeout_s, poll_s=5):
        FakeAsr.watched.append(operation)


def test_resumed_runner_does_not_start_asr_test_failover_again(cosmos, monkeypatch):
    from dr_agent.engine import runner
    from dr_agent.tools import azure_asr, store
    FakeAsr.started, FakeAsr.watched = 0, []
    monkeypatch.setattr(azure_asr.AsrTestFailover, "from_env", classmethod(lambda cls: FakeAsr()))
    store.upsert_exercise("ex-1", {"iaas_started": "2026-10-19T10:00:00+00:00", "iaas_operation": "op-1",
                                   "iaas_failover_done": ""})
    r = runner.ExerciseRunner("ex-1")
    res = r.execute(_asr_step())
    time.sleep(0.05)  # the re-attached watcher thread
    assert FakeAsr.started == 0 and res == {"started": "2026-10-19T10:00:00+00:00", "resumed": True}
    assert FakeAsr.watched == ["op-1"] and store.get_exercise("ex-1")["iaas_failover_done"]


def test_fresh_runner_starts_asr_test_failover(cosmos, monkeypatch):
    from dr_agent.engine import runner
    from dr_agent.tools import azure_asr, store
    FakeAsr.started, FakeAsr.watched = 0, []
    monkeypatch.setattr(azure_asr.AsrTestFailover, "from_env", classmethod(lambda cls: FakeAsr()))
    store.upsert_exercise("ex-2", {"status": "running"})
    runner.ExerciseRunner("ex-2").execute(_asr_step())
    assert FakeAsr.started == 1 and store.get_exercise("ex-2")["iaas_operation"] == "op-new"


def test_handle_failure_gives_the_validator_25s_and_stores_its_version(cosmos, monkeypatch):
    from dr_agent.agents import validator
    from dr_agent.engine import runner
    from dr_agent.llm import AgentResult
    from dr_agent.tools import store
    seen = {}

    def fake_run(step, err, recent, probes, deadline_s=None):
        seen["deadline_s"] = deadline_s
        return AgentResult(validator.fallback(step, err), "llm", 1, None, "foundry-agent", agent_version="4")
    monkeypatch.setattr(validator, "run", fake_run)
    monkeypatch.setattr(runner, "_probe", lambda target: {"ok": True})
    store.upsert_exercise("ex-3", {"status": "running", "command": "continue"})
    r = runner.ExerciseRunner("ex-3")
    monkeypatch.setattr(r, "clear_command", lambda: None)  # keep the queued "continue" so the wait returns at once
    assert r.handle_failure(_asr_step(), "boom") == "continue"
    assert seen["deadline_s"] == 25 and store.get_exercise("ex-3")["diagnosis_version"] == "4"


def test_direct_path_content_filter_blocks(monkeypatch):
    import openai
    req = httpx.Request("POST", "https://aoai/chat/completions")
    err = openai.BadRequestError("filtered", response=httpx.Response(400, request=req), body={"code": "content_filter"})
    client = object.__new__(llm_mod.LLM)
    client.deployment, client.temperature = "gpt-4.1-mini", 0.2
    client.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {
        "parse": staticmethod(lambda **kw: (_ for _ in ()).throw(err))})()})()})()
    r = llm_mod.run_agent("reporter", "sys", {}, Out, lambda: Out(answer="fb"), llm=client)
    assert (r.source, r.blocked) == ("fallback", True) and "ContentBlocked" in r.error
