"""Live activity trace of one agent run, shown in the console while the agent works, and its OpenTelemetry spans.

Only real events are recorded: the Azure reads that gather the facts, the Foundry agent call (agent, version,
model, latency, tokens, $), the code checks on its answer and any fallback. The browser polls
GET /api/trace/{run_id} while its request is running. Traces live in memory (single console instance) and
only the most recent ones are kept.

This module is also the single bridge to OpenTelemetry (exported by dr_agent.telemetry when configured). Every
emit/update/step becomes a span or span event, with or without a UI run id, so drill-time agent calls in the
engine thread are traced too. Spans follow the GenAI semantic conventions:
- "invoke_agent <agent>": one model attempt of an agent (Foundry agent run or direct deployment call), from the
  agent event until its answer has been checked. gen_ai.* attributes, tokens summed over its rounds, dr.cost_usd,
  dr.via, dr.outcome (accepted | rejected | failed | fallback) and, on the call's final span, dr.source. The
  deterministic fallback is an "invoke_agent" span too (dr.via=fallback), so fallback % = fallback / final spans.
- children: "execute_tool <tool>" for each tool the agent called, "validate <agent>" for the code checks, and
  the model HTTP rounds ("chat <model>", from the httpx instrumentation).
- "dr.run" wraps a UI run (dr.run_id); spans in a drill's engine thread carry dr.exercise_id.
With telemetry off (no connection string, tests) none of this runs.
"""
from __future__ import annotations

import contextvars
import itertools
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from contextlib import nullcontext
from typing import Any, Callable, Optional

from dr_agent import telemetry

log = logging.getLogger(__name__)
MAX_TRACES = 50
_current: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_run_id", default=None)
_traces: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def _small(data: Any, limit: int = 6000) -> Any:
    """Keep payloads readable in the UI: JSON-safe and truncated."""
    try:
        text = json.dumps(data, default=str)
    except (TypeError, ValueError):
        return str(data)[:limit]
    return json.loads(text) if len(text) <= limit else {"truncated": text[:limit] + "…"}


def run(run_id: Optional[str], fn: Callable, *args, **kwargs):
    """Run fn with tracing enabled for run_id (no-op tracing when run_id is empty)."""
    if not run_id:
        return fn(*args, **kwargs)
    with _lock:
        _traces[run_id] = {"t0": time.monotonic(), "events": [], "done": False}
        while len(_traces) > MAX_TRACES:
            _traces.popitem(last=False)
    token = _current.set(run_id)
    try:
        with _root_span(run_id):
            try:
                return fn(*args, **kwargs)
            finally:
                _safe(_close_all, "run ended")
    finally:
        _current.reset(token)
        with _lock:
            if run_id in _traces:
                _traces[run_id]["done"] = True


def emit(kind: str, title: str, detail: str = "", status: str = "ok", data: Any = None) -> Optional[int]:
    """Record one event; returns a handle so a 'running' event can be completed later with update().

    Inside a UI run the handle is the event's index. Outside one it is None, unless telemetry is on and the
    event opened a span: then it is a negative number that update() resolves to that span."""
    rid = _current.get()
    idx = None
    if rid:
        with _lock:
            tr = _traces.get(rid)
            if tr is not None:
                ev = {"t_ms": int((time.monotonic() - tr["t0"]) * 1000), "kind": kind, "title": title,
                      "detail": detail, "status": status, "data": _small(data) if data is not None else None}
                tr["events"].append(ev)
                idx = len(tr["events"]) - 1
    if telemetry.enabled():
        idx = _safe(_span_emit, rid, idx, kind, title, detail, status, data, default=idx)
    return idx


