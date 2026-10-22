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
from dr_agent.llm import (MAX_REQUEST_S, ContentBlocked, DeadlineExceeded, new_usage, request_cap, request_timeout,
                           retry_delay)

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


# agents that need multi-step reasoning run on the reasoning model (REASONING_DEPLOYMENT, e.g. gpt-5-mini); the
# Validator runs on the fast model because a drill waits on its diagnosis (WS4's validator eval confirms this)
REASONING_AGENTS = {"planner"}
# a 400 with one of these, or an incomplete response with reason content_filter, is a content-filter block
CONTENT_FILTER_MARKERS = ("content_filter", "ResponsibleAIPolicyViolation")
RESOLVE_CACHE_S = 60.0


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
        self._versions: dict[str, str] = {}  # agent id -> Foundry version in use (pinned on every invoke)
        self._hashes: dict[str, str] = {}  # agent id -> spec hash of that version
        self._resolved: dict[str, tuple[float, str]] = {}  # agent id -> (checked at, spec hash of latest), unpinned mode
        self._pin = True  # False once the endpoint rejects agent_reference.version
        self._lock = threading.Lock()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._cred.get_token(SCOPE).token}", "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None,
             deadline: Optional[float] = None, cap: float = MAX_REQUEST_S) -> httpx.Response:
        """One REST call, timed out at min(time left, cap) when a deadline (time.monotonic()) is given.
        429/500/502/503 is retried once, after retry-after (at most 5 s), if the deadline leaves room for it."""
        for attempt in range(2):
            timeout = httpx.USE_CLIENT_DEFAULT if deadline is None else request_timeout(deadline, cap)
            r = self._http.request(method, self.endpoint + path, headers=self._headers(), json=body, params=params,
                                   timeout=timeout)
            delay = retry_delay(r.status_code, r.headers)
            if attempt or delay is None or (deadline is not None and time.monotonic() + delay + 1 >= deadline):
                return r
            log.warning("foundry %s %s: HTTP %d, retrying in %.1fs", method, path, r.status_code, delay)
            trace.emit("retry", f"Foundry returned HTTP {r.status_code}; retrying once in {delay:.0f}s", status="warn")
            time.sleep(delay)
        return r

    def _post_response(self, aid: str, body: dict, deadline: Optional[float], cap: float = MAX_REQUEST_S) -> dict:
        """POST one Responses API round (timed out at min(time left, cap)); raises ContentBlocked when Azure AI
        Content Safety stopped it."""
        r = self._req("POST", "/openai/v1/responses", body, deadline=deadline, cap=cap)
        if r.status_code >= 300:
            if r.status_code == 400 and any(k in r.text for k in CONTENT_FILTER_MARKERS):
                raise ContentBlocked(f"agent {aid}: blocked by the content filter: {r.text[:300]}")
            raise RuntimeError(f"agent {aid} run failed: HTTP {r.status_code} {r.text[:300]}")
        data = r.json()
        if data.get("status") == "incomplete" and (data.get("incomplete_details") or {}).get("reason") == "content_filter":
            raise ContentBlocked(f"agent {aid}: answer stopped by the content filter")
        return data

    def _run_tools(self, agent: str, aid: str, data: dict, evidence: list[dict], usage: dict,
                   deadline: Optional[float] = None, cap: float = MAX_REQUEST_S) -> dict:
        """While the agent asks for tools: run them (read-only, allowlisted per agent), send the results back, repeat.

        At most MAX_TOOL_CALLS calls run; once the cap is reached, any further calls get an error output and the
        round is sent with tool_choice "none", so the model must answer with what it has. Malformed arguments become
        an error output for the model instead of an exception. Each executed call is appended to `evidence` as
        {tool, args, output}; each response's tokens are added to `usage`."""
        from dr_agent.agents import tools as agent_tools
        calls_made = 0
        while _tool_calls(data):
            outputs = []
            for c in _tool_calls(data):
                if calls_made >= agent_tools.MAX_TOOL_CALLS:
                    outputs.append(_tool_output(c, {"error": "tool call limit reached; answer from the evidence you have"}))
                    continue
                calls_made += 1
                try:
                    args = json.loads(c.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError:
                    trace.emit("tool", f"AI agent called {c.get('name')} with malformed arguments",
                               str(c.get("arguments"))[:200], status="fail")
                    outputs.append(_tool_output(c, {"error": "invalid arguments"}))
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    raise DeadlineExceeded(f"agent {aid}: deadline reached during tool calls")
                label = f"{c.get('name')}({', '.join(str(v) for v in args.values())})"
                idx = trace.emit("tool", f"AI agent called {label}", "read-only investigation chosen by the agent", status="running",
                                 data={"tool": c.get("name", "")})
                started = time.monotonic()
                out = agent_tools.call(agent, c.get("name", ""), json.dumps(args))
                result = json.loads(out)
                summary = "; ".join(f"{k}={v}" for k, v in list(result.items())[:4]) if isinstance(result, dict) else str(result)[:200]
                failed = isinstance(result, dict) and set(result) == {"error"}  # a tool failure; records may carry error=None
                trace.update(idx, "fail" if failed else "ok", summary[:240], data=result,
                             ms=int((time.monotonic() - started) * 1000))
                evidence.append({"tool": c.get("name", ""), "args": args, "output": result})
                outputs.append({"type": "function_call_output", "call_id": c["call_id"], "output": out})
            body = {"agent_reference": self._reference(aid), "previous_response_id": data["id"], "input": outputs}
            capped = calls_made >= agent_tools.MAX_TOOL_CALLS
            if capped:
                body["tool_choice"] = "none"
                trace.emit("tool", f"Tool call limit ({agent_tools.MAX_TOOL_CALLS}) reached",
                           "the agent must now answer from the evidence it has", status="warn")
            data = self._post_response(aid, body, deadline, cap)
            _add_usage(usage, data)
            if capped:
                break
        return data

    # ---------------------------------------------------------------- registration
    def spec(self, name: str, instructions: str, schema: Type[BaseModel]) -> tuple[dict, str]:
        """The agent definition this code registers, and its spec hash."""
        base = name.split("-")[0]
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
        return definition, hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:16]

    def ensure(self, name: str, instructions: str, schema: Type[BaseModel],
               deadline: Optional[float] = None) -> tuple[str, str]:
        """Make sure the Foundry agent exists with exactly this spec; returns (agent id, version to pin)."""
        base, prefix = name.split("-")[0], agent_prefix()
        aid = prefix + base
        definition, spec_hash = self.spec(name, instructions, schema)
        if self._ready.get(aid) == spec_hash:
            return aid, self._versions.get(aid, "")
        with self._lock:
            if self._ready.get(aid) == spec_hash:
                return aid, self._versions.get(aid, "")
            r = self._req("GET", f"/agents/{aid}", params={"api-version": API_VERSION}, deadline=deadline)
            latest = (r.json().get("versions") or {}).get("latest") or {} if r.status_code == 200 else {}
            self._versions[aid] = str(latest.get("version") or "")
            self._hashes[aid] = (latest.get("metadata") or {}).get("spec_hash") or ""
            current = self._hashes[aid] == spec_hash
            if not current and not may_register(prefix):
                if not latest:
                    raise RuntimeError(f"agent {aid} is not registered and registration is off for prefix {prefix!r}")
                log.warning("foundry agent %s spec changed but registration is off; using version %s as-is",
                            aid, self._versions[aid])
            elif not current:
                body = {"definition": definition, "metadata": {"spec_hash": spec_hash, "system": "dr-agent"},
                        "description": AGENTS.get(base, ("", "DR Agent"))[1]}
                r = self._req("POST", f"/agents/{aid}/versions", body, params={"api-version": API_VERSION},
                              deadline=deadline)
                if r.status_code >= 300:
                    raise RuntimeError(f"register {aid} failed: HTTP {r.status_code} {r.text[:300]}")
                self._versions[aid], self._hashes[aid] = str(r.json().get("version") or ""), spec_hash
                log.info("foundry agent %s registered version %s", aid, self._versions[aid])
                trace.emit("agent", f"Registered new version {self._versions[aid]} of {aid}",
                           "Instructions or output schema changed, so Foundry got a new agent version")
            self._ready[aid] = spec_hash
        return aid, self._versions[aid]

    def _reference(self, aid: str) -> dict:
        """agent_reference pinned to the exact version this process registered or found."""
        ref = {"name": aid, "type": "agent_reference"}
        if self._pin and self._versions.get(aid):
            ref["version"] = self._versions[aid]
        return ref

    def _verify_resolved(self, aid: str, deadline: Optional[float]) -> None:
        """Unpinned mode: the latest version (looked up at most every 60 s) must still have the spec we pinned."""
        checked, resolved = self._resolved.get(aid, (0.0, ""))
        if time.monotonic() - checked > RESOLVE_CACHE_S:
            r = self._req("GET", f"/agents/{aid}", params={"api-version": API_VERSION}, deadline=deadline)
            latest = (r.json().get("versions") or {}).get("latest") or {} if r.status_code == 200 else {}
            resolved = (latest.get("metadata") or {}).get("spec_hash") or ""
            self._resolved[aid] = (time.monotonic(), resolved)
        if resolved != self._hashes.get(aid):
            raise RuntimeError(f"agent {aid}: latest version has spec {resolved or '?'}, expected "
                               f"{self._hashes.get(aid) or '?'} (version {self._versions.get(aid) or '?'})")

    # ---------------------------------------------------------------- invocation
    def invoke(self, name: str, instructions: str, payload: dict | str, schema: Type[T], require_tool: bool = False,
               deadline_s: Optional[float] = None, meta: Optional[dict] = None) -> T:
        """Run the Foundry agent once, pinned to its exact version, and parse its structured reply. Raises on any
        failure: ContentBlocked when Azure AI Content Safety stopped the run, DeadlineExceeded when out of time.

        deadline_s: seconds this call may take (every HTTP request is timed out at min(time left, the agent's
        request cap, llm.request_cap)).
        meta: if given, filled with {agent, version, model, usage, response_id, rounds, evidence} as soon as the
        run has answered, so the caller has them even when the reply then fails to parse.
        """
        deadline = None if deadline_s is None else time.monotonic() + deadline_s
        base = name.split("-")[0]
        cap = request_cap(base)
        aid, version = self.ensure(name, instructions, schema, deadline=deadline)
        if not self._pin:
            self._verify_resolved(aid, deadline)
        user = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
        body = {"agent_reference": self._reference(aid), "input": user}
        if require_tool:  # something is wrong: the agent must investigate with at least one tool before answering
            body["tool_choice"] = "required"
        model = self.reasoning_model if base in REASONING_AGENTS and self.reasoning_model else self.model
        idx = trace.emit("agent", f"Azure AI Foundry agent {aid} (version {version or '?'})",
                         f"Model {model} · output must match the {schema.__name__} JSON schema", status="running",
                         data={"agent": aid, "version": version, "model": model,
                               "api": "POST {project}/openai/v1/responses (agent_reference)",
                               "input_chars": len(user), "output_schema": schema.__name__})
        started = time.monotonic()
        usage, evidence = new_usage(), []
        try:
            try:
                data = self._post_response(aid, body, deadline, cap)
            except RuntimeError as e:
                if isinstance(e, ContentBlocked) or not (self._pin and _version_rejected(str(e))):
                    raise
                # the endpoint does not take agent_reference.version: run unpinned, checking the spec instead
                log.warning("foundry rejects agent_reference.version; verifying spec_hash per call instead: %s", e)
                self._pin = False
                self._verify_resolved(aid, deadline)
                body["agent_reference"] = self._reference(aid)
                data = self._post_response(aid, body, deadline, cap)
            _add_usage(usage, data)
            data = self._run_tools(base, aid, data, evidence, usage, deadline, cap)
        except Exception as e:  # noqa: BLE001
            why = ("Blocked by Azure AI Content Safety (Prompt Shields)" if isinstance(e, ContentBlocked)
                   else f"{type(e).__name__}: {e}"[:300])
            trace.update(idx, "fail", why, ms=int((time.monotonic() - started) * 1000))
            raise
        ms = int((time.monotonic() - started) * 1000)
        reasoning = usage["reasoning_tokens"]
        info = {"agent": aid, "version": version, "model": data.get("model") or model,
                "usage": usage, "response_id": data.get("id"), "rounds": usage["rounds"]}
        if meta is not None:
            meta.update(info, evidence=evidence)
        trace.update(idx, "ok", f"Answered in {ms / 1000:.1f}s · {usage['input_tokens']} tokens in, "
                                f"{usage['output_tokens']} out" + (f" ({reasoning} reasoning)" if reasoning else ""),
                     data={**info, "via": "foundry-agent", "status": data.get("status")}, ms=ms)
        return _parse_answer(aid, data, schema)


