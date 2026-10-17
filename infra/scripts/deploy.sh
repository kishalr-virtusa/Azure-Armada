#!/bin/bash
# Deploy DR Agent infrastructure
# Usage: ./deploy.sh
# Requires: az login, SQL_ADMIN_PASSWORD env var

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"

# Check prerequisites
if ! az account show &>/dev/null; then
    echo "Error: Not logged in to Azure. Run 'az login' first."
    exit 1
fi

if [[ -z "${SQL_ADMIN_PASSWORD:-}" ]]; then
    echo "Error: SQL_ADMIN_PASSWORD environment variable is required."
    exit 1
fi

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo "Deploying to subscription: $SUBSCRIPTION_ID"

# Deploy Bicep at subscription scope
echo "Starting infrastructure deployment..."
az deployment sub create \
    --name "dra-deployment-$(date +%Y%m%d-%H%M%S)" \
    --location centralindia \
    --template-file "$INFRA_DIR/main.bicep" \
    --parameters "$INFRA_DIR/parameters.json" \
    --parameters sqlAdminPassword="$SQL_ADMIN_PASSWORD" \
    --output table

# Get deployment outputs
echo ""
echo "Fetching deployment outputs..."
OUTPUTS=$(az deployment sub show \
    --name "$(az deployment sub list --query '[0].name' -o tsv)" \
    --query properties.outputs -o json)

TM_URL=$(echo "$OUTPUTS" | jq -r '.trafficManagerUrl.value')
CONSOLE_URL=$(echo "$OUTPUTS" | jq -r '.consoleUrl.value')
SUFFIX=$(echo "$OUTPUTS" | jq -r '.suffix.value')

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo "Traffic Manager URL: $TM_URL"
echo "Console URL:         $CONSOLE_URL"
echo "Suffix:              $SUFFIX"
echo ""
echo "Next steps:"
echo "1. Run seed-secrets.sh to populate Key Vault"
echo "2. Run deploy-apps.sh to deploy application code"
echo "=========================================="
