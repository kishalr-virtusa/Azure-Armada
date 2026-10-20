"""The console's records in Azure Cosmos DB (NoSQL API), reports in Blob storage.

Cosmos DB database COSMOS_DATABASE (default 'dra'), shared throughput, Entra ID auth only (the console's managed
identity; account keys are disabled). Containers are created by infra (Bicep), all partitioned on /pk:
  exercises      id = pk = exercise id ('ex-YYYYMMDD-HHMMSS', also 'autopilot' and other state rows)
  events         pk = exercise / feed id, id = sequence '000001'
  notifications  pk = 'global', id = 'YYYYmmddHHMMSSffffff'
  heartbeats     pk = 'hb', id = seq zero-padded to 12 (epoch milliseconds)
  conversations  pk = Black box conversation id, id 'bbconv' (header) or 'turn-000001'

Callers still see the Table-shaped dicts they were written against: PartitionKey / RowKey plus the fields
(exercises: RowKey 'meta'). Cosmos system fields are never returned. As with the Table merge-upsert this replaces,
a None value means "leave unchanged" on write.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import threading

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient

from dr_agent.config import config

logger = logging.getLogger(__name__)

# Reads go to the primary (Central India, nearest the console in South India), then the replica
PREFERRED_REGIONS = ["Central India", "Southeast Asia"]
_SYSTEM = {"_rid", "_self", "_etag", "_attachments", "_ts", "_lsn"}
_KEYS = {"id", "pk", "PartitionKey", "RowKey"}
PATCH_MAX_OPS = 10  # Cosmos DB limit per patch request

# Lazy-initialized clients
_cosmos_db = None
_blob_service: BlobServiceClient | None = None


def _get_database():
    """Get or create the Cosmos DB database client."""
    global _cosmos_db
    if _cosmos_db is None:
        client = CosmosClient(config.cosmos_endpoint, credential=DefaultAzureCredential(),
                              preferred_locations=PREFERRED_REGIONS, consistency_level="Session")
        _cosmos_db = client.get_database_client(config.cosmos_database)
    return _cosmos_db


def _container(name: str):
    """A Cosmos DB container (exercises, events, notifications, heartbeats, conversations)."""
    return _get_database().get_container_client(name)


def _get_blob_service() -> BlobServiceClient:
    """Get or create the Blob Service client."""
    global _blob_service
    if _blob_service is None:
        credential = DefaultAzureCredential()
        _blob_service = BlobServiceClient(
            account_url=config.storage_blob_endpoint,
            credential=credential,
        )
    return _blob_service


def get_blob_container(container_name: str = "reports") -> ContainerClient:
    """Get a blob container client."""
    return _get_blob_service().get_container_client(container_name)


# ============== Document helpers ==============

def _plain(v: Any) -> Any:
    """JSON-safe value (datetimes as ISO strings, as the callers already write most of them)."""
    return v.isoformat() if isinstance(v, datetime) else v


def _fields(data: dict[str, Any]) -> dict[str, Any]:
    return {k: _plain(v) for k, v in data.items() if v is not None and k not in _KEYS}


def _entity(doc: dict[str, Any], row_key: str | None = None) -> dict[str, Any]:
    """A stored document in the Table-entity shape the callers use."""
    d = {k: v for k, v in doc.items() if k not in _SYSTEM}
    pk, rid = d.pop("pk", None), d.pop("id", None)
    return {"PartitionKey": pk, "RowKey": row_key or rid, **d}


def _query(container: str, query: str, params: dict[str, Any] | None = None,
           pk: str | None = None) -> list[dict[str, Any]]:
    parameters = [{"name": k, "value": v} for k, v in (params or {}).items()]
    kw: dict[str, Any] = {"partition_key": pk} if pk is not None else {"enable_cross_partition_query": True}
    return list(_container(container).query_items(query=query, parameters=parameters, **kw))


def _merge(container: str, pk: str, item_id: str, data: dict[str, Any]) -> None:
    """Merge fields into a document, creating it if needed. Patch 'set' only touches the named fields, so the
    engine thread and web requests updating different fields of the same record never lose each other's writes."""
    c = _container(container)
    fields = _fields(data)
    ops = [{"op": "set", "path": "/" + k.replace("~", "~0").replace("/", "~1"), "value": v} for k, v in fields.items()]
    for _ in range(3):
        try:
            if not ops:
                c.read_item(item_id, partition_key=pk)
            for i in range(0, len(ops), PATCH_MAX_OPS):
                c.patch_item(item_id, partition_key=pk, patch_operations=ops[i:i + PATCH_MAX_OPS])
            return
        except CosmosResourceNotFoundError:
            try:
                c.create_item({"id": item_id, "pk": pk, **fields})
                return
            except CosmosResourceExistsError:
                continue  # created by another writer in between: patch it
    raise RuntimeError(f"could not write {container}/{item_id}")


