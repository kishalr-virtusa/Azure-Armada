#!/bin/bash
# Seed secrets into Key Vault
# Usage: ./seed-secrets.sh
# Requires: SQL_ADMIN_PASSWORD, CONSOLE_PASSCODE env vars

set -euo pipefail

# Get Key Vault name from deployment
OUTPUTS=$(az deployment sub show \
    --name "$(az deployment sub list --query '[0].name' -o tsv)" \
    --query properties.outputs -o json 2>/dev/null || echo "{}")

if [[ "$OUTPUTS" == "{}" ]]; then
    echo "Error: No deployment found. Run deploy.sh first."
    exit 1
fi

KV_NAME=$(echo "$OUTPUTS" | jq -r '.keyVaultName.value')

echo "Seeding secrets to Key Vault: $KV_NAME"

# SQL Admin Password
if [[ -n "${SQL_ADMIN_PASSWORD:-}" ]]; then
    echo "Setting sql-admin-password..."
    az keyvault secret set --vault-name "$KV_NAME" --name "sql-admin-password" --value "$SQL_ADMIN_PASSWORD" --output none
else
    echo "Warning: SQL_ADMIN_PASSWORD not set, skipping."
fi

# Console Passcode
if [[ -n "${CONSOLE_PASSCODE:-}" ]]; then
    echo "Setting console-passcode..."
    az keyvault secret set --vault-name "$KV_NAME" --name "console-passcode" --value "$CONSOLE_PASSCODE" --output none
else
    echo "Warning: CONSOLE_PASSCODE not set, skipping."
fi

# ACS connection string is set by the Bicep deployment via email.bicep

echo ""
echo "Secrets seeded successfully."
echo "Note: ACS connection string is automatically set during infrastructure deployment."
