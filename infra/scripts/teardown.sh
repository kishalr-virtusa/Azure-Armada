#!/bin/bash
# Teardown DR Agent infrastructure
# Usage: ./teardown.sh

set -euo pipefail

WORKLOAD_RG="rg-dra-workload"
CONTROL_RG="rg-dra-control"

echo "=========================================="
echo "DR Agent Infrastructure Teardown"
echo "=========================================="
echo ""
echo "This will delete the following resource groups:"
echo "  - $WORKLOAD_RG"
echo "  - $CONTROL_RG"
echo ""
echo "ALL RESOURCES IN THESE GROUPS WILL BE PERMANENTLY DELETED."
echo ""

read -p "Are you sure you want to proceed? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Deleting resource groups (this may take several minutes)..."

# Delete workload RG first (has dependencies)
if az group show --name "$WORKLOAD_RG" &>/dev/null; then
    echo "Deleting $WORKLOAD_RG..."
    az group delete --name "$WORKLOAD_RG" --yes --no-wait
else
    echo "$WORKLOAD_RG does not exist, skipping."
fi

# Delete control RG
if az group show --name "$CONTROL_RG" &>/dev/null; then
    echo "Deleting $CONTROL_RG..."
    az group delete --name "$CONTROL_RG" --yes --no-wait
else
    echo "$CONTROL_RG does not exist, skipping."
fi

echo ""
echo "Resource group deletion initiated."
echo "Run 'az group list --query \"[?starts_with(name, 'rg-dra')]\"' to check status."
echo ""
echo "Teardown complete."
