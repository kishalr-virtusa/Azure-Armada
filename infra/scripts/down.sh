#!/usr/bin/env bash
# ONE COMMAND to remove the whole DR Agent solution (stops all costs).
#   ./infra/scripts/down.sh          # asks for confirmation
#   ./infra/scripts/down.sh --yes    # no prompt
# Deletes both resource groups, then purges the soft-deleted Key Vault and Azure OpenAI account
# so the next up.sh can reuse the same names. The subscription budget alert is kept.
set -euo pipefail
RGS=(rg-dra-workload rg-dra-control rg-dra-recovery)
[[ -n "${SUB_ID:-}" ]] && az account set --subscription "$SUB_ID"
echo "Subscription: $(az account show --query '[name, id]' -o tsv | paste -sd' ')"
if [[ "${1:-}" != "--yes" ]]; then
  read -r -p "Delete ${RGS[*]} and ALL their resources? (yes/no): " c
  [[ "$c" == "yes" ]] || { echo "Aborted."; exit 0; }
fi

for rg in "${RGS[@]}"; do
  if [[ "$(az group exists -n "$rg")" == "true" ]]; then
    echo "Deleting $rg ..."
    az group delete -n "$rg" --yes --no-wait
  fi
done
for rg in "${RGS[@]}"; do
  while [[ "$(az group exists -n "$rg")" == "true" ]]; do echo "  waiting for $rg ..."; sleep 20; done
  echo "$rg deleted"
done

echo "Purging soft-deleted Key Vaults / OpenAI accounts created by this project ..."
for kv in $(az keyvault list-deleted --query "[?starts_with(name,'dra-kv-')].name" -o tsv); do
  echo "  purge Key Vault $kv"; az keyvault purge -n "$kv" -o none || true
done
for row in $(az cognitiveservices account list-deleted --query "[?(starts_with(name,'dra-openai-') || starts_with(name,'dra-foundry-'))].[name,location]" -o tsv | tr '\t' '|'); do
  name="${row%%|*}"; loc="${row##*|}"
  echo "  purge OpenAI $name ($loc)"
  az cognitiveservices account purge -n "$name" -l "$loc" -g rg-dra-control -o none || true
done
echo "DOWN. Nothing from this project is running. (Budget alert 'dra-budget' is kept.)"
