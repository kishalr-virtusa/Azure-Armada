"""Azure AI Foundry Agent Service: the seven DR agents are registered as Foundry prompt agents and invoked there.

Each agent is a versioned agent in the Foundry project (visible in the Foundry portal under Agents, with its
instructions, model and strict JSON output schema). The console invokes it through the project's
OpenAI-compatible Responses endpoint with an `agent_reference`, so every call is a Foundry agent run.

Registration is idempotent: a hash of (instructions, schema, model) is kept in the agent's metadata and a new
version is only created when it changes. Plain REST over httpx keeps the dependency set small.

Auth: Entra ID (managed identity in App Service, `az login` locally). The console identity has "Azure AI User".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Optional, Type, TypeVar

import httpx
from pydantic import BaseModel

from dr_agent import trace

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

SCOPE = "https://ai.azure.com/.default"
API_VERSION = "v1"
PREFIX = "dra-"

# agent name -> (output schema import path, description shown in the Foundry portal)
AGENTS: dict[str, tuple[str, str]] = {
    "architect": ("StrategyRec", "Phase 1: recommends a DR strategy from RTO/RPO, criticality and cost."),
    "readiness": ("ReadinessSummary", "Phase 1: go/no-go summary of deterministic readiness checks."),
    "runbook": ("RunbookReview", "Phase 1: reviews the DR runbook against the live inventory."),
    "planner": ("ExercisePlan", "Phase 2: builds the ordered, allowlisted DR exercise plan."),
    "validator": ("Diagnosis", "Phase 2: diagnoses a failed exercise step and proposes retry/rollback."),
    "reporter": ("ReportNarrative", "Phase 2: writes the exercise report and remediation backlog."),
    "blackbox": ("BlackboxAnswer", "Black box: answers questions about recorded drills, only from the records, with citations."),
}


# agents that need multi-step reasoning run on the reasoning model (REASONING_DEPLOYMENT, e.g. gpt-5-mini)
REASONING_AGENTS = {"planner", "validator"}


def agent_id(name: str) -> str:
    """'planner-retry' -> 'dra-planner' (retries reuse the base agent)."""
    base = name.split("-")[0]
    return PREFIX + base


def strict_schema(schema: Type[BaseModel]) -> dict:
    from openai.lib._pydantic import to_strict_json_schema
    return to_strict_json_schema(schema)


class FoundryAgents:
    def __init__(self, project_endpoint: str, model: str, timeout_s: float = 90.0, reasoning_model: str = ""):
        from azure.identity import DefaultAzureCredential
        self.endpoint = project_endpoint.rstrip("/")
        self.model = model
        self.reasoning_model = reasoning_model
        self._cred = DefaultAzureCredential()
        self._http = httpx.Client(timeout=timeout_s)
        self._ready: dict[str, str] = {}  # agent id -> spec hash registered in this process
        self._versions: dict[str, str] = {}  # agent id -> Foundry version in use
        self._lock = threading.Lock()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._cred.get_token(SCOPE).token}", "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> httpx.Response:
        return self._http.request(method, self.endpoint + path, headers=self._headers(), json=body, params=params)

    def _run_tools(self, aid: str, data: dict) -> dict:
        """While the agent asks for tools: run them (read-only, allowlisted per agent), send the results back, repeat."""
        from dr_agent.agents import tools as agent_tools
        agent, calls_made = aid.removeprefix(PREFIX), 0
        while _tool_calls(data) and calls_made < agent_tools.MAX_TOOL_CALLS:
            outputs = []
            for c in _tool_calls(data):
                calls_made += 1
                label = f"{c.get('name')}({', '.join(str(v) for v in json.loads(c.get('arguments') or '{}').values())})"
                idx = trace.emit("tool", f"AI agent called {label}", "read-only investigation chosen by the agent", status="running")
                started = time.monotonic()
                out = agent_tools.call(agent, c.get("name", ""), c.get("arguments", "{}"))
                result = json.loads(out)
                summary = "; ".join(f"{k}={v}" for k, v in list(result.items())[:4]) if isinstance(result, dict) else str(result)[:200]
                failed = isinstance(result, dict) and set(result) == {"error"}  # a tool failure; records may carry error=None
                trace.update(idx, "fail" if failed else "ok", summary[:240], data=result,
                             ms=int((time.monotonic() - started) * 1000))
                outputs.append({"type": "function_call_output", "call_id": c["call_id"], "output": out})
            r = self._req("POST", "/openai/v1/responses", {"agent_reference": {"name": aid, "type": "agent_reference"},
                                                          "previous_response_id": data["id"], "input": outputs})
            if r.status_code >= 300:
                raise RuntimeError(f"agent {aid} tool round failed: HTTP {r.status_code} {r.text[:300]}")
            data = r.json()
        return data

    # ---------------------------------------------------------------- registration
    def ensure(self, name: str, instructions: str, schema: Type[BaseModel]) -> str:
        """Make sure the Foundry agent exists with exactly this spec; returns the agent id."""
        aid = agent_id(name)
        definition = {
            "kind": "prompt",
            "model": self.model,
            "instructions": instructions,
            "temperature": 0.2,
            "text": {"format": {"type": "json_schema", "name": schema.__name__,
                                "schema": strict_schema(schema), "strict": True}},
        }
        from dr_agent.agents import tools as agent_tools
        tool_defs = agent_tools.definitions(aid.removeprefix(PREFIX))
        if tool_defs:  # read-only investigation tools the agent may call itself
            definition["tools"] = tool_defs
        if aid.removeprefix(PREFIX) in REASONING_AGENTS and self.reasoning_model:
            definition["model"] = self.reasoning_model
            definition.pop("temperature")  # reasoning models only accept the default
            definition["reasoning"] = {"effort": "low"}
        spec_hash = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:16]
        if self._ready.get(aid) == spec_hash:
            return aid
        with self._lock:
            if self._ready.get(aid) == spec_hash:
                return aid
            r = self._req("GET", f"/agents/{aid}", params={"api-version": API_VERSION})
            latest = (r.json().get("versions") or {}).get("latest") or {} if r.status_code == 200 else {}
            self._versions[aid] = str(latest.get("version") or "")
            if (latest.get("metadata") or {}).get("spec_hash") != spec_hash:
                base = aid.removeprefix(PREFIX)
                body = {"definition": definition, "metadata": {"spec_hash": spec_hash, "system": "dr-agent"},
                        "description": AGENTS.get(base, ("", "DR Agent"))[1]}
                r = self._req("POST", f"/agents/{aid}/versions", body, params={"api-version": API_VERSION})
                if r.status_code >= 300:
                    raise RuntimeError(f"register {aid} failed: HTTP {r.status_code} {r.text[:300]}")
                self._versions[aid] = str(r.json().get("version") or "")
                log.info("foundry agent %s registered version %s", aid, self._versions[aid])
                trace.emit("agent", f"Registered new version {self._versions[aid]} of {aid}",
                           "Instructions or output schema changed, so Foundry got a new agent version")
            self._ready[aid] = spec_hash
        return aid

    # ---------------------------------------------------------------- invocation
    def invoke(self, name: str, instructions: str, payload: dict | str, schema: Type[T], require_tool: bool = False) -> T:
        """Run the Foundry agent once and parse its structured reply. Raises on any failure."""
        aid = self.ensure(name, instructions, schema)
        user = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
        body = {"agent_reference": {"name": aid, "type": "agent_reference"}, "input": user}
        if require_tool:  # something is wrong: the agent must investigate with at least one tool before answering
            body["tool_choice"] = "required"
        model = self.reasoning_model if aid.removeprefix(PREFIX) in REASONING_AGENTS and self.reasoning_model else self.model
        idx = trace.emit("agent", f"Azure AI Foundry agent {aid} (version {self._versions.get(aid) or '?'})",
                         f"Model {model} · output must match the {schema.__name__} JSON schema", status="running",
                         data={"agent": aid, "version": self._versions.get(aid), "model": model,
                               "api": "POST {project}/openai/v1/responses (agent_reference)",
                               "input_chars": len(user), "output_schema": schema.__name__})
        started = time.monotonic()
        r = self._req("POST", "/openai/v1/responses", body)
        ms = int((time.monotonic() - started) * 1000)
        if r.status_code >= 300:
            trace.update(idx, "fail", f"HTTP {r.status_code}", ms=ms)
            raise RuntimeError(f"agent {aid} run failed: HTTP {r.status_code} {r.text[:300]}")
        data = r.json()
        data = self._run_tools(aid, data)
        usage = data.get("usage") or {}
        reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        trace.update(idx, "ok", f"Answered in {ms / 1000:.1f}s · {usage.get('input_tokens', '?')} tokens in, "
                                f"{usage.get('output_tokens', '?')} out" + (f" ({reasoning} reasoning)" if reasoning else ""),
                     data={"agent": aid, "version": self._versions.get(aid), "model": data.get("model") or model,
                           "response_id": data.get("id"), "status": data.get("status"), "usage": usage}, ms=ms)
        text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", []) if c.get("type") == "output_text")
        if not text:
            raise RuntimeError(f"agent {aid} returned no text (status={data.get('status')})")
        return schema.model_validate_json(text)


def _tool_calls(data: dict) -> list[dict]:
    return [o for o in data.get("output", []) if o.get("type") == "function_call"]


_client: Optional[FoundryAgents] = None


def get_agents() -> Optional[FoundryAgents]:
    """Process-wide client; None when FOUNDRY_PROJECT_ENDPOINT is unset or USE_AGENT_SERVICE=false."""
    global _client
    ep = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if _client is None and ep and os.environ.get("USE_AGENT_SERVICE", "true").lower() != "false":
        try:
            _client = FoundryAgents(ep, os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
                                    reasoning_model=os.environ.get("REASONING_DEPLOYMENT", ""))
        except Exception:  # noqa: BLE001
            log.exception("could not initialise Foundry Agent Service client")
    return _client


def register_all() -> dict[str, str]:
    """Register all seven agents (called at console startup so they appear in the portal right after deploy)."""
    from dr_agent import models
    from dr_agent.agents import prompt
    client = get_agents()
    if client is None:
        return {}
    out = {}
    for name, (schema_name, _) in AGENTS.items():
        try:
            out[name] = client.ensure(name, prompt(name), getattr(models, schema_name))
        except Exception as e:  # noqa: BLE001
            log.warning("foundry registration of %s failed: %s", name, e)
            out[name] = f"error: {e}"[:200]
    return out
