#!/bin/bash
# Deploy application code to App Services
# Usage: ./deploy-apps.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Get resource names from deployment outputs
echo "Fetching deployment information..."
OUTPUTS=$(az deployment sub show \
    --name dra-deployment \
    --query properties.outputs -o json 2>/dev/null || echo "{}")

if [[ "$OUTPUTS" == "{}" ]]; then
    echo "Error: No deployment found. Run deploy.sh first."
    exit 1
fi

SUFFIX=$(echo "$OUTPUTS" | jq -r '.suffix.value')
WORKLOAD_RG="rg-dra-workload"
CONTROL_RG="rg-dra-control"

WEBAPP_A="dra-app-eas-${SUFFIX}"
WEBAPP_B="dra-app-sea-${SUFFIX}"
CONSOLE_APP=$(echo "$OUTPUTS" | jq -r ".consoleAppName.value // \"dra-console-${SUFFIX}\"")

echo "Deploying to:"
echo "  Web App A: $WEBAPP_A"
echo "  Web App B: $WEBAPP_B"
echo "  Console:   $CONSOLE_APP"
echo ""

# Create zip packages
echo "Creating deployment packages..."

# Demo app
DEMO_ZIP="/tmp/demo-app.zip"
(cd "$REPO_ROOT/apps/demo-app" && zip -r "$DEMO_ZIP" . -x "*.pyc" -x "__pycache__/*" -x ".env" -x "*.zip")

# Console app (bundle the DR runbook reviewed by the Runbook agent)
mkdir -p "$REPO_ROOT/apps/dr-console/runbooks" && cp "$REPO_ROOT/docs/runbooks/"*.md "$REPO_ROOT/apps/dr-console/runbooks/"
CONSOLE_ZIP="/tmp/dr-console.zip"
(cd "$REPO_ROOT/apps/dr-console" && zip -r "$CONSOLE_ZIP" . -x "*.pyc" -x "__pycache__/*" -x ".env" -x "*.zip" -x ".pytest_cache/*" -x "tests/*")

# Deploy demo app to both regions
echo "Deploying demo app to Region A ($WEBAPP_A)..."
az webapp deploy --resource-group "$WORKLOAD_RG" --name "$WEBAPP_A" --src-path "$DEMO_ZIP" --type zip --timeout 900000

echo "Deploying demo app to Region B ($WEBAPP_B)..."
az webapp deploy --resource-group "$WORKLOAD_RG" --name "$WEBAPP_B" --src-path "$DEMO_ZIP" --type zip --timeout 900000

# Deploy console app
echo "Deploying console app ($CONSOLE_APP)..."
az webapp deploy --resource-group "$CONTROL_RG" --name "$CONSOLE_APP" --src-path "$CONSOLE_ZIP" --type zip --timeout 900000

# Restart apps to pick up new code
echo "Restarting applications..."
az webapp restart --resource-group "$WORKLOAD_RG" --name "$WEBAPP_A" &
az webapp restart --resource-group "$WORKLOAD_RG" --name "$WEBAPP_B" &
az webapp restart --resource-group "$CONTROL_RG" --name "$CONSOLE_APP" &
wait

# Key Vault references: a brand-new app's identity can be denied for a few minutes (RBAC propagation) and
# App Service caches that failure. Wait until each reference resolves, nudging a refresh, then restart.
SUB_ID=$(az account show --query id -o tsv)
wait_kv_refs() { # rg app
  local url="https://management.azure.com/subscriptions/$SUB_ID/resourceGroups/$1/providers/Microsoft.Web/sites/$2/config/configreferences/appsettings?api-version=2024-04-01"
  for i in $(seq 1 20); do
    local bad
    bad=$(az rest --method get --url "$url" --query "value[?properties.status!='Resolved'].name" -o tsv 2>/dev/null)
    if [[ -z "$bad" ]]; then echo "  $2: Key Vault references resolved"; return 0; fi
    echo "  $2: waiting for Key Vault references ($(echo $bad | tr '\n' ' '))"
    az webapp config appsettings set -g "$1" -n "$2" --settings KV_REFRESH="$(date +%s)" -o none
    sleep 30
  done
  echo "  WARNING: $2 Key Vault references still unresolved"
}
wait_kv_refs "$WORKLOAD_RG" "$WEBAPP_A"
wait_kv_refs "$WORKLOAD_RG" "$WEBAPP_B"
wait_kv_refs "$CONTROL_RG" "$CONSOLE_APP"
az webapp restart -g "$WORKLOAD_RG" -n "$WEBAPP_A" -o none & az webapp restart -g "$WORKLOAD_RG" -n "$WEBAPP_B" -o none & wait

# Cleanup
rm -f "$DEMO_ZIP" "$CONSOLE_ZIP"

echo ""
echo "=========================================="
echo "Application deployment complete!"
echo "=========================================="
echo "Demo app A: https://${WEBAPP_A}.azurewebsites.net"
echo "Demo app B: https://${WEBAPP_B}.azurewebsites.net"
echo "Console:    https://${CONSOLE_APP}.azurewebsites.net"
echo "=========================================="
