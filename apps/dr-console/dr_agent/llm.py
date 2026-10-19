"""Azure OpenAI client with structured outputs and a deterministic fallback (design §4.3).

Auth: Entra ID token via DefaultAzureCredential (managed identity in App Service, `az login` locally).
No API keys. The console's identity needs the "Cognitive Services OpenAI User" role.

Every agent call goes through `run_agent`, which never raises. If the model is slow, unavailable
or returns invalid output, the agent's fallback is used, and the result records which path produced it.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Optional, Type, TypeVar

from pydantic import BaseModel

from dr_agent import trace

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

DEFAULT_API_VERSION = "2024-10-21"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def new_usage() -> dict:
    """Token usage of one agent call, summed over its model rounds (AgentResult.usage)."""
    return {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0, "rounds": 0}


@dataclass
class AgentResult(Generic[T]):
    output: T
    source: str  # "llm" | "fallback"
    latency_ms: int
    error: Optional[str] = None
    via: str = ""  # "foundry-agent" | "azure-openai" | "fallback"
    agent_version: str = ""  # Foundry agent version that answered ("" on the direct and fallback paths)
    model: str = ""  # model deployment that answered ("" for the fallback)
    # tokens summed over every model round of this call: input_tokens, output_tokens, cached_tokens,
    # reasoning_tokens, rounds (zeros where the path does not report them)
    usage: dict = field(default_factory=new_usage)
    evidence: list[dict] = field(default_factory=list)  # tool calls behind the answer: {tool, args, output}
    blocked: bool = False  # the content filter stopped the model (set by WS1)
    grounding: dict = field(default_factory=dict)  # grounding check of the answer against evidence (set by WS2)


class RepairableError(ValueError):
    """Raised by a validate hook when the answer can be fixed: run_agent asks the model once more, with
    `problems` added to the payload as previous_attempt_problems."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "answer needs repair")


class LLM:
    def __init__(self, endpoint: Optional[str] = None, deployment: Optional[str] = None,
                 api_version: Optional[str] = None, timeout_s: float = 60.0, temperature: float = 0.2):
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from openai import AzureOpenAI

        self.endpoint = endpoint or os.environ["AZURE_OPENAI_ENDPOINT"]
        self.deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
        self.temperature = temperature
        self.client = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_version=api_version or os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION),
            azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SCOPE),
            timeout=timeout_s,
            max_retries=1,
        )

    def structured(self, system_prompt: str, payload: dict | str, schema: Type[T]) -> T:
        """One chat call whose reply is parsed into `schema` (strict JSON schema). Raises on failure."""
        user = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
        completions = self.client.chat.completions
        parse = getattr(completions, "parse", None) or self.client.beta.chat.completions.parse  # SDK-version safe
        resp = parse(
            model=self.deployment,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user}],
            response_format=schema,
            temperature=self.temperature,
        )
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise RuntimeError(f"model refused: {msg.refusal}")
        if msg.parsed is None:
            raise RuntimeError("model returned no parsable output")
        return msg.parsed


_llm: Optional[LLM] = None


def get_llm() -> Optional[LLM]:
    """Process-wide client; None when AZURE_OPENAI_ENDPOINT is not configured (fallback-only mode)."""
    global _llm
    if _llm is None and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        try:
            _llm = LLM()
        except Exception:  # noqa: BLE001
            log.exception("could not initialise Azure OpenAI client; agents will use fallbacks")
    return _llm


