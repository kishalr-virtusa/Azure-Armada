"""Azure AI Foundry Agent Service: the seven DR agents are registered as Foundry prompt agents and invoked there.

Each agent is a versioned agent in the Foundry project (visible in the Foundry portal under Agents, with its
instructions, model and strict JSON output schema). The console invokes it through the project's
OpenAI-compatible Responses endpoint with an `agent_reference`, so every call is a Foundry agent run.

Registration is idempotent: a hash of (instructions, schema, model) is kept in the agent's metadata and a new
version is only created when it changes. Plain REST over httpx keeps the dependency set small.

Auth: Entra ID (managed identity in App Service, `az login` locally). The console identity has "Azure AI User".

Environment isolation:
- DRA_AGENT_PREFIX: agent name prefix. Defaults to "dra-" on App Service (WEBSITE_SITE_NAME is set), otherwise
  "dra-dev-", so a laptop or eval run gets its own dra-dev-* agents and never touches the production dra-* ones.
- DRA_REGISTER_AGENTS: "true" (default) lets ensure() create a new agent version when the spec changed; "false"
  never writes and invokes the latest registered version as-is. Off App Service the production prefix "dra-" is
  never written, whatever this says.
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
from dr_agent.llm import new_usage

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

SCOPE = "https://ai.azure.com/.default"
API_VERSION = "v1"
PROD_PREFIX = "dra-"  # the agents the deployed console uses
DEV_PREFIX = "dra-dev-"

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


def agent_prefix() -> str:
    """DRA_AGENT_PREFIX, else "dra-" on App Service and "dra-dev-" anywhere else."""
    return os.environ.get("DRA_AGENT_PREFIX") or (PROD_PREFIX if os.environ.get("WEBSITE_SITE_NAME") else DEV_PREFIX)


def may_register(prefix: str) -> bool:
    """Whether this process may create agent versions under `prefix` (see DRA_REGISTER_AGENTS above)."""
    if prefix == PROD_PREFIX and not os.environ.get("WEBSITE_SITE_NAME"):
        return False
    return os.environ.get("DRA_REGISTER_AGENTS", "true").lower() != "false"


def agent_id(name: str) -> str:
    """'planner-retry' -> 'dra-planner' (retries reuse the base agent); the prefix comes from agent_prefix()."""
    return agent_prefix() + name.split("-")[0]


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

    def _run_tools(self, agent: str, aid: str, data: dict, evidence: list[dict], usage: dict) -> dict:
        """While the agent asks for tools: run them (read-only, allowlisted per agent), send the results back, repeat.

        Each call is appended to `evidence` as {tool, args, output}; each response's tokens are added to `usage`."""
        from dr_agent.agents import tools as agent_tools
        calls_made = 0
        while _tool_calls(data) and calls_made < agent_tools.MAX_TOOL_CALLS:
            outputs = []
            for c in _tool_calls(data):
                calls_made += 1
                args = json.loads(c.get("arguments") or "{}")
                label = f"{c.get('name')}({', '.join(str(v) for v in args.values())})"
                idx = trace.emit("tool", f"AI agent called {label}", "read-only investigation chosen by the agent", status="running")
                started = time.monotonic()
                out = agent_tools.call(agent, c.get("name", ""), c.get("arguments", "{}"))
                result = json.loads(out)
                summary = "; ".join(f"{k}={v}" for k, v in list(result.items())[:4]) if isinstance(result, dict) else str(result)[:200]
                failed = isinstance(result, dict) and set(result) == {"error"}  # a tool failure; records may carry error=None
                trace.update(idx, "fail" if failed else "ok", summary[:240], data=result,
                             ms=int((time.monotonic() - started) * 1000))
                evidence.append({"tool": c.get("name", ""), "args": args, "output": result})
                outputs.append({"type": "function_call_output", "call_id": c["call_id"], "output": out})
            r = self._req("POST", "/openai/v1/responses", {"agent_reference": {"name": aid, "type": "agent_reference"},
                                                          "previous_response_id": data["id"], "input": outputs})
            if r.status_code >= 300:
                raise RuntimeError(f"agent {aid} tool round failed: HTTP {r.status_code} {r.text[:300]}")
            data = r.json()
            _add_usage(usage, data)
        return data

    # ---------------------------------------------------------------- registration
    def ensure(self, name: str, instructions: str, schema: Type[BaseModel]) -> str:
        """Make sure the Foundry agent exists with exactly this spec; returns the agent id."""
        base, prefix = name.split("-")[0], agent_prefix()
        aid = prefix + base
        definition = {
            "kind": "prompt",
            "model": self.model,
            "instructions": instructions,
            "temperature": 0.2,
            "text": {"format": {"type": "json_schema", "name": schema.__name__,
                                "schema": strict_schema(schema), "strict": True}},
        }
        from dr_agent.agents import tools as agent_tools
        tool_defs = agent_tools.definitions(base)
        if tool_defs:  # read-only investigation tools the agent may call itself
            definition["tools"] = tool_defs
        if base in REASONING_AGENTS and self.reasoning_model:
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
            current = (latest.get("metadata") or {}).get("spec_hash") == spec_hash
            if not current and not may_register(prefix):
                if not latest:
                    raise RuntimeError(f"agent {aid} is not registered and registration is off for prefix {prefix!r}")
                log.warning("foundry agent %s spec changed but registration is off; using version %s as-is",
                            aid, self._versions[aid])
            elif not current:
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
    def invoke(self, name: str, instructions: str, payload: dict | str, schema: Type[T], require_tool: bool = False,
               deadline_s: Optional[float] = None, meta: Optional[dict] = None) -> T:
        """Run the Foundry agent once and parse its structured reply. Raises on any failure.

        deadline_s: reserved; enforced by WS1 (the HTTP client timeout still applies).
        meta: if given, filled with {agent, version, model, usage, response_id, rounds, evidence} as soon as the
        run has answered, so the caller has them even when the reply then fails to parse.
        """
        base = name.split("-")[0]
        aid = self.ensure(name, instructions, schema)
        user = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
        body = {"agent_reference": {"name": aid, "type": "agent_reference"}, "input": user}
        if require_tool:  # something is wrong: the agent must investigate with at least one tool before answering
            body["tool_choice"] = "required"
        model = self.reasoning_model if base in REASONING_AGENTS and self.reasoning_model else self.model
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
        usage, evidence = new_usage(), []
        _add_usage(usage, data)
        data = self._run_tools(base, aid, data, evidence, usage)
        ms = int((time.monotonic() - started) * 1000)
        reasoning = usage["reasoning_tokens"]
        info = {"agent": aid, "version": self._versions.get(aid) or "", "model": data.get("model") or model,
                "usage": usage, "response_id": data.get("id"), "rounds": usage["rounds"]}
        if meta is not None:
            meta.update(info, evidence=evidence)
        trace.update(idx, "ok", f"Answered in {ms / 1000:.1f}s · {usage['input_tokens']} tokens in, "
                                f"{usage['output_tokens']} out" + (f" ({reasoning} reasoning)" if reasoning else ""),
                     data={**info, "via": "foundry-agent", "status": data.get("status")}, ms=ms)
        text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", []) if c.get("type") == "output_text")
        if not text:
            raise RuntimeError(f"agent {aid} returned no text (status={data.get('status')})")
        return schema.model_validate_json(text)


def _tool_calls(data: dict) -> list[dict]:
    return [o for o in data.get("output", []) if o.get("type") == "function_call"]


def _add_usage(total: dict, data: dict) -> None:
    """Add one Responses API reply's usage to `total`; a reply without usage still counts as a round."""
    u = data.get("usage") or {}
    total["rounds"] += 1
    total["input_tokens"] += u.get("input_tokens") or 0
    total["output_tokens"] += u.get("output_tokens") or 0
    total["cached_tokens"] += (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
    total["reasoning_tokens"] += (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0


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
