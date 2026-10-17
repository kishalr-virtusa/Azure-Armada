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
