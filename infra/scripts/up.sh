#!/usr/bin/env bash
# ONE COMMAND to bring the whole DR Agent solution up (infra + apps + smoke test).
#   ./infra/scripts/up.sh
# Secrets live in $DRA_SECRETS_FILE (default ~/.dra-secrets, never committed). Created on first run.
# Optional env: SUB_ID (subscription id), NOTIFY_EMAIL_TO (budget + DR notification emails),
#               PLANT_CONFIG_GAP=true|false (demo drift in Region B), DEPLOY_EMAIL=true|false
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE="${DRA_SECRETS_FILE:-$HOME/.dra-secrets}"

command -v az >/dev/null || { echo "Azure CLI (az) is required"; exit 1; }
command -v jq >/dev/null || { echo "jq is required"; exit 1; }
command -v zip >/dev/null || { echo "zip is required"; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not logged in. Run: az login"; exit 1; }
[[ -n "${SUB_ID:-}" ]] && az account set --subscription "$SUB_ID"
echo "Subscription: $(az account show --query '[name, id]' -o tsv | paste -sd' ')"

if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "Creating $SECRETS_FILE with new random secrets"
  (umask 077; printf 'export SQL_ADMIN_PASSWORD=%s\nexport CONSOLE_PASSCODE=%s\n' \
     "Dra$(openssl rand -hex 10)!9" "virtusa-$(openssl rand -hex 3)" > "$SECRETS_FILE")
fi
# shellcheck disable=SC1090
source "$SECRETS_FILE"
if [[ -z "${APP_ACCESS_CODE:-}" ]]; then  # team access code for the private demo app (added once)
  APP_ACCESS_CODE=$(LC_ALL=C tr -dc 'A-HJ-NP-Z2-9' </dev/urandom | head -c 8)
  echo "export APP_ACCESS_CODE=$APP_ACCESS_CODE" >> "$SECRETS_FILE"
  export APP_ACCESS_CODE
fi

for p in Microsoft.Web Microsoft.Sql Microsoft.Network Microsoft.Storage Microsoft.KeyVault \
         Microsoft.CognitiveServices Microsoft.OperationalInsights Microsoft.Insights \
         Microsoft.Communication Microsoft.Consumption Microsoft.Compute Microsoft.RecoveryServices \
         Microsoft.Chaos Microsoft.LoadTestService Microsoft.Search; do
  az provider register -n "$p" -o none
done

if [[ -n "${NOTIFY_EMAIL_TO:-}" ]]; then
  echo "== 1/4 Budget alerts"
  ALERT_EMAIL="$NOTIFY_EMAIL_TO" "$DIR/budget.sh"
else
  echo "== 1/4 Budget alerts skipped (set NOTIFY_EMAIL_TO to create/update them)"
fi

echo "== 2/4 Infrastructure (Bicep, ~15-25 min)"
"$DIR/deploy.sh"

echo "== 3/4 Application code + sign-in + Site Recovery"
"$DIR/entra-auth.sh" || echo "WARNING: Entra sign-in not configured (console uses the passcode); re-run entra-auth.sh"
"$DIR/deploy-apps.sh"
if [[ "${DEPLOY_IAAS:-true}" == "true" ]]; then
  "$DIR/asr.sh" enable || echo "WARNING: ASR replication not enabled yet; re-run asr.sh enable"
fi

echo "== 4/4 Smoke test"
"$DIR/smoke.sh" || echo "WARNING: smoke test reported problems (apps may still be warming up; re-run smoke.sh in 2 min)"

echo
echo "UP. Console: https://${CONSOLE_APP_NAME:-azure-armada}.azurewebsites.net (Microsoft Entra ID sign-in;"
echo "    add teammates with: $DIR/entra-auth.sh add-user <user@tenant>)."
echo "    Phones (team, private): https://${CONSOLE_APP_NAME:-azure-armada}.azurewebsites.net/pay?code=\$APP_ACCESS_CODE (see $SECRETS_FILE)"
