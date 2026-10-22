"""Deterministic data for the agents' tools during an eval: the model is real, the records and Azure state are frozen.

Tool calls run client-side (dr_agent.foundry runs them in the calling thread), so the eval replaces what the tools
READ, not the tools themselves: the real tool code formats real recorded drills exactly as in production.

- Records (Black box tools, Reporter facts): dr_agent.tools.store reads are served from fixtures/records.json, the
  read-only Cosmos DB snapshot made by evals.snapshot. Nothing is written.
- Live Azure reads (Validator / Readiness tools: probe, traffic_manager, web_app, sql_failover_group,
  app_settings_diff, site_recovery) return the per-case stubs from the dataset.
- Current state and Retail Prices come from fixtures/current_state.json and fixtures/pricing.json.

Per-case data (tool stubs, records overlays such as a planted injection) is thread-local, so cases can run
concurrently without seeing each other's overlays.
"""
from __future__ import annotations

import contextlib
import copy
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_TOOLS = ("probe", "traffic_manager", "web_app", "sql_failover_group", "app_settings_diff", "site_recovery")

_local = threading.local()


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Records:
    """The frozen record set, with an optional per-thread overlay (extra events, field overrides)."""

    def __init__(self, data: dict):
        self.data = data
        self.captured_at = datetime.fromisoformat(data["captured_at"])

    @classmethod
    def load(cls, name: str = "records.json") -> "Records":
        return cls(load_json(name))

    # ---------------------------------------------------------------- views with the thread's overlay applied
    @staticmethod
    def _overlay() -> dict:
        return getattr(_local, "overlay", None) or {}

    def exercise(self, xid: str) -> Optional[dict]:
        row = self.data["exercises"].get(xid)
        if row is None:
            return None
        fields = {k: v for k, v in row.items() if k not in ("id", "pk")}
        fields.update((self._overlay().get("exercises") or {}).get(xid) or {})
        return {"PartitionKey": xid, "RowKey": "meta", **copy.deepcopy(fields)}

    def events(self, xid: str, after: int = 0) -> list[dict]:
        rows = [*self.data["events"].get(xid, []), *((self._overlay().get("events") or {}).get(xid) or [])]
        out = []
        for i, r in enumerate(rows, 1):
            rid = r.get("id") or str(i).zfill(6)
            if int(rid) <= int(after):
                continue
            e = {"PartitionKey": xid, "RowKey": rid, **{k: v for k, v in r.items() if k not in ("id", "pk")}}
            if e.get("data_json"):
                e["data"] = json.loads(e["data_json"])
            out.append(copy.deepcopy(e))
        return sorted(out, key=lambda e: e["RowKey"])

    def list_exercises(self, limit: int = 50, fields: tuple[str, ...] = ("status", "t0", "rto_s", "rpo_s")) -> list[dict]:
        rows = [self.exercise(x) for x in self.data["exercises"]]
        rows = [{"PartitionKey": r["PartitionKey"], "RowKey": "meta", **{f: r.get(f) for f in fields}}
                for r in rows if r]
        return sorted(rows, key=lambda x: x.get("t0") or "", reverse=True)[:limit]

    def drill_ids(self) -> list[str]:
        """Newest first, as service.list_exercises orders them."""
        return sorted((x for x in self.data["exercises"] if x.startswith("ex-")), reverse=True)


@contextlib.contextmanager
def case_context(tool_stubs: Optional[dict] = None, overlay: Optional[dict] = None) -> Iterator[None]:
    """Per-case data for the current thread: live-tool stubs and a records overlay."""
    prev = (getattr(_local, "stubs", None), getattr(_local, "overlay", None))
    _local.stubs, _local.overlay = tool_stubs or {}, overlay or {}
    try:
        yield
    finally:
        _local.stubs, _local.overlay = prev


def _stub(name: str):
    def call(**args: Any) -> Any:
        stubs = getattr(_local, "stubs", None) or {}
        if name not in stubs:
            return {"error": f"{name} is not available in this scenario"}
        v = stubs[name]
        if isinstance(v, dict) and "by_arg" in v:  # e.g. probe: {"by_arg": "target", "values": {"A": {...}}}
            key = str(args.get(v["by_arg"]))
            return copy.deepcopy(v["values"].get(key, {"error": f"no data for {v['by_arg']}={key}"}))
        return copy.deepcopy(v)
    return call


class _FrozenDatetime(datetime):
    """datetime whose now() is the snapshot time, so "today" in the Black box payload matches the records."""
    frozen: datetime = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return cls.frozen if tz is None else cls.frozen.astimezone(tz)


def install(records: Records, patch: Callable[[Any, str, Any], None] = setattr) -> None:
    """Point every read the agents' tools make at the fixtures. Process-wide; evals only. Tests pass
    monkeypatch.setattr as `patch` so everything is restored afterwards."""
    from dr_agent import service
    from dr_agent.agents import blackbox, tools
    from dr_agent.tools import pricing, store

    def _no_write(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("evals never write to the records")

    from dr_agent import knowledge
    # search_knowledge stays live (read-only vector search of the index built from the same records); indexing never
    # runs from an eval
    patch(knowledge, "index_drill_async", lambda *_a, **_k: None)
    patch(knowledge, "index_shared_async", lambda *_a, **_k: None)
    patch(store, "get_exercise", records.exercise)
    patch(store, "get_events", records.events)
    patch(store, "list_exercises", records.list_exercises)
    for name in ("upsert_exercise", "add_event", "add_conversation_turn", "upload_report", "record_heartbeat",
                 "add_notification"):
        patch(store, name, _no_write)
    patch(service, "list_exercises", lambda: [{"PartitionKey": x, "RowKey": "meta"} for x in records.drill_ids()][:50])
    current = load_json("current_state.json")
    patch(service, "live_map", lambda: copy.deepcopy(current["live_map"]))
    prices = load_json("pricing.json")
    patch(pricing, "region_monthly_usd", lambda region="eastasia", timeout_s=8.0: copy.deepcopy(prices))
    live = dict(tools.TOOLS)
    for name in LIVE_TOOLS:
        desc, schema, _ = live[name]
        live[name] = (desc, schema, _stub(name))  # same description and schema: the agent spec is unchanged
    patch(tools, "TOOLS", live)
    frozen = type("FrozenDatetime", (_FrozenDatetime,), {"frozen": records.captured_at})
    patch(blackbox, "datetime", frozen)