def update(idx: Optional[int], status: str, detail: str = "", data: Any = None, ms: Optional[int] = None) -> None:
    """Complete a 'running' event. An agent event whose data has {model, usage} also gets its tokens and $ cost
    (event fields tokens/cost_usd and "· $x" in the detail; the data itself is kept exactly as given)."""
    if idx is None:
        return
    rid = _current.get()
    tokens, cost = _cost(data)
    if rid and idx >= 0:
        with _lock:
            tr = _traces.get(rid)
            if tr is not None and idx < len(tr["events"]):
                ev = tr["events"][idx]
                ev["status"] = status
                if detail:
                    ev["detail"] = detail
                if data is not None:
                    ev["data"] = _small(data)
                if ms is not None:
                    ev["ms"] = ms
                if tokens:
                    ev["tokens"] = tokens
                    ev["cost_usd"] = cost
                    if cost is not None:
                        ev["detail"] = f"{ev['detail']} · {_money(cost)}".lstrip(" ·")
    if telemetry.enabled():
        _safe(_span_update, rid, idx, status, detail, data, ms, cost)


def step(kind: str, title: str, fn: Callable, *args, summary: Optional[Callable[[Any], str]] = None, **kwargs):
    """Trace one call: emitted as running, then completed with its duration and a one-line result summary."""
    idx = emit(kind, title, status="running")
    started = time.monotonic()
    try:
        out = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        update(idx, "fail", f"{type(e).__name__}: {e}"[:300], ms=int((time.monotonic() - started) * 1000))
        raise
    failed = isinstance(out, dict) and "error" in out
    update(idx, "fail" if failed else "ok",
           (str(out.get("error"))[:300] if failed else (summary(out) if summary else "")),
           ms=int((time.monotonic() - started) * 1000))
    return out


def get(run_id: str) -> dict:
    with _lock:
        tr = _traces.get(run_id)
        if tr is None:
            return {"events": [], "done": False, "known": False}
        return {"events": [dict(e) for e in tr["events"]], "done": tr["done"], "known": True}


def snapshot(run_id: str, limit: int = 30000) -> Optional[str]:
    """The finished trace as compact JSON for saving next to the agent's result (Azure Table text fields hold
    ~32K characters, so the largest 'details' payloads are dropped first if needed)."""
    tr = get(run_id)
    if not tr.get("known"):
        return None
    events = tr["events"]
    out = {"events": events, "elapsed_ms": max((e["t_ms"] + (e.get("ms") or 0) for e in events), default=0),
           "tokens": sum(e.get("tokens") or 0 for e in events),
           "cost_usd": round(sum(e.get("cost_usd") or 0.0 for e in events), 6)}
    raw = json.dumps(out, default=str)
    for e in sorted((e for e in events if e.get("data") is not None), key=lambda e: -len(json.dumps(e["data"], default=str))):
        if len(raw) <= limit:
            break
        e["data"] = None
        raw = json.dumps(out, default=str)
    return raw if len(raw) <= limit else None


# --------------------------------------------------------------------------------------------- cost
def _cost(data: Any) -> tuple[int, Optional[float]]:
    """(tokens, USD) of an agent event's data {model, usage}; (0, None) for any other event."""
    if not isinstance(data, dict) or not isinstance(data.get("usage"), dict):
        return 0, None
    u = data["usage"]
    tokens = int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
    return tokens, telemetry.cost_usd(str(data.get("model") or ""), u)


def _money(usd: float) -> str:
    return f"${usd:.4f}" if usd >= 0.0001 or usd == 0 else "<$0.0001"


# --------------------------------------------------------------------------------------------- OpenTelemetry bridge
# Spans are opened and closed by events of one agent call, which all happen in one thread (the Foundry and direct
# paths are synchronous), so the bookkeeping is per thread. A span left open by an exception (e.g. the HTTP call
# raised before update()) is closed by the next agent-level event or when the run ends.
_STALE_S = 300  # flags (repair, blocked, fallback start) older than this belong to an earlier call
_handles = itertools.count(-1, -1)
_state = threading.local()
_TOOL_IN_TITLE = re.compile(r"called ([\w.-]+)\(")
_EXERCISE_THREAD = re.compile(r"^(?:exercise|rollback)-(.+)$")
_RULES_RESULT = re.compile(r"^Rules-based (\w+) produced")


