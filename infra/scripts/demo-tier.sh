#!/usr/bin/env bash
# Switch the workload between the build tier and the demo-week tier.
#   demo-tier.sh up     App Service P0v3 + SQL Standard S1 (faster restarts, headroom for the load test)  ~$6.75/day
#   demo-tier.sh down   back to App Service B1 + SQL Basic (the cheap build tier)                         ~$1.30/day
# SQL: the geo-secondary must never be on a lower tier than the primary, so it is upgraded first and downgraded last.
set -euo pipefail
RG="${WORKLOAD_RG:-rg-dra-workload}"
SFX=$(az resource list -g "$RG" --resource-type Microsoft.Web/serverfarms --query "[0].name" -o tsv | sed 's/.*-//')
PLAN_A="dra-plan-eas-$SFX"; PLAN_B="dra-plan-sea-$SFX"
SQL_A="dra-sql-eas-$SFX";  SQL_B="dra-sql-sea-$SFX"

case "${1:-}" in
  up)
    az sql db update -g "$RG" -s "$SQL_B" -n paymentsdb --edition Standard --service-objective S1 -o none
    az sql db update -g "$RG" -s "$SQL_A" -n paymentsdb --edition Standard --service-objective S1 -o none
    az appservice plan update -g "$RG" -n "$PLAN_B" --sku P0V3 -o none
    az appservice plan update -g "$RG" -n "$PLAN_A" --sku P0V3 -o none ;;
  down)
    az appservice plan update -g "$RG" -n "$PLAN_A" --sku B1 -o none
    az appservice plan update -g "$RG" -n "$PLAN_B" --sku B1 -o none
    az sql db update -g "$RG" -s "$SQL_A" -n paymentsdb --edition Basic --service-objective Basic -o none
    az sql db update -g "$RG" -s "$SQL_B" -n paymentsdb --edition Basic --service-objective Basic -o none ;;
  *) echo "usage: $0 up|down"; exit 2 ;;
esac
az appservice plan list -g "$RG" --query "[].{plan:name,sku:sku.name}" -o table
az sql db list -g "$RG" -s "$SQL_A" --query "[?name=='paymentsdb'].{db:name,tier:currentServiceObjectiveName}" -o table
