#!/usr/bin/env bash
# Contoso daily payments report on the IaaS VM (dra-vm-reports): the job whose recovery the ASR drill proves.
#   reports-vm.sh setup     idempotent: reader password in Key Vault (reports-reader-password), contained SQL user
#                           reports_reader (SELECT on dbo.transactions only, created through the VM), service +
#                           scheduled tasks + DPAPI-protected config on the VM, then one report generated now
#   reports-vm.sh status    /health and /report/latest from inside the VM, plus the scheduled tasks
#   reports-vm.sh generate  build today's (UTC) report now from the database (/report/generate)
# Env: ROTATE=true          new reader password (Key Vault + SQL user + VM config)
#      PROTECTION=acl-only  store the password on the VM as plain text in an ACL-restricted file instead of DPAPI
#      TEST_OUTBOUND=false  do not enable default outbound access on the isolated ASR test subnet
#      SQL_ADMIN_PASSWORD   otherwise read from $DRA_SECRETS_FILE (~/.dra-secrets) or Key Vault sql-admin-password
# Secrets never appear on a command line or in output: they go to the VM as Run Command protected parameters
# (encrypted in the extension settings) from 0600 temp files, and the run command is deleted afterwards.
set -euo pipefail
umask 077

CMD="${1:-status}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA="$(cd "$DIR/.." && pwd)"
RG="${WORKLOAD_RG:-rg-dra-workload}"
VM="dra-vm-reports"
DB="${SQL_DATABASE:-paymentsdb}"
SQL_ADMIN_USER="${SQL_ADMIN_USER:-sqladmin}"
SECRET="reports-reader-password"
PROTECTION="${PROTECTION:-dpapi-machine}"
MGMT="https://management.azure.com"
CAPI="api-version=2024-07-01"

OUT=$(az deployment sub show -n dra-deployment --query properties.outputs -o json 2>/dev/null || echo '{}')
SFX=$(jq -r '.suffix.value // empty' <<<"$OUT")
[[ -n "$SFX" ]] || SFX=$(az resource list -g "$RG" --resource-type Microsoft.RecoveryServices/vaults --query "[0].name" -o tsv | sed 's/^dra-rsv-//')
[[ -n "$SFX" ]] || { echo "cannot derive the resource suffix (no dra-deployment outputs, no dra-rsv-* vault in $RG)"; exit 1; }
KV=$(jq -r '.keyVaultName.value // empty' <<<"$OUT"); KV="${KV:-dra-kv-$SFX}"
FOG=$(jq -r '.sqlFogListener.value // empty' <<<"$OUT"); FOG="${FOG:-dra-fog-$SFX.database.windows.net}"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

need_vm() {
  local power
  power=$(az vm get-instance-view -g "$RG" -n "$VM" --query "instanceView.statuses[?starts_with(code,'PowerState/')].code | [0]" -o tsv 2>/dev/null || true)
  [[ "$power" == "PowerState/running" ]] || { echo "$VM is not running (${power:-not found}); start it: az vm start -g $RG -n $VM"; exit 1; }
}

b64() { base64 < "$1" | tr -d '\n'; }

# Legacy Run Command (no secrets): prints the script's stdout; stderr goes to stderr.
invoke_ps() {
  local res
  res=$(az vm run-command invoke -g "$RG" -n "$VM" --command-id RunPowerShellScript --scripts "$1" -o json)
  jq -r '.value[0].message // empty' <<<"$res" | tr -d '\r'
  local err; err=$(jq -r '.value[1].message // empty' <<<"$res")
  [[ -z "$err" ]] || echo "$err" >&2
}

