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
        def invoke(self, name, instr, payload, schema, require_tool=False):
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


def test_agent_id_and_strict_schema():
    from dr_agent.foundry import agent_id, strict_schema
    from dr_agent.models import ExercisePlan
    assert agent_id("planner-retry") == "dra-planner"
    sch = strict_schema(ExercisePlan)
    assert sch["additionalProperties"] is False