# ============== Exercises ==============

def get_exercise(exercise_id: str) -> dict[str, Any] | None:
    """Get an exercise by ID."""
    try:
        return _entity(_container("exercises").read_item(exercise_id, partition_key=exercise_id), row_key="meta")
    except Exception:
        return None


def upsert_exercise(exercise_id: str, data: dict[str, Any]) -> None:
    """Create or update an exercise (merge: fields not named here are kept)."""
    _merge("exercises", exercise_id, exercise_id, data)


_LIST_FIELDS = ("status", "t0", "rto_s", "rpo_s")


def list_exercises(limit: int = 50, fields: tuple[str, ...] = _LIST_FIELDS) -> list[dict[str, Any]]:
    """List recent exercises with only the named fields: one query, never a read per drill."""
    proj = ", ".join(f'c["{f}"]' for f in fields)  # bracket form: field names may be reserved words
    rows = _query("exercises", f"SELECT TOP {int(limit)} c.pk, {proj} FROM c")
    results = [{"PartitionKey": r["pk"], "RowKey": "meta", **{f: r.get(f) for f in fields}} for r in rows]
    return sorted(results, key=lambda x: x.get("t0") or "", reverse=True)  # selected-but-missing fields are None


# ============== Black box conversations ==============
# Container 'conversations', partition = conversation id:
#   id 'bbconv'       title, created_at, updated_at, turns, by
#   id 'turn-000001'  question, reply_json, trace_json, at

def add_conversation_turn(conv_id: str, question: str, reply: dict[str, Any], trace_json: str | None,
                          by: str = "") -> int:
    c = _container("conversations")
    now = datetime.now(timezone.utc).isoformat()
    try:
        head = {k: v for k, v in c.read_item("bbconv", partition_key=conv_id).items() if k not in _SYSTEM}
    except CosmosResourceNotFoundError:  # a new conversation
        head = {"id": "bbconv", "pk": conv_id, "title": question[:120], "created_at": now, "turns": 0, "by": by}
    n = int(head.get("turns") or 0) + 1
    c.upsert_item({"id": f"turn-{n:06d}", "pk": conv_id, "question": question, "at": now,
                   "reply_json": json.dumps(reply, default=str)[:30000],
                   "trace_json": trace_json if trace_json and len(trace_json) <= 30000 else None})
    c.upsert_item({**head, "turns": n, "updated_at": now})
    return n


def list_conversations(limit: int = 30) -> list[dict[str, Any]]:
    # c["by"]: BY is a reserved word in the Cosmos DB query language
    rows = _query("conversations", 'SELECT c.pk, c.title, c.created_at, c.updated_at, c.turns, c["by"] FROM c '
                                   "WHERE c.id = 'bbconv'")
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return [{"id": r["pk"], "title": r.get("title") or "", "turns": int(r.get("turns") or 0),
             "created_at": r.get("created_at"), "updated_at": r.get("updated_at"), "by": r.get("by") or ""}
            for r in rows[:limit]]


def get_conversation(conv_id: str) -> dict[str, Any] | None:
    rows = sorted(_query("conversations", "SELECT * FROM c", pk=conv_id), key=lambda r: r["id"])
    head = next((r for r in rows if r["id"] == "bbconv"), None)
    if not head:
        return None
    turns = [{"question": r.get("question") or "", "at": r.get("at"),
              "reply": json.loads(r.get("reply_json") or "{}"),
              "trace": json.loads(r["trace_json"]) if r.get("trace_json") else None}
             for r in rows if r["id"].startswith("turn-")]
    return {"id": conv_id, "title": head.get("title") or "", "updated_at": head.get("updated_at"), "turns": turns}


def delete_conversation(conv_id: str) -> int:
    """Delete a conversation and all its turns. Returns how many documents were removed."""
    c = _container("conversations")
    ids = [r["id"] for r in _query("conversations", "SELECT c.id FROM c", pk=conv_id)]
    for i in sorted(ids, key=lambda i: i == "bbconv"):  # the header last: a half-done delete stays listed
        c.delete_item(i, partition_key=conv_id)
    return len(ids)


# ============== Events ==============

