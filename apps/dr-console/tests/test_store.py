"""The Cosmos DB store keeps the record shapes and write semantics the console was built on."""
import time
from datetime import datetime, timezone

from azure.cosmos.exceptions import CosmosResourceExistsError

from dr_agent.tools import store

SYSTEM = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def test_partial_upserts_merge_instead_of_replacing(cosmos):
    store.upsert_exercise("ex-20261019-105207", {"status": "running", "t0": "2026-10-19T10:53:03+00:00"})
    store.upsert_exercise("ex-20261019-105207", {"rto_s": 27.74, "current_step": None})   # None: leave unchanged
    store.upsert_exercise("ex-20261019-105207", {"status": "completed"})
    ex = store.get_exercise("ex-20261019-105207")
    assert ex == {"PartitionKey": "ex-20261019-105207", "RowKey": "meta", "status": "completed",
                  "t0": "2026-10-19T10:53:03+00:00", "rto_s": 27.74}


def test_merge_with_more_than_ten_fields_and_datetimes(cosmos):
    data = {f"f{i}": i for i in range(23)} | {"t_failover": datetime(2026, 10, 19, 10, 53, 4, tzinfo=timezone.utc)}
    store.upsert_exercise("ex-1", {"status": "approved"})
    store.upsert_exercise("ex-1", data)   # 24 fields: three patch requests of at most 10 operations
    ex = store.get_exercise("ex-1")
    assert ex["status"] == "approved" and ex["f22"] == 22 and ex["t_failover"] == "2026-10-19T10:53:04+00:00"


def test_upsert_of_only_none_values_still_creates_the_record(cosmos):
    store.upsert_exercise("autopilot", {"proposal_json": None})
    assert store.get_exercise("autopilot") == {"PartitionKey": "autopilot", "RowKey": "meta"}


def test_records_never_expose_cosmos_system_fields(cosmos):
    store.upsert_exercise("ex-1", {"status": "running"})
    store.add_event("ex-1", "info", "started", data={"a": 1})
    nid = store.add_notification("info", "t", "b")
    store.record_heartbeat(1760870000000, datetime.now(timezone.utc), True)
    rows = [store.get_exercise("ex-1"), *store.get_events("ex-1"), *store.get_notifications(),
            *store.get_heartbeats(), *store.list_exercises()]
    assert all(SYSTEM.isdisjoint(r) and "id" not in r and "pk" not in r for r in rows)
    assert store.get_notifications()[0]["RowKey"] == nid


def test_events_are_numbered_in_order_and_skip_a_taken_number(cosmos, monkeypatch):
    assert [store.add_event("ex-1", "info", f"e{i}") for i in range(3)] == [1, 2, 3]
    # another writer took 000004 between our count and our write: the event moves to the next number
    real = store._query
    monkeypatch.setattr(store, "_query", lambda c, q, *a, **k: [3] if "COUNT" in q else real(c, q, *a, **k))
    cosmos["events"].create_item({"id": "000004", "pk": "ex-1", "kind": "info", "message": "racer"})
    assert store.add_event("ex-1", "step", "mine", step_id="s1", data={"x": 1}) == 5
    monkeypatch.setattr(store, "_query", real)
    ev = store.get_events("ex-1", after=3)
    assert [(e["RowKey"], e["message"]) for e in ev] == [("000004", "racer"), ("000005", "mine")]
    assert ev[1]["data"] == {"x": 1} and ev[1]["step_id"] == "s1" and ev[1]["PartitionKey"] == "ex-1"
    assert store.get_events("ex-2") == []


def test_list_exercises_has_table_shape_and_sees_only_exercises(cosmos):
    store.upsert_exercise("ex-20261019-105207", {"status": "completed", "t0": "2026-10-19T10:53", "rto_s": 27.7})
    store.upsert_exercise("ex-20261019-095400", {"status": "completed", "t0": "2026-10-19T09:54"})
    store.upsert_exercise("autopilot", {"settings_json": "{}"})
    store.add_conversation_turn("aaaaaaaaaaaa", "q", {"answer": "a"}, None)   # own container
    rows = store.list_exercises(1000)
    assert [r["PartitionKey"] for r in rows] == ["ex-20261019-105207", "ex-20261019-095400", "autopilot"]
    assert rows[1] == {"PartitionKey": "ex-20261019-095400", "RowKey": "meta", "status": "completed",
                       "t0": "2026-10-19T09:54", "rto_s": None, "rpo_s": None}


def test_notifications_newest_first_after_and_read(cosmos):
    ids = []
    for i in range(3):
        ids.append(store.add_notification("info", f"n{i}", "b"))
        time.sleep(0.002)   # ids are microsecond timestamps
    assert [n["title"] for n in store.get_notifications()] == ["n2", "n1", "n0"]
    assert [n["title"] for n in store.get_notifications(after=ids[0])] == ["n2", "n1"]
    assert len(store.get_notifications(limit=2)) == 2
    store.mark_notification_read(ids[1])
    store.mark_notification_read("missing")              # never raises
    store.update_notification(ids[1], {"email_sent": True, "email_channel": "acs"})
    n1 = next(n for n in store.get_notifications() if n["RowKey"] == ids[1])
    assert n1["read"] is True and n1["email_sent"] is True and n1["title"] == "n1"


def test_heartbeats_since(cosmos):
    t = datetime(2026, 10, 19, 10, 53, tzinfo=timezone.utc)
    for seq in (1760870000000, 1760870001000, 1760870002000):
        store.record_heartbeat(seq, t, seq != 1760870001000, None if seq != 1760870001000 else "timeout")
    rows = store.get_heartbeats_since(1760870001000)
    assert [int(r["RowKey"]) for r in rows] == [1760870001000, 1760870002000]
    assert rows[0]["acked"] is False and rows[0]["error"] == "timeout" and "error" not in rows[1]
    assert int(store.get_heartbeats(1)[0]["RowKey"]) == 1760870002000


def test_fake_rejects_duplicate_create(cosmos):
    store.add_notification("info", "t", "b")
    c = cosmos["notifications"]
    doc = next(iter(c.docs.values()))
    try:
        c.create_item({"id": doc["id"], "pk": "global"})
        raise AssertionError("expected a conflict")
    except CosmosResourceExistsError:
        pass


def test_migration_maps_table_rows_to_the_new_documents():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("migrate", Path(__file__).parents[1] / "scripts/migrate_tables_to_cosmos.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    t = datetime(2026, 10, 19, 10, 53, tzinfo=timezone.utc)
    assert m.convert("exercises", {"PartitionKey": "ex-1", "RowKey": "meta", "status": "completed", "t0": t, "x": None}) == \
        ("exercises", {"id": "ex-1", "pk": "ex-1", "status": "completed", "t0": "2026-10-19T10:53:00+00:00"})
    assert m.convert("exercises", {"PartitionKey": "bbconv-abc", "RowKey": "turn-000001", "question": "q"}) == \
        ("conversations", {"id": "turn-000001", "pk": "abc", "question": "q"})
    assert m.convert("exercises", {"PartitionKey": "ex-1", "RowKey": "other"}) is None
    assert m.convert("events", {"PartitionKey": "ex-1", "RowKey": "000001", "kind": "info"}) == \
        ("events", {"id": "000001", "pk": "ex-1", "kind": "info"})
