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

WEBAPP_A="dra-app-cin-${SUFFIX}"
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