def add_event(
    exercise_id: str,
    kind: str,
    message: str,
    step_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> int:
    """Add an event to the exercise timeline. Returns the sequence number."""
    c = _container("events")
    ts = datetime.now(timezone.utc).isoformat()  # stamped before the sequence lookup, so step times stay exact
    # The engine thread and web requests write events concurrently: serialise per process (one worker) and, if an
    # id is taken anyway, move to the next number instead of losing the event.
    with _EVENT_LOCK:
        seq = (_query("events", "SELECT VALUE COUNT(1) FROM c", pk=exercise_id) or [0])[0] + 1
        for _ in range(5):
            try:
                c.create_item({"id": str(seq).zfill(6), "pk": exercise_id, "ts": ts, "step_id": step_id,
                               "kind": kind, "message": message, "data_json": json.dumps(data) if data else None})
                return seq
            except CosmosResourceExistsError:
                seq += 1
    raise RuntimeError(f"could not write event for {exercise_id}")


_EVENT_LOCK = threading.Lock()


def get_events(exercise_id: str, after: int = 0) -> list[dict[str, Any]]:
    """Get events for an exercise after a sequence number."""
    rows = _query("events", "SELECT * FROM c WHERE c.id > @after", {"@after": str(int(after)).zfill(6)}, pk=exercise_id)
    results = []
    for r in rows:
        event = _entity(r)
        if event.get("data_json"):
            event["data"] = json.loads(event["data_json"])
        results.append(event)
    return sorted(results, key=lambda x: x["RowKey"])


# ============== Notifications ==============

def add_notification(
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
    exercise_id: str | None = None,
) -> str:
    """Add a notification. Returns the notification ID."""
    ts = datetime.now(timezone.utc)
    notif_id = ts.strftime("%Y%m%d%H%M%S%f")
    _container("notifications").create_item({
        "id": notif_id,
        "pk": "global",
        "ts": ts.isoformat(),
        "kind": kind,
        "title": title,
        "body": body,
        "link": link,
        "exercise_id": exercise_id,
        "read": False,
    })
    return notif_id


def get_notifications(after: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The newest notifications (newest first), optionally only those after a certain ID."""
    where, params = ("WHERE c.id > @after ", {"@after": str(after)}) if after else ("", {})
    rows = _query("notifications", f"SELECT TOP {int(limit)} * FROM c {where}ORDER BY c.id DESC", params, pk="global")
    return [_entity(r) for r in rows]


def mark_notification_read(notif_id: str) -> None:
    """Mark a notification as read."""
    try:
        _container("notifications").patch_item(notif_id, partition_key="global",
                                               patch_operations=[{"op": "set", "path": "/read", "value": True}])
    except Exception:
        pass


def update_notification(notif_id: str, fields: dict[str, Any]) -> None:
    """Merge fields into an existing notification (e.g. how its email was delivered)."""
    _merge("notifications", "global", notif_id, fields)


# ============== Heartbeats ==============

def record_heartbeat(seq: int, client_ts: datetime, acked: bool, error: str | None = None) -> None:
    """Record a heartbeat result."""
    _container("heartbeats").upsert_item({"id": str(seq).zfill(12), "pk": "hb",
                                          **_fields({"client_ts": client_ts.isoformat(), "acked": acked, "error": error})})


def get_heartbeats_since(min_seq: int) -> list[dict[str, Any]]:
    """Heartbeats with seq >= min_seq (seqs are epoch milliseconds, so this is 'written since')."""
    rows = _query("heartbeats", "SELECT * FROM c WHERE c.id >= @min", {"@min": str(int(min_seq)).zfill(12)}, pk="hb")
    return [_entity(r) for r in rows]


def get_heartbeats(limit: int = 100) -> list[dict[str, Any]]:
    """Get recent heartbeats."""
    rows = _query("heartbeats", f"SELECT TOP {int(limit)} * FROM c ORDER BY c.id DESC", pk="hb")
    return [_entity(r) for r in rows]


# ============== Blob Operations ==============

def upload_report(exercise_id: str, html_content: str) -> str:
    """Upload an HTML report to blob storage. Returns the blob URL."""
    container = get_blob_container("reports")
    blob_name = f"{exercise_id}/report.html"

    blob_client = container.get_blob_client(blob_name)
    blob_client.upload_blob(
        html_content,
        content_type="text/html",
        overwrite=True,
    )

    return blob_client.url


def get_report(exercise_id: str) -> str | None:
    """Get the HTML report content for an exercise."""
    try:
        container = get_blob_container("reports")
        blob_client = container.get_blob_client(f"{exercise_id}/report.html")
        return blob_client.download_blob().readall().decode("utf-8")
    except Exception:
        return None


# ============== CLI for testing ==============

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Storage operations CLI")
    parser.add_argument("--list-exercises", action="store_true", help="List exercises")
    parser.add_argument("--list-notifications", action="store_true", help="List notifications")
    parser.add_argument("--test-notification", action="store_true", help="Create a test notification")

    args = parser.parse_args()

    if args.list_exercises:
        for ex in list_exercises():
            print(f"{ex.get('PartitionKey')}: {ex.get('status')}")

    if args.list_notifications:
        for n in get_notifications():
            print(f"{n.get('RowKey')}: [{n.get('kind')}] {n.get('title')}")

    if args.test_notification:
        nid = add_notification("info", "Test", "This is a test notification")
        print(f"Created notification: {nid}")
