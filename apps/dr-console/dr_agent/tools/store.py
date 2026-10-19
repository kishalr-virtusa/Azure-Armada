"""Storage operations for Tables and Blob."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import threading

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.data.tables import TableServiceClient, TableClient
from azure.storage.blob import BlobServiceClient, ContainerClient

from dr_agent.config import config

logger = logging.getLogger(__name__)

# Lazy-initialized clients
_table_service: TableServiceClient | None = None
_blob_service: BlobServiceClient | None = None


def _get_table_service() -> TableServiceClient:
    """Get or create the Table Service client."""
    global _table_service
    if _table_service is None:
        credential = DefaultAzureCredential()
        _table_service = TableServiceClient(
            endpoint=config.storage_table_endpoint,
            credential=credential,
        )
    return _table_service


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


def get_table_client(table_name: str) -> TableClient:
    """Get a table client for the specified table."""
    return _get_table_service().get_table_client(table_name)


def get_blob_container(container_name: str = "reports") -> ContainerClient:
    """Get a blob container client."""
    return _get_blob_service().get_container_client(container_name)


# ============== Exercises Table ==============

def get_exercise(exercise_id: str) -> dict[str, Any] | None:
    """Get an exercise by ID."""
    try:
        client = get_table_client("exercises")
        entity = client.get_entity(partition_key=exercise_id, row_key="meta")
        return dict(entity)
    except Exception:
        return None


def upsert_exercise(exercise_id: str, data: dict[str, Any]) -> None:
    """Create or update an exercise."""
    client = get_table_client("exercises")
    entity = {
        "PartitionKey": exercise_id,
        "RowKey": "meta",
        **data,
    }
    client.upsert_entity(entity)


def list_exercises(limit: int = 50) -> list[dict[str, Any]]:
    """List recent exercises."""
    client = get_table_client("exercises")
    entities = client.query_entities(
        query_filter="RowKey eq 'meta'",
        select=["PartitionKey", "status", "t0", "rto_s", "rpo_s"],
    )
    results = []
    for e in entities:
        results.append(dict(e))
        if len(results) >= limit:
            break
    return sorted(results, key=lambda x: x.get("t0") or "", reverse=True)  # selected-but-missing props come back as None


# ============== Black box conversations ==============
# In the exercises table, but never with RowKey 'meta', so drill listings do not see them:
#   PartitionKey bbconv-<id>, RowKey 'bbconv'       title, created_at, updated_at, turns, by
#   PartitionKey bbconv-<id>, RowKey 'turn-000001'  question, reply_json, trace_json, at

def add_conversation_turn(conv_id: str, question: str, reply: dict[str, Any], trace_json: str | None,
                          by: str = "") -> int:
    client = get_table_client("exercises")
    pk = f"bbconv-{conv_id}"
    now = datetime.now(timezone.utc).isoformat()
    try:
        head = dict(client.get_entity(partition_key=pk, row_key="bbconv"))
    except Exception:  # noqa: BLE001 - a new conversation
        head = {"PartitionKey": pk, "RowKey": "bbconv", "title": question[:120], "created_at": now, "turns": 0, "by": by}
    n = int(head.get("turns") or 0) + 1
    client.upsert_entity({"PartitionKey": pk, "RowKey": f"turn-{n:06d}", "question": question, "at": now,
                          "reply_json": json.dumps(reply, default=str)[:30000],
                          "trace_json": trace_json if trace_json and len(trace_json) <= 30000 else None})
    client.upsert_entity({**head, "turns": n, "updated_at": now})
    return n


def list_conversations(limit: int = 30) -> list[dict[str, Any]]:
    client = get_table_client("exercises")
    rows = [dict(e) for e in client.query_entities(query_filter="RowKey eq 'bbconv'",
                                                   select=["PartitionKey", "title", "created_at", "updated_at", "turns", "by"])]
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return [{"id": r["PartitionKey"][len("bbconv-"):], "title": r.get("title") or "", "turns": int(r.get("turns") or 0),
             "created_at": r.get("created_at"), "updated_at": r.get("updated_at"), "by": r.get("by") or ""}
            for r in rows[:limit]]


def get_conversation(conv_id: str) -> dict[str, Any] | None:
    client = get_table_client("exercises")
    pk = f"bbconv-{conv_id}"
    rows = sorted((dict(e) for e in client.query_entities(query_filter="PartitionKey eq @pk", parameters={"pk": pk})),
                  key=lambda r: r["RowKey"])
    head = next((r for r in rows if r["RowKey"] == "bbconv"), None)
    if not head:
        return None
    turns = [{"question": r.get("question") or "", "at": r.get("at"),
              "reply": json.loads(r.get("reply_json") or "{}"),
              "trace": json.loads(r["trace_json"]) if r.get("trace_json") else None}
             for r in rows if r["RowKey"].startswith("turn-")]
    return {"id": conv_id, "title": head.get("title") or "", "updated_at": head.get("updated_at"), "turns": turns}


# ============== Events Table ==============

def add_event(
    exercise_id: str,
    kind: str,
    message: str,
    step_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> int:
    """Add an event to the exercise timeline. Returns the sequence number."""
    client = get_table_client("events")
    ts = datetime.now(timezone.utc).isoformat()  # stamped before the sequence lookup, so step times stay exact
    # The engine thread and web requests write events concurrently: serialise per process (one worker) and, if a
    # row key is taken anyway, move to the next number instead of losing the event.
    with _EVENT_LOCK:
        seq = len(list(client.query_entities(query_filter="PartitionKey eq @pk", parameters={"pk": exercise_id},
                                             select=["RowKey"]))) + 1
        for _ in range(5):
            try:
                client.create_entity({"PartitionKey": exercise_id, "RowKey": str(seq).zfill(6), "ts": ts,
                                      "step_id": step_id, "kind": kind, "message": message,
                                      "data_json": json.dumps(data) if data else None})
                return seq
            except ResourceExistsError:
                seq += 1
    raise RuntimeError(f"could not write event for {exercise_id}")


_EVENT_LOCK = threading.Lock()


def get_events(exercise_id: str, after: int = 0) -> list[dict[str, Any]]:
    """Get events for an exercise after a sequence number."""
    client = get_table_client("events")
    entities = client.query_entities(
        query_filter="PartitionKey eq @pk and RowKey gt @after",
        parameters={"pk": exercise_id, "after": str(int(after)).zfill(6)},
    )
    results = []
    for e in entities:
        event = dict(e)
        if event.get("data_json"):
            event["data"] = json.loads(event["data_json"])
        results.append(event)
    return sorted(results, key=lambda x: x["RowKey"])


# ============== Notifications Table ==============

def add_notification(
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
    exercise_id: str | None = None,
) -> str:
    """Add a notification. Returns the notification ID."""
    client = get_table_client("notifications")
    
    ts = datetime.now(timezone.utc)
    notif_id = ts.strftime("%Y%m%d%H%M%S%f")
    
    entity = {
        "PartitionKey": "global",
        "RowKey": notif_id,
        "ts": ts.isoformat(),
        "kind": kind,
        "title": title,
        "body": body,
        "link": link,
        "exercise_id": exercise_id,
        "read": False,
    }
    client.create_entity(entity)
    return notif_id


def get_notifications(after: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Get notifications, optionally after a certain ID."""
    client = get_table_client("notifications")
    
    if after:
        entities = client.query_entities(query_filter="PartitionKey eq 'global' and RowKey gt @after",
                                         parameters={"after": str(after)})
    else:
        entities = client.query_entities(query_filter="PartitionKey eq 'global'")
    results = []
    for e in entities:
        results.append(dict(e))
        if len(results) >= limit:
            break
    return sorted(results, key=lambda x: x["RowKey"], reverse=True)