class _Open:
    """An open span: role is 'agent', 'tool', 'check' or 'step'."""
    __slots__ = ("span", "role", "token", "answered", "children")

    def __init__(self, span: Any, role: str, token: Any = None):
        self.span, self.role, self.token, self.answered, self.children = span, role, token, False, []


def _st() -> threading.local:
    if not hasattr(_state, "open"):
        _state.open = {}  # (run id, handle) -> _Open
        _state.agent = None  # the invoke_agent span of the call in progress
        _state.model = ""
        _state.last_agent = ""
        _state.flags = {}  # name -> time.monotonic() it was set
    return _state


def _safe(fn: Callable, *args, default: Any = None) -> Any:
    """Telemetry must never break or slow the traced code: any error is logged at debug level and ignored."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        log.debug("trace span bridge failed", exc_info=True)
        return default


def _flag(st: threading.local, name: str, consume: bool = False) -> Optional[tuple]:
    """A flag set by an earlier event of this call, as (monotonic time, ...); None if unset or stale."""
    t = st.flags.pop(name, None) if consume else st.flags.get(name)
    return t if t is not None and time.monotonic() - t[0] < _STALE_S else None


def _common(rid: Optional[str]) -> dict:
    attrs = {"dr.run_id": rid} if rid else {}
    m = _EXERCISE_THREAD.match(threading.current_thread().name or "")
    if m:
        attrs["dr.exercise_id"] = m.group(1)
    return attrs


def _ctx(span: Any) -> Any:
    from opentelemetry import trace as ot
    return ot.set_span_in_context(span)


def _error(span: Any, message: str) -> None:
    from opentelemetry.trace import Status, StatusCode
    span.set_status(Status(StatusCode.ERROR, (message or "failed")[:300]))


def _kind(name: str) -> Any:
    from opentelemetry.trace import SpanKind
    return getattr(SpanKind, name)


def _root_span(run_id: str) -> Any:
    tracer = telemetry.tracer()
    if tracer is None:
        return nullcontext()
    try:
        return tracer.start_as_current_span("dr.run", attributes=_common(run_id))
    except Exception:  # noqa: BLE001
        log.debug("dr.run span failed", exc_info=True)
        return nullcontext()


def current_agent_model() -> str:
    """Model of the agent call in progress in this thread (names the httpx "chat <model>" spans)."""
    st = _st()
    return st.model if st.agent is not None else ""


def _detach(o: _Open) -> None:
    if o.token is not None:
        from opentelemetry import context
        token, o.token = o.token, None
        context.detach(token)


def _end(st: threading.local, o: _Open) -> None:
    for c in o.children:
        if c.span.is_recording():
            _error(c.span, "not completed")
            c.span.end()
    _detach(o)
    o.span.end()
    for k in [k for k, v in st.open.items() if v is o or v in o.children]:
        del st.open[k]


def _finish_agent(st: threading.local, outcome: str, message: str = "") -> None:
    """End the open invoke_agent span with its outcome; accepted is final (dr.source=llm)."""
    o, st.agent = st.agent, None
    if o is None:
        return
    o.span.set_attribute("dr.outcome", outcome)
    if outcome == "accepted":
        o.span.set_attribute("dr.source", "llm")
        if _flag(st, "blocked", consume=True):
            o.span.set_attribute("dr.blocked", True)
    elif outcome in ("failed", "rejected"):
        _error(o.span, message or outcome)
    _end(st, o)


def _close_all(reason: str) -> None:
    if not telemetry.enabled():
        return
    st = _st()
    if st.agent is not None:
        _finish_agent(st, "failed" if not st.agent.answered else "rejected", reason)
    for o in list(st.open.values()):
        if o.span.is_recording():
            _error(o.span, reason)
            _end(st, o)
    st.open.clear()


def _agent_attrs(span: Any, data: dict, cost: Optional[float]) -> None:
    agent, version = str(data.get("agent") or ""), str(data.get("version") or "")
    model = str(data.get("model") or "")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if agent:
        span.update_name(f"invoke_agent {agent}")
        span.set_attribute("gen_ai.agent.name", agent)
        span.set_attribute("gen_ai.agent.id", f"{agent}:{version}" if version else agent)
    if version:
        span.set_attribute("gen_ai.agent.version", version)
    if model:
        span.set_attribute("gen_ai.response.model", model)
    if data.get("response_id"):
        span.set_attribute("gen_ai.response.id", str(data["response_id"]))
    if data.get("via"):
        span.set_attribute("dr.via", str(data["via"]))
    span.set_attribute("gen_ai.usage.input_tokens", int(usage.get("input_tokens") or 0))
    span.set_attribute("gen_ai.usage.output_tokens", int(usage.get("output_tokens") or 0))
    span.set_attribute("gen_ai.usage.cache_read.input_tokens", int(usage.get("cached_tokens") or 0))
    span.set_attribute("dr.usage.reasoning_tokens", int(usage.get("reasoning_tokens") or 0))
    span.set_attribute("dr.rounds", int(data.get("rounds") or usage.get("rounds") or 0))
    span.set_attribute("dr.cost_usd", float(cost or 0.0))


def _register(st: threading.local, rid: Optional[str], idx: Optional[int], o: _Open) -> int:
    handle = idx if idx is not None else next(_handles)
    st.open[(rid, handle)] = o
    return handle


def _span_emit(rid: Optional[str], idx: Optional[int], kind: str, title: str, detail: str, status: str,
               data: Any) -> Optional[int]:
    tracer, st = telemetry.tracer(), _st()
    data = data if isinstance(data, dict) else {}
    if status != "running":
        _span_event(st, rid, kind, title, detail, status, data)
        return idx
    if kind == "agent":  # a new model attempt: whatever was left open belongs to the previous one
        if st.agent is not None:
            _finish_agent(st, "failed", "superseded by a new attempt")
        st.flags.pop("fallback", None)  # a model attempt follows it, so it was not the rules fallback
        model = str(data.get("model") or "")
        if not model:
            m = re.search(r"deployment (\S+)", title)
            model = m.group(1) if m else ""
        agent = str(data.get("agent") or "")
        attrs = {**_common(rid), "gen_ai.operation.name": "invoke_agent", "gen_ai.provider.name": "azure.ai.openai"}
        if agent:
            attrs["gen_ai.agent.name"] = agent
        if model:
            attrs["gen_ai.request.model"] = model
        if data.get("version"):
            attrs["gen_ai.agent.version"] = str(data["version"])
        if _flag(st, "repair", consume=True):
            attrs["dr.repair"] = True
        span = tracer.start_span(f"invoke_agent {agent}".strip(), kind=_kind("CLIENT"), attributes=attrs)
        from opentelemetry import context
        o = _Open(span, "agent", token=context.attach(_ctx(span)))  # the model HTTP rounds nest under it
        st.agent, st.model = o, model
        if agent:
            st.last_agent = agent
        return _register(st, rid, idx, o)
    parent = st.agent
    if kind == "check" and parent is not None and parent.answered:
        name = parent.span.name.replace("invoke_agent", "validate", 1)
        span = tracer.start_span(name, context=_ctx(parent.span), attributes={**_common(rid), "dr.title": title})
        o = _Open(span, "check")
    elif kind == "tool" and parent is not None and not parent.answered:
        m = _TOOL_IN_TITLE.search(title)
        tool = str(data.get("tool") or (m.group(1) if m else title))[:100]
        span = tracer.start_span(f"execute_tool {tool}", context=_ctx(parent.span), kind=_kind("INTERNAL"),
                                 attributes={**_common(rid), "gen_ai.operation.name": "execute_tool",
                                             "gen_ai.tool.name": tool, "gen_ai.tool.type": "function"})
        o = _Open(span, "tool")
    else:  # an Azure read or other code step (trace.step)
        span = tracer.start_span(f"dr.{kind}", context=_ctx(parent.span) if parent is not None else None,
                                 attributes={**_common(rid), "dr.title": title[:300]})
        o = _Open(span, "step")
    if parent is not None:
        parent.children.append(o)
    return _register(st, rid, idx, o)


def _span_update(rid: Optional[str], idx: int, status: str, detail: str, data: Any, ms: Optional[int],
                 cost: Optional[float]) -> None:
    st = _st()
    o = st.open.pop((rid, idx), None)
    if o is None or not o.span.is_recording():
        return
    span, failed = o.span, status == "fail"
    if o.role == "agent":
        if isinstance(data, dict):
            _agent_attrs(span, data, cost)
            if data.get("agent"):
                st.last_agent = str(data["agent"])
            if data.get("blocked"):
                st.flags["blocked"] = (time.monotonic(),)
                span.set_attribute("dr.blocked", True)
        _detach(o)
        if failed or st.agent is not o:
            if st.agent is o:
                _finish_agent(st, "failed", detail)
            return
        o.answered = True  # stays open until its answer has been checked (the 'check' event) or rejected
        return
    if ms is not None:
        span.set_attribute("dr.ms", int(ms))
    span.set_attribute("dr.status", status)
    if failed:
        _error(span, detail)
    if o.role == "check":
        span.end()
        if st.agent is not None and o in st.agent.children:
            st.agent.span.set_attribute("dr.validation", "fail" if failed else "ok")
            _finish_agent(st, "rejected" if failed else "accepted", detail)
        return
    span.end()


def _span_event(st: threading.local, rid: Optional[str], kind: str, title: str, detail: str, status: str,
                data: dict) -> None:
    """A point-in-time event: a span event, plus the bookkeeping for repair, blocked and the rules fallback."""
    if kind == "blocked" or data.get("blocked"):
        st.flags["blocked"] = (time.monotonic(),)
        if st.agent is not None:
            st.agent.span.set_attribute("dr.blocked", True)
    if kind in ("fallback", "repair", "blocked") and st.agent is not None:
        # the attempt in progress ended without a checked answer (it raised, or its reply did not parse)
        _finish_agent(st, "rejected" if st.agent.answered else "failed", title)
    if kind == "repair":
        st.flags["repair"] = (time.monotonic(),)
    if kind == "fallback":
        st.flags["fallback"] = (time.monotonic(), time.time_ns())
    m = _RULES_RESULT.match(title) if kind == "result" else None
    if m:
        _fallback_span(st, rid, m.group(1))
        return
    from opentelemetry import trace as ot
    target = st.agent.span if st.agent is not None else ot.get_current_span()
    if target.is_recording():
        target.add_event(f"dr.{kind}", {"dr.title": title[:300], "dr.detail": (detail or "")[:300], "dr.status": status})


def _agent_for_schema(schema: str, st: threading.local) -> str:
    try:
        from dr_agent.foundry import AGENTS, agent_prefix
        for name, (schema_name, _) in AGENTS.items():
            if schema_name == schema:
                return agent_prefix() + name
    except Exception:  # noqa: BLE001
        pass
    return st.last_agent or schema


def _fallback_span(st: threading.local, rid: Optional[str], schema: str) -> None:
    """The deterministic fallback answered: the call's final invoke_agent span (dr.via=fallback, no tokens)."""
    agent = _agent_for_schema(schema, st)
    started = _flag(st, "fallback", consume=True)
    attrs = {**_common(rid), "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent,
             "gen_ai.agent.id": agent, "dr.via": "fallback", "dr.source": "fallback", "dr.outcome": "fallback",
             "gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0, "dr.cost_usd": 0.0, "dr.rounds": 0}
    if _flag(st, "blocked", consume=True):
        attrs["dr.blocked"] = True
    st.flags.pop("repair", None)
    span = telemetry.tracer().start_span(f"invoke_agent {agent}", kind=_kind("INTERNAL"), attributes=attrs,
                                         start_time=started[1] if started else None)
    span.end()
