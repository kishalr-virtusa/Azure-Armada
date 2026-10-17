#!/usr/bin/env bash
# Creates the subscription budget "dra-budget" ($150/month) with alerts to subscription Owners
# at $25, $50, $100 and $150 (actual), plus a $150 forecast alert. Run BEFORE deploying anything.
set -euo pipefail
SUB_ID="${SUB_ID:-$(az account show --query id -o tsv)}"
az provider register -n Microsoft.Consumption -o none
az rest --method put \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/providers/Microsoft.Consumption/budgets/dra-budget?api-version=2023-05-01" \
  --body @"$(dirname "$0")/budget.json" --query "{name:name,amount:properties.amount}" -o tsv