def _parse_answer(aid: str, data: dict, schema: Type[T]) -> T:
    """The structured answer of a Responses API reply.

    Each message item is parsed on its own: gpt-4.1-mini sometimes returns two or three message items in one reply,
    each a complete JSON answer, and joining them gave "Invalid JSON: trailing characters" (5 of 30 Validator eval runs
    lost their tool-backed answer this way). The last answer that parses is used; how many there were is traced."""
    texts = ["".join(c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text")
             for item in data.get("output", []) if item.get("type") == "message"]
    texts = [t for t in texts if t.strip()]
    if not texts:
        raise RuntimeError(f"agent {aid} returned no text (status={data.get('status')})")
    answers, error = [], None
    for t in texts:
        try:
            answers.append(schema.model_validate_json(t))
        except ValueError as e:  # pydantic.ValidationError is a ValueError
            error = e
    if not answers:
        raise error  # type: ignore[misc]
    if len(texts) > 1:
        distinct = len({a.model_dump_json() for a in answers})
        log.warning("agent %s returned %d answers (%d valid, %d distinct); using the last valid one",
                    aid, len(texts), len(answers), distinct)
        trace.emit("check", f"Agent returned {len(texts)} answers in one reply; using the last valid one",
                   f"{len(answers)} valid, {distinct} distinct", status="warn")
    return answers[-1]


def _tool_calls(data: dict) -> list[dict]:
    return [o for o in data.get("output", []) if o.get("type") == "function_call"]


def _tool_output(call: dict, result: dict) -> dict:
    return {"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}


def _version_rejected(error: str) -> bool:
    """A 400 about agent_reference.version (the endpoint predates version pinning)."""
    return "HTTP 400" in error and "version" in error.lower() and "api-version" not in error.lower()


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
            out[name] = client.ensure(name, prompt(name), getattr(models, schema_name))[0]
        except Exception as e:  # noqa: BLE001
            log.warning("foundry registration of %s failed: %s", name, e)
            out[name] = f"error: {e}"[:200]
    return out
