#!/bin/bash
# Smoke test: verify Traffic Manager, both regions, and FOG status
# Usage: ./smoke.sh

set -euo pipefail

echo "=========================================="
echo "DR Agent Smoke Test"
echo "=========================================="
echo ""

# Get deployment outputs
OUTPUTS=$(az deployment sub show \
    --name dra-deployment \
    --query properties.outputs -o json 2>/dev/null || echo "{}")

if [[ "$OUTPUTS" == "{}" ]]; then
    echo "Error: No deployment found. Run deploy.sh first."
    exit 1
fi

TM_FQDN=$(echo "$OUTPUTS" | jq -r '.trafficManagerFqdn.value')
WEBAPP_A_URL=$(echo "$OUTPUTS" | jq -r '.webAppAUrl.value')
WEBAPP_B_URL=$(echo "$OUTPUTS" | jq -r '.webAppBUrl.value')
CONSOLE_URL=$(echo "$OUTPUTS" | jq -r '.consoleUrl.value')
FOG_LISTENER=$(echo "$OUTPUTS" | jq -r '.sqlFogListener.value')
SUFFIX=$(echo "$OUTPUTS" | jq -r '.suffix.value')

FOG_NAME="dra-fog-${SUFFIX}"
SQL_SERVER_A="dra-sql-eas-${SUFFIX}"

echo "Testing endpoints..."
echo ""

# Test Traffic Manager
echo "1. Traffic Manager (http://${TM_FQDN}/health):"
TM_RESULT=$(curl -s -w "\n%{http_code}" "http://${TM_FQDN}/health" 2>/dev/null || echo -e "\n000")
TM_BODY=$(echo "$TM_RESULT" | head -n -1)
TM_CODE=$(echo "$TM_RESULT" | tail -n 1)
if [[ "$TM_CODE" == "200" ]]; then
    echo "   ✅ HTTP 200 - $(echo "$TM_BODY" | jq -r '.region // "unknown"')"
else
    echo "   ❌ HTTP $TM_CODE"
fi

# Test Region A directly
echo ""
echo "2. Region A direct (${WEBAPP_A_URL}/health):"
A_RESULT=$(curl -s -w "\n%{http_code}" "${WEBAPP_A_URL}/health" 2>/dev/null || echo -e "\n000")
A_BODY=$(echo "$A_RESULT" | head -n -1)
A_CODE=$(echo "$A_RESULT" | tail -n 1)
if [[ "$A_CODE" == "200" ]]; then
    echo "   ✅ HTTP 200 - $(echo "$A_BODY" | jq -r '.region // "unknown"')"
else
    echo "   ❌ HTTP $A_CODE"
fi

# Test Region B directly
echo ""
echo "3. Region B direct (${WEBAPP_B_URL}/health):"
B_RESULT=$(curl -s -w "\n%{http_code}" "${WEBAPP_B_URL}/health" 2>/dev/null || echo -e "\n000")
B_BODY=$(echo "$B_RESULT" | head -n -1)
B_CODE=$(echo "$B_RESULT" | tail -n 1)
if [[ "$B_CODE" == "200" ]]; then
    echo "   ✅ HTTP 200 - $(echo "$B_BODY" | jq -r '.region // "unknown"')"
else
    echo "   ❌ HTTP $B_CODE"
fi

# Test Console
echo ""
echo "4. Console (${CONSOLE_URL}):"
C_RESULT=$(curl -s -w "\n%{http_code}" "${CONSOLE_URL}" 2>/dev/null || echo -e "\n000")
C_CODE=$(echo "$C_RESULT" | tail -n 1)
if [[ "$C_CODE" == "200" ]] || [[ "$C_CODE" == "302" ]]; then
    echo "   ✅ HTTP $C_CODE"
else
    echo "   ❌ HTTP $C_CODE"
fi

# Check FOG status
echo ""
echo "5. SQL Failover Group status:"
FOG_STATUS=$(az sql failover-group show \
    --resource-group "rg-dra-workload" \
    --server "$SQL_SERVER_A" \
    --name "$FOG_NAME" \
    --query "{role:replicationRole, state:replicationState}" \
    -o json 2>/dev/null || echo '{"role":"unknown","state":"unknown"}')

FOG_ROLE=$(echo "$FOG_STATUS" | jq -r '.role')
FOG_STATE=$(echo "$FOG_STATUS" | jq -r '.state')
if [[ "$FOG_STATE" == "CATCH_UP" ]] || [[ "$FOG_STATE" == "SEEDING" ]]; then
    echo "   ⏳ Role: $FOG_ROLE, State: $FOG_STATE (replication in progress)"
elif [[ "$FOG_ROLE" == "Primary" ]]; then
    echo "   ✅ Role: $FOG_ROLE, State: $FOG_STATE"
else
    echo "   ⚠️  Role: $FOG_ROLE, State: $FOG_STATE"
fi

echo ""
echo "=========================================="
echo "Smoke test complete."
echo "=========================================="
