#!/usr/bin/env bash
# Creates/updates the subscription budget "dra-budget" ($150/month) with alerts at $25, $50, $100, $150 (actual)
# and a $150 forecast alert, sent to subscription Owners + ALERT_EMAIL. Run BEFORE deploying anything.
# Usage: ALERT_EMAIL=you@example.com ./budget.sh
set -euo pipefail
: "${ALERT_EMAIL:?Set ALERT_EMAIL to the address that should receive budget alerts}"
SUB_ID="${SUB_ID:-$(az account show --query id -o tsv)}"
BODY="$(mktemp)"; trap 'rm -f "$BODY"' EXIT
sed "s/__ALERT_EMAIL__/${ALERT_EMAIL}/g" "$(dirname "$0")/budget.json" > "$BODY"
az provider register -n Microsoft.Consumption -o none
az rest --method put \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/providers/Microsoft.Consumption/budgets/dra-budget?api-version=2023-05-01" \
  --body @"$BODY" --query "{name:name,amount:properties.amount}" -o tsv
