"""Storage operations for Tables and Blob."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

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
    return sorted(results, key=lambda x: x.get("t0", ""), reverse=True)


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
    
    # Get next sequence number
    events = list(client.query_entities(
        query_filter=f"PartitionKey eq '{exercise_id}'",
        select=["RowKey"],
    ))
    seq = len(events) + 1
    
    entity = {
        "PartitionKey": exercise_id,
        "RowKey": str(seq).zfill(6),
        "ts": datetime.now(timezone.utc).isoformat(),
        "step_id": step_id,
        "kind": kind,
        "message": message,
        "data_json": json.dumps(data) if data else None,
    }
    client.create_entity(entity)
    return seq


def get_events(exercise_id: str, after: int = 0) -> list[dict[str, Any]]:
    """Get events for an exercise after a sequence number."""
    client = get_table_client("events")
    entities = client.query_entities(
        query_filter=f"PartitionKey eq '{exercise_id}' and RowKey gt '{str(after).zfill(6)}'",
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
        query = f"PartitionKey eq 'global' and RowKey gt '{after}'"
    else:
        query = "PartitionKey eq 'global'"
    
    entities = client.query_entities(query_filter=query)
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
