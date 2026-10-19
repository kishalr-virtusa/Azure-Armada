"""One-off move of the console's records from Azure Table Storage to Azure Cosmos DB.

Needs azure-data-tables (requirements-dev.txt) besides the console's requirements, and an identity with
'Storage Table Data Reader' on the storage account and the 'Cosmos DB Built-in Data Contributor' data-plane role on
the Cosmos DB account (az login works: DefaultAzureCredential).

  STORAGE_ACCOUNT_NAME=drastoragee23x COSMOS_ENDPOINT=https://dra-cosmos-e23x.documents.azure.com:443/ \\
      python scripts/migrate_tables_to_cosmos.py --dry-run
  (then again without --dry-run; STORAGE_TABLE_ENDPOINT may replace STORAGE_ACCOUNT_NAME, COSMOS_DATABASE
  defaults to 'dra', --skip-heartbeats leaves the raw heartbeat rows behind)

Idempotent: every document is upserted under the same id, so it can be re-run (e.g. once more just before the
switch-over). Table -> Cosmos shapes (see dr_agent/tools/store.py):
  exercises      PK=<id>, RK='meta'                 -> exercises      {id: <id>, pk: <id>, ...}
  exercises      PK='bbconv-<conv>', RK=<row>       -> conversations  {id: <row>, pk: <conv>, ...}
  events         PK=<feed>, RK=<seq>                -> events         {id: <seq>, pk: <feed>, ...}
  notifications  PK='global', RK=<id>              -> notifications  {id: <id>, pk: 'global', ...}
  heartbeats     PK='hb', RK=<seq>                  -> heartbeats     {id: <seq>, pk: 'hb', ...}
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime

from azure.cosmos import CosmosClient
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential

TABLES = ("exercises", "events", "notifications", "heartbeats")


def _value(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return getattr(v, "value", v)   # EntityProperty (e.g. Int64) -> plain value


def convert(table: str, entity: dict) -> tuple[str, dict] | None:
    """(container, document) for one Table entity, or None when it has no place in the new layout."""
    pk, rk = entity["PartitionKey"], entity["RowKey"]
    fields = {k: _value(v) for k, v in entity.items() if k not in ("PartitionKey", "RowKey") and v is not None}
    if table == "exercises":
        if pk.startswith("bbconv-"):
            return "conversations", {**fields, "id": rk, "pk": pk[len("bbconv-"):]}
        if rk == "meta":
            return "exercises", {**fields, "id": pk, "pk": pk}
        return None
    return table, {**fields, "id": rk, "pk": pk}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="read and convert only, write nothing")
    ap.add_argument("--skip-heartbeats", action="store_true", help="do not copy the raw heartbeat rows")
    args = ap.parse_args()

    endpoint = os.getenv("STORAGE_TABLE_ENDPOINT") or (
        f"https://{os.environ['STORAGE_ACCOUNT_NAME']}.table.core.windows.net" if os.getenv("STORAGE_ACCOUNT_NAME") else "")
    if not endpoint or not os.getenv("COSMOS_ENDPOINT"):
        print("set STORAGE_ACCOUNT_NAME (or STORAGE_TABLE_ENDPOINT) and COSMOS_ENDPOINT", file=sys.stderr)
        return 2
    cred = DefaultAzureCredential()
    tables = TableServiceClient(endpoint=endpoint, credential=cred)
    db = None if args.dry_run else CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=cred).get_database_client(
        os.getenv("COSMOS_DATABASE", "dra"))

    read, written, skipped = Counter(), Counter(), Counter()
    for table in TABLES:
        if table == "heartbeats" and args.skip_heartbeats:
            continue
        for entity in tables.get_table_client(table).list_entities():
            read[table] += 1
            out = convert(table, dict(entity))
            if not out:
                skipped[table] += 1
                continue
            container, doc = out
            if db is not None:
                db.get_container_client(container).upsert_item(doc)
            written[container] += 1
            if sum(written.values()) % 500 == 0:
                print(f"  ... {sum(written.values())} documents", flush=True)

    print(f"{'DRY RUN: ' if args.dry_run else ''}read from Table Storage: {dict(read)}")
    print(f"{'would write' if args.dry_run else 'written'} to Cosmos DB: {dict(written)}")
    if skipped:
        print(f"skipped (no place in the new layout): {dict(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
