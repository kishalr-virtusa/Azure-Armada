#!/bin/bash
# Black box knowledge index (RAG): Cosmos DB vector search + text-embedding-3-small. Deploys ONLY these, into the
# existing control resource group; main.bicep is not run.
#   1. capability EnableNoSQLVectorSearch on the existing Cosmos DB account (az cosmosdb update; takes a few minutes,
#      cannot be removed afterwards, no cost)
#   2. container `knowledge` with the vector policy (modules/knowledge.bicep; shares the free-tier 500 RU/s)
#   3. deployment text-embedding-3-small, GlobalStandard, 50K TPM (modules/embeddings.bicep; pay per token)
#   4. app setting EMBEDDING_DEPLOYMENT on the console (only if it differs; this restarts the app)
#
# Usage: ./deploy-rag.sh             what-if, then deploy (asks first)
#        ./deploy-rag.sh --what-if   only show the changes (read-only)
#        ./deploy-rag.sh --yes       no prompt
# Afterwards build the index once (from apps/dr-console, signed in with az login; needs Cosmos DB data contributor
# and Cognitive Services OpenAI User):
#        AZURE_OPENAI_ENDPOINT=https://<foundry>.openai.azure.com/ COSMOS_ENDPOINT=https://<cosmos>.documents.azure.com:443/ \
#          python -m dr_agent.knowledge reindex
# After that every drill report indexes its drill automatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
RG="${CONTROL_RG:-rg-dra-control}"
EXPECTED_SUB="Azure subscription 1"
EMBEDDING_DEPLOYMENT="${EMBEDDING_DEPLOYMENT:-text-embedding-3-small}"
MODE="${1:-}"
case "$MODE" in
    ""|--what-if|--yes) ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Error: unknown argument $MODE"; exit 1 ;;
esac

az account show &>/dev/null || { echo "Error: not logged in to Azure. Run 'az login' first."; exit 1; }
SUB_NAME=$(az account show --query name -o tsv)
[[ "$SUB_NAME" == "$EXPECTED_SUB" ]] || { echo "Error: active subscription is '$SUB_NAME'; run: az account set --subscription \"$EXPECTED_SUB\""; exit 1; }

COSMOS="${COSMOS_ACCOUNT:-$(az cosmosdb list -g "$RG" --query "[?starts_with(name, 'dra-cosmos-')].name | [0]" -o tsv)}"
FOUNDRY="${FOUNDRY_NAME:-$(az cognitiveservices account list -g "$RG" --query "[?starts_with(name, 'dra-foundry-')].name | [0]" -o tsv)}"
[[ -n "$COSMOS" ]] || { echo "Error: no dra-cosmos-* account in $RG (set COSMOS_ACCOUNT)"; exit 1; }
[[ -n "$FOUNDRY" ]] || { echo "Error: no dra-foundry-* account in $RG (set FOUNDRY_NAME)"; exit 1; }
CONSOLE_APP=$(az webapp list -g "$RG" --query "[?contains(name, 'armada') || starts_with(name, 'dra-console')].name | [0]" -o tsv)
echo "Cosmos DB: $RG/$COSMOS   Foundry: $RG/$FOUNDRY   Console: ${CONSOLE_APP:-(none found)}"

HAS_CAP=$(az cosmosdb show -g "$RG" -n "$COSMOS" --query "contains(capabilities[].name, 'EnableNoSQLVectorSearch')" -o tsv)
echo ""
echo "1. Vector search capability on $COSMOS: $([[ "$HAS_CAP" == "true" ]] && echo "already enabled" || echo "WILL BE ENABLED (az cosmosdb update --capabilities EnableNoSQLVectorSearch)")"
echo ""
echo "2. Knowledge container (what-if):"
# the container what-if needs the capability only at create time; what-if shows the planned container either way
az deployment group what-if -g "$RG" -n dra-knowledge -f "$INFRA_DIR/modules/knowledge.bicep" -p accountName="$COSMOS"
echo ""
echo "3. Embedding deployment (what-if):"
az deployment group what-if -g "$RG" -n dra-embeddings -f "$INFRA_DIR/modules/embeddings.bicep" \
    -p foundryName="$FOUNDRY" deploymentName="$EMBEDDING_DEPLOYMENT"
[[ "$MODE" == "--what-if" ]] && exit 0

if [[ "$MODE" != "--yes" ]]; then
    read -r -p "Apply these changes? [y/N] " ok
    [[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "Nothing changed."; exit 0; }
fi

if [[ "$HAS_CAP" != "true" ]]; then
    echo "Enabling EnableNoSQLVectorSearch on $COSMOS (a few minutes)..."
    az cosmosdb update -g "$RG" -n "$COSMOS" --capabilities EnableNoSQLVectorSearch -o none
    for _ in $(seq 1 60); do  # the capability must be active before a container with a vector policy is created
        [[ "$(az cosmosdb show -g "$RG" -n "$COSMOS" --query "contains(capabilities[].name, 'EnableNoSQLVectorSearch') && provisioningState == 'Succeeded'" -o tsv)" == "true" ]] && break
        sleep 20
    done
fi

echo "Creating the knowledge container..."
az deployment group create -g "$RG" -n dra-knowledge -f "$INFRA_DIR/modules/knowledge.bicep" -p accountName="$COSMOS" -o none

echo "Deploying $EMBEDDING_DEPLOYMENT..."
az deployment group create -g "$RG" -n dra-embeddings -f "$INFRA_DIR/modules/embeddings.bicep" \
    -p foundryName="$FOUNDRY" deploymentName="$EMBEDDING_DEPLOYMENT" -o none

if [[ -n "$CONSOLE_APP" ]]; then
    CURRENT=$(az webapp config appsettings list -g "$RG" -n "$CONSOLE_APP" --query "[?name=='EMBEDDING_DEPLOYMENT'].value | [0]" -o tsv)
    if [[ "$EMBEDDING_DEPLOYMENT" != "text-embedding-3-small" && "$CURRENT" != "$EMBEDDING_DEPLOYMENT" ]]; then
        echo "Setting EMBEDDING_DEPLOYMENT=$EMBEDDING_DEPLOYMENT on $CONSOLE_APP (the app restarts)..."
        az webapp config appsettings set -g "$RG" -n "$CONSOLE_APP" --settings "EMBEDDING_DEPLOYMENT=$EMBEDDING_DEPLOYMENT" -o none
    fi
fi

COSMOS_ENDPOINT=$(az cosmosdb show -g "$RG" -n "$COSMOS" --query documentEndpoint -o tsv)
echo ""
echo "Done. Build the index once (apps/dr-console, after deploy-apps.sh shipped the code):"
echo "  AZURE_OPENAI_ENDPOINT=https://$FOUNDRY.openai.azure.com/ COSMOS_ENDPOINT=$COSMOS_ENDPOINT \\"
echo "    python -m dr_agent.knowledge reindex"
echo "  python -m dr_agent.knowledge search \"failing back the database\" --source runbook   # smoke test"
echo "Kill switch: app setting KNOWLEDGE_ENABLED=false (search_knowledge then returns an error; indexing stops)."