def mark_notification_read(notif_id: str) -> None:
    """Mark a notification as read."""
    client = get_table_client("notifications")
    try:
        entity = client.get_entity(partition_key="global", row_key=notif_id)
        entity["read"] = True
        client.update_entity(entity)
    except Exception:
        pass


# ============== Heartbeats Table ==============

def record_heartbeat(seq: int, client_ts: datetime, acked: bool, error: str | None = None) -> None:
    """Record a heartbeat result."""
    client = get_table_client("heartbeats")
    entity = {
        "PartitionKey": "hb",
        "RowKey": str(seq).zfill(12),
        "client_ts": client_ts.isoformat(),
        "acked": acked,
        "error": error,
    }
    client.upsert_entity(entity)


def get_heartbeats_since(min_seq: int) -> list[dict[str, Any]]:
    """Heartbeats with seq >= min_seq (seqs are epoch milliseconds, so this is 'written since')."""
    client = get_table_client("heartbeats")
    return list(client.query_entities(query_filter="PartitionKey eq 'hb' and RowKey ge @min",
                                      parameters={"min": str(int(min_seq)).zfill(12)}))


def get_heartbeats(limit: int = 100) -> list[dict[str, Any]]:
    """Get recent heartbeats."""
    client = get_table_client("heartbeats")
    entities = client.query_entities(
        query_filter="PartitionKey eq 'hb'",
    )
    results = list(entities)
    return sorted(results, key=lambda x: x["RowKey"], reverse=True)[:limit]


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
