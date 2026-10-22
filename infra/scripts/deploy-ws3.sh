#!/bin/bash
# WS3 observability: Foundry project <-> App Insights connection (Foundry portal Tracing), the
# "DR Agent: agent operations" workbook and, optionally, the fallback-rate alert.
# Standalone module (infra/modules/observability.bicep) deployed into rg-dra-control; main.bicep is untouched.
#
# Usage: ./deploy-ws3.sh                  deploy connection + workbook
#        ./deploy-ws3.sh --with-alert     also the "fallback rate > 30% in 1 h" log alert (about $1.50 per month)
#        ./deploy-ws3.sh --telemetry off  kill switch: TELEMETRY_ENABLED=false on the console (restarts it); "on" undoes
#
# The console code (dr_agent/telemetry.py, gunicorn.conf.py) ships with deploy-apps.sh. It exports to the
# APPLICATIONINSIGHTS_CONNECTION_STRING app setting that console.bicep already sets; no other app setting is needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
CONTROL_RG="rg-dra-control"
EXPECTED_SUB="Azure subscription 1"
ALERT=false
TELEMETRY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-alert) ALERT=true; shift ;;
        --telemetry) TELEMETRY="${2:-}"; shift 2 || { echo "Error: --telemetry needs on|off"; exit 1; } ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Error: unknown argument $1"; exit 1 ;;
    esac
done

if ! az account show &>/dev/null; then
    echo "Error: Not logged in to Azure. Run 'az login' first."
    exit 1
fi
SUB_NAME=$(az account show --query name -o tsv)
if [[ "$SUB_NAME" != "$EXPECTED_SUB" ]]; then
    echo "Error: active subscription is '$SUB_NAME'; run: az account set --subscription \"$EXPECTED_SUB\""
    exit 1
fi

INSIGHTS=$(az resource list -g "$CONTROL_RG" --resource-type Microsoft.Insights/components \
    --query "[?starts_with(name, 'dra-insights-')].name | [0]" -o tsv)
if [[ -z "$INSIGHTS" ]]; then
    echo "Error: no dra-insights-* Application Insights in $CONTROL_RG. Run deploy.sh first."
    exit 1
fi
SUFFIX="${INSIGHTS#dra-insights-}"
CONSOLE_APP=$(az webapp list -g "$CONTROL_RG" --query "[?contains(name, 'armada') || starts_with(name, 'dra-console')].name | [0]" -o tsv)

if [[ -n "$TELEMETRY" ]]; then
    case "$TELEMETRY" in
        on) VALUE=true ;;
        off) VALUE=false ;;
        *) echo "Error: --telemetry takes on or off"; exit 1 ;;
    esac
    echo "Setting TELEMETRY_ENABLED=$VALUE on $CONSOLE_APP (the app restarts)..."
    az webapp config appsettings set -g "$CONTROL_RG" -n "$CONSOLE_APP" --settings "TELEMETRY_ENABLED=$VALUE" -o none
    echo "Done."
    exit 0
fi

echo "Deploying observability (suffix $SUFFIX, alert: $ALERT) to $CONTROL_RG..."
OUT=$(az deployment group create \
    --resource-group "$CONTROL_RG" \
    --name dra-observability \
    --template-file "$INFRA_DIR/modules/observability.bicep" \
    --parameters suffix="$SUFFIX" deployFallbackAlert="$ALERT" \
    --query properties.outputs -o json)

WORKBOOK_URL=$(echo "$OUT" | jq -r '.workbookPortalUrl.value')
CONNECTION=$(echo "$OUT" | jq -r '.connectionName.value')
echo ""
echo "Workbook:  $WORKBOOK_URL"
echo "           (or Portal > $INSIGHTS > Workbooks > 'DR Agent: agent operations')"
echo "Tracing:   https://ai.azure.com > project dr-agent > Tracing (connection: $CONNECTION)"
echo "           needs 'Log Analytics Reader' on $INSIGHTS for whoever opens it (Owner/Contributor already have it)"
echo ""
echo "Check after one rehearsal (Log Analytics, dra-log-$SUFFIX):"
echo '  AppDependencies | where Name startswith "invoke_agent" | summarize count(), percentile(DurationMs, 95),'
echo '    sum(todouble(Properties["dr.cost_usd"])) by tostring(Properties["gen_ai.agent.name"])'
echo '  AppRequests | where Url has "/api/live-map" | count            // expect 0'
echo '  Usage | where TimeGenerated > ago(1d) | summarize GB = sum(Quantity) / 1000 by DataType'