def _takes_ctx(fn: Callable) -> bool:
    """True if validate accepts a second positional argument (the ctx dict)."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):  # some builtins have no signature
        return False
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 2 or any(p.kind == p.VAR_POSITIONAL for p in params)


def _with_problems(payload: dict | str, problems: list[str]) -> dict:
    base = dict(payload) if isinstance(payload, dict) else {"request": payload}
    return {**base, "previous_attempt_problems": problems}


def run_agent(name: str, system_prompt: str, payload: dict | str, schema: Type[T],
              fallback: Callable[[], T], llm: Optional[LLM] = None,
              validate: Optional[Callable[..., None]] = None, require_tool: bool = False,
              deadline_s: Optional[float] = None) -> AgentResult[T]:
    """Run one agent step. Never raises.

    validate: optional extra check on the model output, called as validate(out) or, if it takes two positional
      arguments, validate(out, ctx) with ctx = {"payload": <the original payload>, "evidence": [{tool, args, output}]}.
      Raise to reject the answer (the next path, finally the fallback, is used instead). Raise RepairableError(problems)
      to get exactly one repair round per call: the same path is asked again with "previous_attempt_problems" added
      to the payload, and if that answer fails validation too, it is rejected as above.
    deadline_s: reserved; enforced by WS1. Passed through to the Foundry agent call.
    The result's usage is summed over every Foundry model round of this call, including rejected attempts.
    """
    started = time.monotonic()
    errors: list[str] = []
    usage = new_usage()
    takes_ctx = bool(validate) and _takes_ctx(validate)
    repaired = False

    def _accept(out: T, via: str, meta: dict) -> AgentResult[T]:
        idx = trace.emit("check", "Code checks the answer",
                         f"Parsed into {schema.__name__}" + ("; now applying this agent's business rules" if validate else ""),
                         status="running")
        try:
            if validate:
                if takes_ctx:
                    validate(out, {"payload": payload, "evidence": meta.get("evidence") or []})
                else:
                    validate(out)
        except Exception as e:  # noqa: BLE001
            trace.update(idx, "fail", f"Rejected: {e}"[:300])
            raise
        trace.update(idx, "ok", f"Valid {schema.__name__}" + (" and passes the business rules" if validate else ""),
                     data=out.model_dump(mode="json"))
        ms = int((time.monotonic() - started) * 1000)
        log.info("agent=%s source=llm via=%s latency_ms=%d", name, via, ms)
        return AgentResult(out, "llm", ms, "; ".join(errors) or None, via, agent_version=meta.get("version") or "",
                           model=meta.get("model") or "", usage=usage, evidence=meta.get("evidence") or [])

    def _attempt(call: Callable[[dict | str, dict], T], via: str) -> AgentResult[T]:
        """call(payload, meta) -> output on one path; validate it, with at most one repair round per run_agent."""
        nonlocal repaired
        meta: dict = {}
        try:
            return _accept(call(payload, meta), via, meta)
        except RepairableError as e:
            if repaired:
                raise
            repaired = True
            trace.emit("repair", "Asking the agent to fix its answer", "; ".join(e.problems)[:300], status="warn")
            meta = {}
            return _accept(call(_with_problems(payload, e.problems), meta), via, meta)

    def _foundry(agents: Any) -> Callable[[dict | str, dict], T]:
        def call(p: dict | str, meta: dict) -> T:
            try:
                return agents.invoke(name, system_prompt, p, schema, require_tool=require_tool,
                                     deadline_s=deadline_s, meta=meta)
            finally:  # tokens are spent even when the reply is then rejected
                for k, v in (meta.get("usage") or {}).items():
                    usage[k] = usage.get(k, 0) + v
        return call

    def _direct(client: Any) -> Callable[[dict | str, dict], T]:
        from dr_agent.foundry import agent_id
        model = getattr(client, "deployment", "") or ""

        def call(p: dict | str, meta: dict) -> T:
            # the direct path reports no token usage yet; it counts as one round
            usage["rounds"] += 1
            meta.update(model=model)
            idx = trace.emit("agent", f"Azure OpenAI deployment {model or 'model'} (direct)", status="running")
            t0 = time.monotonic()
            try:
                out = client.structured(system_prompt, p, schema)
            except Exception as e:  # noqa: BLE001
                trace.update(idx, "fail", f"{type(e).__name__}: {e}"[:300], ms=int((time.monotonic() - t0) * 1000))
                raise
            trace.update(idx, "ok", data={"agent": agent_id(name), "version": "", "model": model,
                                          "usage": {**new_usage(), "rounds": 1}, "response_id": None, "rounds": 1,
                                          "via": "azure-openai"},
                         ms=int((time.monotonic() - t0) * 1000))
            return out
        return call

    # 1) Azure AI Foundry Agent Service (registered prompt agent). Skipped when an explicit llm is injected (tests).
    if llm is None:
        from dr_agent.foundry import get_agents
        agents = get_agents()
        if agents is not None:
            try:
                return _attempt(_foundry(agents), "foundry-agent")
            except Exception as e:  # noqa: BLE001
                errors.append(f"foundry-agent {type(e).__name__}: {e}"[:400])
                log.warning("agent=%s foundry agent failed, trying direct model: %s", name, errors[-1])
                trace.emit("fallback", "Falling back to the model deployment directly", errors[-1][:300], status="warn")
    # 2) Direct structured call to the model deployment.
    client = llm or get_llm()
    if client is not None:
        try:
            return _attempt(_direct(client), "azure-openai")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}"[:500])
            log.warning("agent=%s llm failed, using fallback: %s", name, errors[-1])
    else:
        errors.append("LLM not configured")
    # 3) Deterministic fallback.
    trace.emit("fallback", "Using the deterministic rules fallback", (errors[-1] if errors else "")[:300], status="warn")
    out = fallback()
    trace.emit("result", f"Rules-based {schema.__name__} produced", data=out.model_dump(mode="json"))
    return AgentResult(out, "fallback", int((time.monotonic() - started) * 1000), "; ".join(errors), "fallback",
                       usage=usage)
