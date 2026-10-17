#!/usr/bin/env bash
# Microsoft Entra ID sign-in for the DR console (App Service built-in authentication, "Easy Auth").
#   entra-auth.sh                 idempotent: app registration, assignment-only access for the signed-in user,
#                                 client secret in Key Vault, authsettingsV2 on the console (public phone paths excluded)
#   entra-auth.sh add-user <upn>  allow another user (teammate) to sign in to the console
#   entra-auth.sh disable         turn Entra sign-in off (console falls back to the passcode)
# Approvals in the console are then recorded under the signed-in Entra identity.
set -euo pipefail

CMD="${1:-enable}"
CONSOLE_RG="${CONTROL_RG:-rg-dra-control}"
CONSOLE_APP="${CONSOLE_APP_NAME:-azure-armada}"
DISPLAY_NAME="DR Agent Console (Azure Armada)"
SECRETS_FILE="${DRA_SECRETS_FILE:-$HOME/.dra-secrets}"
TENANT=$(az account show --query tenantId -o tsv)
SUB=$(az account show --query id -o tsv)
SITE="/subscriptions/$SUB/resourceGroups/$CONSOLE_RG/providers/Microsoft.Web/sites/$CONSOLE_APP"
HOST="$CONSOLE_APP.azurewebsites.net"
GRAPH="https://graph.microsoft.com/v1.0"

app_id() { az ad app list --display-name "$DISPLAY_NAME" --query "[0].appId" -o tsv; }

assign_user() { # upn
  local upn=$1 sp uid
  sp=$(az ad sp show --id "$(app_id)" --query id -o tsv)
  uid=$(az ad user show --id "$upn" --query id -o tsv)
  if az rest --method get --url "$GRAPH/servicePrincipals/$sp/appRoleAssignedTo" --query "value[?principalId=='$uid'] | [0].id" -o tsv | grep -q .; then
    echo "  $upn already allowed"
  else
    az rest --method post --url "$GRAPH/servicePrincipals/$sp/appRoleAssignedTo" -o none \
      --body "{\"principalId\":\"$uid\",\"resourceId\":\"$sp\",\"appRoleId\":\"00000000-0000-0000-0000-000000000000\"}"
    echo "  $upn allowed to sign in"
  fi
}

enable() {
  echo "Entra ID sign-in for https://$HOST"
  local app kv secret
  app=$(app_id)
  if [[ -z "$app" ]]; then
    app=$(az ad app create --display-name "$DISPLAY_NAME" --sign-in-audience AzureADMyOrg \
      --web-redirect-uris "https://$HOST/.auth/login/aad/callback" --enable-id-token-issuance true --query appId -o tsv)
    echo "  app registration created ($app)"
  else
    az ad app update --id "$app" --web-redirect-uris "https://$HOST/.auth/login/aad/callback" --enable-id-token-issuance true
    echo "  app registration exists ($app)"
  fi
  az ad sp show --id "$app" -o none 2>/dev/null || { az ad sp create --id "$app" -o none; sleep 10; }
  az ad sp update --id "$app" --set appRoleAssignmentRequired=true -o none   # only assigned users can sign in
  assign_user "$(az ad signed-in-user show --query userPrincipalName -o tsv)"

  # client secret lives in Key Vault; regenerated only when missing (e.g. after down.sh purged the vault)
  kv=$(az keyvault list -g "$CONSOLE_RG" --query "[0].name" -o tsv)
  if ! az keyvault secret show --vault-name "$kv" -n entra-client-secret -o none 2>/dev/null; then
    secret=$(az ad app credential reset --id "$app" --append --display-name "easyauth-$(date +%Y%m%d)" --years 1 --query password -o tsv)
    # the deploying user needs data-plane rights to write the secret (Owner has none by default on RBAC vaults)
    local me kvid; me=$(az ad signed-in-user show --query id -o tsv); kvid=$(az keyvault show -n "$kv" --query id -o tsv)
    az role assignment create --assignee-object-id "$me" --assignee-principal-type User \
      --role "Key Vault Secrets Officer" --scope "$kvid" -o none 2>/dev/null || true
    for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
      az keyvault secret set --vault-name "$kv" -n entra-client-secret --value "$secret" -o none 2>/dev/null && break
      sleep 15  # RBAC propagation
    done
    echo "  client secret stored in Key Vault ($kv/entra-client-secret)"
  fi

  az webapp config appsettings set -g "$CONSOLE_RG" -n "$CONSOLE_APP" -o none --settings \
    ENTRA_AUTH_ENABLED=true ENTRA_CLIENT_ID="$app" \
    "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET=@Microsoft.KeyVault(VaultName=$kv;SecretName=entra-client-secret)"

  az rest --method put --url "https://management.azure.com$SITE/config/authsettingsV2?api-version=2024-04-01" -o none --body "{
    \"properties\": {
      \"platform\": {\"enabled\": true},
      \"globalValidation\": {
        \"requireAuthentication\": true,
        \"unauthenticatedClientAction\": \"RedirectToLoginPage\",
        \"redirectToProvider\": \"azureactivedirectory\",
        \"excludedPaths\": [\"/health\", \"/public/live\", \"/public/access-mode\", \"/pay\", \"/go\", \"/static\"]
      },
      \"identityProviders\": {\"azureActiveDirectory\": {\"enabled\": true, \"registration\": {
        \"clientId\": \"$app\",
        \"clientSecretSettingName\": \"MICROSOFT_PROVIDER_AUTHENTICATION_SECRET\",
        \"openIdIssuer\": \"https://sts.windows.net/$TENANT/v2.0\"},
        \"validation\": {\"allowedAudiences\": [\"api://$app\", \"$app\"]}}},
      \"login\": {\"tokenStore\": {\"enabled\": true}, \"preserveUrlFragmentsForLogins\": true},
      \"httpSettings\": {\"requireHttps\": true}
    }}"
  grep -q "^export ENTRA_CLIENT_ID=" "$SECRETS_FILE" 2>/dev/null || echo "export ENTRA_CLIENT_ID=$app" >> "$SECRETS_FILE"
  echo "  console now requires Microsoft Entra ID sign-in (phone endpoints stay public)"
}

disable() {
  az rest --method put --url "https://management.azure.com$SITE/config/authsettingsV2?api-version=2024-04-01" -o none \
    --body '{"properties":{"platform":{"enabled":false}}}'
  az webapp config appsettings set -g "$CONSOLE_RG" -n "$CONSOLE_APP" --settings ENTRA_AUTH_ENABLED=false -o none
  echo "Entra sign-in disabled (passcode login is active)"
}

case "$CMD" in
  enable) enable ;;
  add-user) assign_user "${2:?usage: $0 add-user <user@tenant>}" ;;
  disable) disable ;;
  *) echo "usage: $0 [enable|add-user <upn>|disable]"; exit 2 ;;
esac
