"""Freeze the console's recorded drills from Cosmos DB into evals/fixtures/records.json (read-only).

The golden sets are built from REAL recorded drills, but evals must be reproducible and must not need Cosmos: this
script is run once (or whenever the fixture should be refreshed) and the JSON is committed. It only issues reads.

    python -m evals.snapshot                      # COSMOS_ENDPOINT / COSMOS_DATABASE, else the e23x defaults
    python -m evals.snapshot --out /tmp/records.json

Auth: DefaultAzureCredential (`az login` locally); the identity needs Cosmos DB data-plane read access.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
DEFAULT_ENDPOINT = "https://dra-cosmos-e23x.documents.azure.com:443/"
SYSTEM = {"_rid", "_self", "_etag", "_attachments", "_ts", "_lsn"}
# the one large field no agent tool or eval reads (the per-second heartbeat series): dropped to keep the fixture small
DROP = {"write_series_json"}
STATE_ROWS = ("autopilot",)  # non-drill rows of the exercises container the tools read


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in SYSTEM and k not in DROP}


def snapshot(endpoint: str, database: str) -> dict:
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential
    db = CosmosClient(endpoint, credential=DefaultAzureCredential()).get_database_client(database)
    exercises = db.get_container_client("exercises")
    ids = [r["id"] for r in exercises.query_items("SELECT c.id FROM c", enable_cross_partition_query=True)]
    keep = sorted(i for i in ids if i.startswith("ex-") or i in STATE_ROWS)
    out: dict = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "source": f"{endpoint.split('//')[-1].split('.')[0]}/{database} (read-only snapshot)",
                 "exercises": {}, "events": {}, "conversations": []}
    events = db.get_container_client("events")
    for xid in keep:
        out["exercises"][xid] = _clean(exercises.read_item(xid, partition_key=xid))
        rows = list(events.query_items("SELECT * FROM c", partition_key=xid))
        out["events"][xid] = sorted((_clean(r) for r in rows), key=lambda r: r["id"])
    conv = db.get_container_client("conversations")
    for r in conv.query_items("SELECT * FROM c WHERE STARTSWITH(c.id, 'turn-')", enable_cross_partition_query=True):
        r = _clean(r)
        out["conversations"].append({"conversation_id": r["pk"], "turn": r["id"], "at": r.get("at"),
                                     "question": r.get("question"), "reply": json.loads(r.get("reply_json") or "{}"),
                                     "trace": json.loads(r["trace_json"]) if r.get("trace_json") else None})
    out["conversations"].sort(key=lambda t: t.get("at") or "")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default=os.environ.get("COSMOS_ENDPOINT") or DEFAULT_ENDPOINT)
    ap.add_argument("--database", default=os.environ.get("COSMOS_DATABASE") or "dra")
    ap.add_argument("--out", default=str(FIXTURES / "records.json"))
    a = ap.parse_args()
    data = snapshot(a.endpoint, a.database)
    Path(a.out).write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    n_ev = sum(len(v) for v in data["events"].values())
    print(f"wrote {a.out}: {len(data['exercises'])} exercise rows, {n_ev} events, {len(data['conversations'])} turns")


if __name__ == "__main__":
    main()
