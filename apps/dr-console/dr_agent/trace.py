"""Live activity trace of one agent run, shown in the console while the agent works.

Only real events are recorded: the Azure reads that gather the facts, the Foundry agent call (agent, version,
model, latency, tokens), the code checks on its answer and any fallback. The browser polls
GET /api/trace/{run_id} while its request is running. Traces live in memory (single console instance) and
only the most recent ones are kept.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

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
        return fn(*args, **kwargs)
    finally:
        _current.reset(token)
        with _lock:
            if run_id in _traces:
                _traces[run_id]["done"] = True


def emit(kind: str, title: str, detail: str = "", status: str = "ok", data: Any = None) -> Optional[int]:
    """Record one event; returns its index so a 'running' event can be completed later with update()."""
    rid = _current.get()
    if not rid:
        return None
    with _lock:
        tr = _traces.get(rid)
        if tr is None:
            return None
        ev = {"t_ms": int((time.monotonic() - tr["t0"]) * 1000), "kind": kind, "title": title,
              "detail": detail, "status": status, "data": _small(data) if data is not None else None}
        tr["events"].append(ev)
        return len(tr["events"]) - 1


def update(idx: Optional[int], status: str, detail: str = "", data: Any = None, ms: Optional[int] = None) -> None:
    rid = _current.get()
    if idx is None or not rid:
        return
    with _lock:
        tr = _traces.get(rid)
        if tr is None or idx >= len(tr["events"]):
            return
        ev = tr["events"][idx]
        ev["status"] = status
        if detail:
            ev["detail"] = detail
        if data is not None:
            ev["data"] = _small(data)
        if ms is not None:
            ev["ms"] = ms


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
