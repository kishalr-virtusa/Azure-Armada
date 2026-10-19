"""Azure OpenAI client with structured outputs and a deterministic fallback (design §4.3).

Auth: Entra ID token via DefaultAzureCredential (managed identity in App Service, `az login` locally).
No API keys. The console's identity needs the "Cognitive Services OpenAI User" role.

Every agent call goes through `run_agent`, which never raises. If the model is slow, unavailable
or returns invalid output, the agent's fallback is used, and the result records which path produced it.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, Type, TypeVar

from pydantic import BaseModel

from dr_agent import trace

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

DEFAULT_API_VERSION = "2024-10-21"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


@dataclass
class AgentResult(Generic[T]):
    output: T
    source: str  # "llm" | "fallback"
    latency_ms: int
    error: Optional[str] = None
    via: str = ""  # "foundry-agent" | "azure-openai" | "fallback"


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


def run_agent(name: str, system_prompt: str, payload: dict | str, schema: Type[T],
              fallback: Callable[[], T], llm: Optional[LLM] = None,
              validate: Optional[Callable[[T], None]] = None, require_tool: bool = False) -> AgentResult[T]:
    """Run one agent step. Never raises.

    validate: optional extra check on the model output; raise to reject it (the fallback is used instead).
    """
    started = time.monotonic()
    errors: list[str] = []

    def _accept(out: T, via: str) -> AgentResult[T]:
        idx = trace.emit("check", "Code checks the answer",
                         f"Parsed into {schema.__name__}" + ("; now applying this agent's business rules" if validate else ""),
                         status="running")
        try:
            if validate:
                validate(out)
        except Exception as e:  # noqa: BLE001
            trace.update(idx, "fail", f"Rejected: {e}"[:300])
            raise
        trace.update(idx, "ok", f"Valid {schema.__name__}" + (" and passes the business rules" if validate else ""),
                     data=out.model_dump(mode="json"))
        ms = int((time.monotonic() - started) * 1000)
        log.info("agent=%s source=llm via=%s latency_ms=%d", name, via, ms)
        return AgentResult(out, "llm", ms, "; ".join(errors) or None, via)

    # 1) Azure AI Foundry Agent Service (registered prompt agent). Skipped when an explicit llm is injected (tests).
    if llm is None:
        from dr_agent.foundry import get_agents
        agents = get_agents()
        if agents is not None:
            try:
                return _accept(agents.invoke(name, system_prompt, payload, schema, require_tool=require_tool), "foundry-agent")
            except Exception as e:  # noqa: BLE001
                errors.append(f"foundry-agent {type(e).__name__}: {e}"[:400])
                log.warning("agent=%s foundry agent failed, trying direct model: %s", name, errors[-1])
                trace.emit("fallback", "Falling back to the model deployment directly", errors[-1][:300], status="warn")
    # 2) Direct structured call to the model deployment.
    client = llm or get_llm()
    if client is not None:
        try:
            out = trace.step("agent", f"Azure OpenAI deployment {getattr(client, 'deployment', 'model')} (direct)", client.structured,
                             system_prompt, payload, schema)
            return _accept(out, "azure-openai")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}"[:500])
            log.warning("agent=%s llm failed, using fallback: %s", name, errors[-1])
    else:
        errors.append("LLM not configured")
    # 3) Deterministic fallback.
    trace.emit("fallback", "Using the deterministic rules fallback", (errors[-1] if errors else "")[:300], status="warn")
    out = fallback()
    trace.emit("result", f"Rules-based {schema.__name__} produced", data=out.model_dump(mode="json"))
    return AgentResult(out, "fallback", int((time.monotonic() - started) * 1000), "; ".join(errors), "fallback")