# Managed Run Command with protected parameters (secrets). Args: name script-file params-json-file
run_protected() {
  local name=$1 script=$2 params=$3 url view state="" t=0
  # unique name per run: a run command resource is executed when it is created (and deleted again below)
  url="$MGMT$(az vm show -g "$RG" -n "$VM" --query id -o tsv)/runCommands/$name-$(date +%s)"
  local loc; loc=$(az vm show -g "$RG" -n "$VM" --query location -o tsv)
  jq -n --arg loc "$loc" --rawfile s "$script" --slurpfile p "$params" \
    '{location: $loc, properties: {source: {script: $s}, protectedParameters: $p[0], asyncExecution: false,
      timeoutInSeconds: 600, treatFailureAsDeploymentFailure: false}}' > "$TMP/body.json"
  rm -f "$params"
  az rest --method put --url "$url?$CAPI" --body @"$TMP/body.json" -o none
  rm -f "$TMP/body.json"
  while :; do
    view=$(az rest --method get --url "$url?$CAPI&\$expand=instanceView" -o json 2>/dev/null || echo '{}')
    state=$(jq -r '.properties.instanceView.executionState // "Pending"' <<<"$view")
    [[ "$(jq -r '.properties.provisioningState // ""' <<<"$view")" == "Failed" && "$state" == "Pending" ]] && state="Failed"
    case "$state" in Succeeded|Failed|TimedOut|Canceled) break ;; esac
    (( t >= 900 )) && { state="TimedOut"; break; }
    sleep 10; t=$((t + 10))
  done
  jq -r '.properties.instanceView.output // empty' <<<"$view" | sed 's/^/    /'
  local err; err=$(jq -r '.properties.instanceView.error // empty' <<<"$view")
  [[ -z "$err" ]] || echo "$err" | sed 's/^/    stderr: /' >&2
  local code; code=$(jq -r '.properties.instanceView.exitCode // 1' <<<"$view")
  az rest --method delete --url "$url?$CAPI" -o none 2>/dev/null || true   # drop the (encrypted) parameters too
  [[ "$state" == "Succeeded" && "$code" == "0" ]] || { echo "  run command $name: $state (exit $code)"; return 1; }
}

sql_admin_password() {
  if [[ -z "${SQL_ADMIN_PASSWORD:-}" && -f "${DRA_SECRETS_FILE:-$HOME/.dra-secrets}" ]]; then
    # shellcheck disable=SC1090
    source "${DRA_SECRETS_FILE:-$HOME/.dra-secrets}"
  fi
  if [[ -z "${SQL_ADMIN_PASSWORD:-}" ]]; then
    SQL_ADMIN_PASSWORD=$(az keyvault secret show --vault-name "$KV" -n sql-admin-password --query value -o tsv 2>/dev/null || true)
  fi
  [[ -n "${SQL_ADMIN_PASSWORD:-}" ]] || { echo "SQL admin password not found (env, ~/.dra-secrets or $KV/sql-admin-password)"; exit 1; }
}

PS_GENERATE=$(cat <<'EOF'
$ProgressPreference = 'SilentlyContinue'
try { (Invoke-WebRequest -UseBasicParsing -Method Post -TimeoutSec 120 -Uri 'http://localhost:8080/report/generate').Content }
catch { if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { '{"status":"error","reason":"' + ($_.Exception.Message -replace '"', "'") + '"}' } }
EOF
)

PS_STATUS=$(cat <<'EOF'
$ProgressPreference = 'SilentlyContinue'
function Get-Body($u) { try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $u).Content } catch { if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { '{"status":"error","reason":"' + ($_.Exception.Message -replace '"', "'") + '"}' } } }
'HEALTH ' + (Get-Body 'http://localhost:8080/health')
'LATEST ' + (Get-Body 'http://localhost:8080/report/latest')
'TASKS ' + ((Get-ScheduledTask -TaskName 'ContosoReports*' -ErrorAction SilentlyContinue | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "$($_.TaskName)=$($_.State) last=$($i.LastRunTime) result=$($i.LastTaskResult) next=$($i.NextRunTime)" }) -join '; ')
'FILES ' + ((Get-ChildItem C:\reports -Filter 'payments-*.json' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 5 | ForEach-Object { $_.Name }) -join ', ')
EOF
)

pretty() { local l; while IFS= read -r l; do jq -C . <<<"$l" 2>/dev/null || echo "$l"; done; }

