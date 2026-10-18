#!/usr/bin/env bash
# Creates/updates the subscription budget "dra-budget" with alerts at 20/40/80/100% (actual) and 100% (forecast),
# sent ONLY to ALERT_EMAIL (not to subscription Owners: in the Virtusa tenant those include the tenant admins).
# The amount is in the SUBSCRIPTION's billing currency (INR here): default 12500 (about USD 150).
# Usage: ALERT_EMAIL=you@example.com ./budget.sh
set -euo pipefail
: "${ALERT_EMAIL:?Set ALERT_EMAIL to the address that should receive budget alerts}"
SUB_ID="${SUB_ID:-$(az account show --query id -o tsv)}"
BODY="$(mktemp)"; trap 'rm -f "$BODY"' EXIT
AMOUNT="${BUDGET_AMOUNT:-12500}"
sed -e "s/__ALERT_EMAIL__/${ALERT_EMAIL}/g" -e "s/__AMOUNT__/${AMOUNT}/g" "$(dirname "$0")/budget.json" > "$BODY"
az provider register -n Microsoft.Consumption -o none
az rest --method put \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/providers/Microsoft.Consumption/budgets/dra-budget?api-version=2023-05-01" \
  --body @"$BODY" --query "{name:name,amount:properties.amount}" -o tsv
