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

if [[ -z "${SQL_ADMIN_PASSWORD:-}" || -z "${CONSOLE_PASSCODE:-}" || -z "${APP_ACCESS_CODE:-}" ]]; then
    echo "Error: SQL_ADMIN_PASSWORD, CONSOLE_PASSCODE and APP_ACCESS_CODE are required (up.sh creates them in ~/.dra-secrets)."
    echo "Optional: NOTIFY_EMAIL_TO (email for DR notifications), PLANT_CONFIG_GAP=true|false, DEPLOY_EMAIL=true|false (default false: blocked on Free Trial)"
    exit 1
fi

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo "Deploying to subscription: $SUBSCRIPTION_ID"

# Deploy Bicep at subscription scope
echo "Starting infrastructure deployment..."
# SSH key for the IaaS VM (never used for login; Run Command is used). Generated once, kept outside the repo.
VM_KEY="${DRA_VM_KEY:-$HOME/.dra-vm-key}"
[[ -f "$VM_KEY" ]] || ssh-keygen -q -t ed25519 -N "" -C "dra-vm" -f "$VM_KEY"

az deployment sub create \
    --name "dra-deployment" \
    --location centralindia \
    --template-file "$INFRA_DIR/main.bicep" \
    --parameters "$INFRA_DIR/parameters.json" \
    --parameters sqlAdminPassword="$SQL_ADMIN_PASSWORD" consolePasscode="$CONSOLE_PASSCODE" \
    --parameters notifyEmailTo="${NOTIFY_EMAIL_TO:-}" plantConfigGap="${PLANT_CONFIG_GAP:-true}" deployEmail="${DEPLOY_EMAIL:-false}" \
    --parameters vmSshPublicKey="$(cat "$VM_KEY.pub")" deployIaas="${DEPLOY_IAAS:-true}" \
    --parameters publicUrl="${PUBLIC_URL:-}" altPublicUrl="${ALT_PUBLIC_URL:-}" consoleAppName="${CONSOLE_APP_NAME:-azure-armada}" resendApiKey="${RESEND_API_KEY:-}" appAccessCode="$APP_ACCESS_CODE" \
    --output table

# Get deployment outputs
echo ""
echo "Fetching deployment outputs..."
OUTPUTS=$(az deployment sub show \
    --name dra-deployment \
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
echo "Secrets are written to Key Vault by the deployment itself."
echo "Next: run deploy-apps.sh to deploy application code"
echo "=========================================="
