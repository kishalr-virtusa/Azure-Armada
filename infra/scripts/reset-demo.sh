#!/usr/bin/env bash
# Reset the DR console to a fresh "no drills yet" state, keeping the environment deployed and healthy.
#   reset-demo.sh             drill history, timelines, notifications, heartbeats, reports, saved agent results
#   reset-demo.sh --payments  also empties Contoso's test payments and the SQL heartbeat table
#                             (adds a temporary SQL firewall rule for this machine, removed straight after)
# Everything is backed up first to ~/dr-backups/<timestamp>/. Asks for confirmation before deleting
# (or pass CONFIRM=RESET in the environment).
set -euo pipefail

RG_CONTROL="${CONTROL_RG:-rg-dra-control}"
RG_WORKLOAD="${WORKLOAD_RG:-rg-dra-workload}"
SA=$(az storage account list -g "$RG_CONTROL" --query "[0].name" -o tsv)
CONSOLE="${CONSOLE_APP_NAME:-azure-armada}"
PAYMENTS=false; [[ "${1:-}" == "--payments" ]] && PAYMENTS=true
BACKUP="$HOME/dr-backups/$(date +%Y%m%d-%H%M%S)"
PY="${PYTHON:-$( [[ -x $HOME/agents/azure-armada/.venv/bin/python ]] && echo $HOME/agents/azure-armada/.venv/bin/python || echo python3 )}"

echo "This resets the DR console in storage account $SA:"
echo "  - deletes all drills, timelines, notifications, heartbeats, reports and saved phase-1/plan results"
$PAYMENTS && echo "  - empties Contoso payments and the SQL heartbeat table (paymentsdb)"
echo "  - backs everything up first to $BACKUP"
ok="${CONFIRM:-}"   # CONFIRM=RESET skips the prompt (for non-interactive shells)
[[ -n "$ok" ]] || read -r -p "Type RESET to continue: " ok
[[ "$ok" == "RESET" ]] || { echo "Cancelled."; exit 1; }

export STORAGE_KEY SA BACKUP
STORAGE_KEY=$(az storage account keys list -n "$SA" -g "$RG_CONTROL" --query "[0].value" -o tsv)
mkdir -p "$BACKUP/reports"
"$PY" - <<'EOF'
import json, os
from azure.core.credentials import AzureNamedKeyCredential
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient

sa, key, out = os.environ["SA"], os.environ["STORAGE_KEY"], os.environ["BACKUP"]
tables = TableServiceClient(endpoint=f"https://{sa}.table.core.windows.net", credential=AzureNamedKeyCredential(sa, key))
blobs = BlobServiceClient(f"https://{sa}.blob.core.windows.net", credential={"account_name": sa, "account_key": key})
for name in ["exercises", "events", "notifications", "heartbeats"]:
    tc = tables.get_table_client(name)
    rows = [dict(e) for e in tc.list_entities()]
    json.dump(rows, open(f"{out}/{name}.json", "w"), default=str, indent=1)
    for e in rows:
        if name == "exercises" and e["PartitionKey"] == "settings":
            continue  # keep the demo app's private/public setting
        tc.delete_entity(e["PartitionKey"], e["RowKey"])
    print(f"  {name}: backed up and cleared {len(rows)} rows")
cc = blobs.get_container_client("reports")
names = [b.name for b in cc.list_blobs()]
for n in names:
    path = f"{out}/reports/{n}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(cc.download_blob(n).readall())
    cc.delete_blob(n)
print(f"  reports: backed up and deleted {len(names)} files")
EOF

if $PAYMENTS; then
  SERVER=$(az sql server list -g "$RG_WORKLOAD" --query "[?contains(name,'-eas-')].name | [0]" -o tsv)
  FOG=$(az sql failover-group list -g "$RG_WORKLOAD" -s "$SERVER" --query "[0].name" -o tsv)
  MYIP=$(curl -s https://api.ipify.org)
  RULE="reset-demo-temp"
  echo "  adding temporary SQL firewall rule for $MYIP on $SERVER"
  az sql server firewall-rule create -g "$RG_WORKLOAD" -s "$SERVER" -n "$RULE" --start-ip-address "$MYIP" --end-ip-address "$MYIP" -o none
  trap 'az sql server firewall-rule delete -g "$RG_WORKLOAD" -s "$SERVER" -n "$RULE" -o none && echo "  temporary firewall rule removed"' EXIT
  sleep 20
  source "$HOME/.dra-secrets"
  SQL_HOST="$FOG.database.windows.net" "$PY" - <<'EOF'
import os, pymssql
c = pymssql.connect(server=os.environ["SQL_HOST"], user="sqladmin", password=os.environ["SQL_ADMIN_PASSWORD"],
                    database="paymentsdb", login_timeout=15)
cur = c.cursor()
for t in ("transactions", "heartbeat"):
    cur.execute(f"SELECT COUNT(*) FROM dbo.{t}"); n = cur.fetchone()[0]
    cur.execute(f"TRUNCATE TABLE dbo.{t}")
    print(f"  paymentsdb.{t}: cleared {n} rows (test data; not backed up)")
c.commit(); c.close()
EOF
fi

echo "  restarting the console to clear its in-memory caches"
az webapp restart -g "$RG_CONTROL" -n "$CONSOLE" -o none
echo "Done. Backup: $BACKUP"
