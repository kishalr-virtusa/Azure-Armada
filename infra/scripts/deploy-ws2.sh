#!/usr/bin/env bash
# WS2 guardrails: content filter "dra-guardrails" (DefaultV2 + Prompt Shields Indirect Attack, blocking) on both model
# deployments, and capacity gpt-4.1-mini 30 -> 200K TPM, gpt-5-mini 50 -> 100K TPM (pay per token: no added cost).
# A targeted deployment of modules/guardrails.bicep against the existing Foundry resource; main.bicep is untouched.
#
# Usage: ./deploy-ws2.sh            what-if, then deploy (asks first)
#        ./deploy-ws2.sh --what-if  only show the changes
#        ./deploy-ws2.sh --yes      deploy without asking
# The deployments are re-PUT: run it outside rehearsal windows; ask the Black box one question afterwards.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/../modules/guardrails.bicep"
RG="${CONTROL_RG:-rg-dra-control}"
MODE="${1:-}"

FOUNDRY="${FOUNDRY_NAME:-$(az cognitiveservices account list -g "$RG" --query "[?starts_with(name, 'dra-foundry-')].name | [0]" -o tsv)}"
[[ -n "$FOUNDRY" ]] || { echo "No dra-foundry-* account in $RG (set FOUNDRY_NAME)"; exit 1; }
PARAMS=(foundryName="$FOUNDRY" capacity="${CAPACITY:-200}" reasoningCapacity="${REASONING_CAPACITY:-100}")
echo "Foundry resource: $RG/$FOUNDRY"

az deployment group what-if -g "$RG" -n dra-guardrails -f "$TEMPLATE" -p "${PARAMS[@]}"
[[ "$MODE" == "--what-if" ]] && exit 0
if [[ "$MODE" != "--yes" ]]; then
  read -r -p "Deploy these changes? [y/N] " ok
  [[ "$ok" == [yY] ]] || { echo "Cancelled."; exit 0; }
fi
az deployment group create -g "$RG" -n dra-guardrails -f "$TEMPLATE" -p "${PARAMS[@]}" -o none

echo "Deployments now:"
az cognitiveservices account deployment list -g "$RG" -n "$FOUNDRY" \
  --query "[].{name:name, capacity:sku.capacity, contentFilter:properties.raiPolicyName}" -o table
SUB=$(az account show --query id -o tsv)
echo "dra-guardrails filters:"
az rest --method get -o tsv \
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY/raiPolicies/dra-guardrails?api-version=2025-06-01" \
  --query "properties.contentFilters[?source=='Prompt' && (name=='Jailbreak' || name=='Indirect Attack')].[name, blocking, enabled]"