status() {
  need_vm
  echo "Reports service on $VM ($RG):"
  invoke_ps "$PS_STATUS" | while IFS= read -r line; do
    case "$line" in
      HEALTH\ *|LATEST\ *) echo "${line%% *}:"; pretty <<<"${line#* }" ;;
      *) if [[ -n "$line" ]]; then echo "$line"; fi ;;
    esac
  done
}

generate() {
  need_vm
  echo "Generating today's payments report on $VM (from $FOG/$DB):"
  invoke_ps "$PS_GENERATE" | pretty
}

setup() {
  need_vm
  echo "Reports VM setup: $VM, database $FOG/$DB, Key Vault $KV"

  echo "== 1/5 reader password (Key Vault secret $SECRET)"
  local reader=""
  [[ "${ROTATE:-false}" == "true" ]] || reader=$(az keyvault secret show --vault-name "$KV" -n "$SECRET" --query value -o tsv 2>/dev/null || true)
  if [[ -z "$reader" ]]; then
    reader="Rr7$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 26 || true)"
    printf '%s' "$reader" > "$TMP/reader"
    az keyvault secret set --vault-name "$KV" -n "$SECRET" --file "$TMP/reader" --encoding utf-8 \
      --content-type "SQL contained user reports_reader@$DB (read-only)" -o none
    rm -f "$TMP/reader"
    echo "  new password stored"
  else
    echo "  reusing the existing secret (ROTATE=true to change it)"
  fi

  echo "== 2/5 contained database user reports_reader (run from inside the VM)"
  sql_admin_password
  sed "s|__SQL_B64__|$(b64 "$INFRA/sql/reports_reader.sql")|" "$INFRA/vm/reports/create-reader.ps1" > "$TMP/create-reader.ps1"
  ADMIN_PW="$SQL_ADMIN_PASSWORD" READER_PW="$reader" jq -n --arg srv "$FOG" --arg db "$DB" --arg au "$SQL_ADMIN_USER" \
    '[{name: "SqlServer", value: $srv}, {name: "SqlDatabase", value: $db}, {name: "AdminUser", value: $au},
      {name: "AdminPassword", value: env.ADMIN_PW}, {name: "ReaderPassword", value: env.READER_PW}]' > "$TMP/p-reader.json"
  run_protected "reports-sql-reader" "$TMP/create-reader.ps1" "$TMP/p-reader.json"

  echo "== 3/5 reports service, scheduled tasks and $PROTECTION config on the VM"
  sed "s|__SERVICE_B64__|$(b64 "$INFRA/vm/reports/reports-service.ps1")|" "$INFRA/vm/reports/install-reports.ps1" > "$TMP/install.ps1"
  READER_PW="$reader" jq -n --arg srv "$FOG" --arg db "$DB" --arg prot "$PROTECTION" \
    '[{name: "SqlServer", value: $srv}, {name: "SqlDatabase", value: $db}, {name: "ReaderUser", value: "reports_reader"},
      {name: "ReaderPassword", value: env.READER_PW}, {name: "Protection", value: $prot}]' > "$TMP/p-install.json"
  run_protected "reports-install" "$TMP/install.ps1" "$TMP/p-install.json"

  echo "== 4/5 isolated ASR test network: outbound access (lets the recovered VM reach Azure SQL)"
  local subnet=(-g "$RG" --vnet-name "dra-vnet-sea-test-$SFX" -n default)
  if [[ "${TEST_OUTBOUND:-true}" != "true" ]]; then
    echo "  skipped (TEST_OUTBOUND=false): a drill will prove the cached report only"
  elif [[ "$(az network vnet subnet show "${subnet[@]}" --query defaultOutboundAccess -o tsv 2>/dev/null || true)" == "true" ]]; then
    echo "  already enabled"
  else
    az network vnet subnet update "${subnet[@]}" --default-outbound-access true -o none \
      && echo "  enabled (no peering or routes to production are added)" \
      || echo "  WARNING: could not enable it; drills will fall back to the cached report"
  fi

  echo "== 5/5 first report"
  generate
}

case "$CMD" in
  setup) setup ;;
  status) status ;;
  generate) generate ;;
  *) echo "usage: $0 setup|status|generate"; exit 2 ;;
esac
