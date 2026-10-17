#!/usr/bin/env bash
# Azure Site Recovery (Azure-to-Azure) replication for the IaaS "reports" VM: Region A -> Region B.
#   asr.sh enable    idempotent: fabrics, containers, policy, container + network mappings, protected item
#   asr.sh status    replication health of the protected VM
#   asr.sh disable   removes replication and the ASR configuration so the vault/RG can be deleted (used by down.sh)
# Initial replication takes ~30-60 min after `enable`; the DR Agent's readiness check reports its progress.
set -euo pipefail

CMD="${1:-enable}"
SUB=$(az account show --query id -o tsv)
RG="${WORKLOAD_RG:-rg-dra-workload}"
SFX=$(az deployment sub show -n dra-deployment --query properties.outputs.suffix.value -o tsv 2>/dev/null || true)
[[ -n "$SFX" ]] || SFX=$(az resource list -g "$RG" --resource-type Microsoft.RecoveryServices/vaults --query "[0].name" -o tsv | sed 's/^dra-rsv-//')
API="api-version=2024-04-01"
LOC_A="${LOCATION_A:-centralindia}"
LOC_B="${LOCATION_B:-southeastasia}"

VAULT="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.RecoveryServices/vaults/dra-rsv-$SFX"
RGID="/subscriptions/$SUB/resourceGroups/$RG"
VM="dra-vm-reports"
FAB_A="asr-a2a-$LOC_A";            FAB_B="asr-a2a-$LOC_B"
PC_A="$FAB_A-container";           PC_B="$FAB_B-container"
POLICY="dra-a2a-policy"
MAP="a-to-b-mapping"
NETMAP="a-to-b-network"
PI_PATH="$VAULT/replicationFabrics/$FAB_A/replicationProtectionContainers/$PC_A/replicationProtectedItems/$VM"
MGMT="https://management.azure.com"

get()  { az rest --method get --url "$MGMT$1?$API" 2>/dev/null || true; }
exists() { [[ -n "$(get "$1")" ]]; }
put()  { az rest --method put --url "$MGMT$1?$API" --body "$2" -o none; }

wait_for() { # path jq-expression description [timeout_s]
  local path=$1 expr=$2 what=$3 timeout=${4:-600} t=0
  until [[ "$(get "$path" | jq -r "$expr" 2>/dev/null)" == "true" ]]; do
    (( t >= timeout )) && { echo "  timed out waiting for $what"; return 1; }
    sleep 10; t=$((t + 10))
  done
  echo "  $what ready"
}

ensure() { # path body description [ready-jq]
  local path=$1 body=$2 what=$3 ready=${4:-'.id != null'}
  if exists "$path"; then echo "  $what exists"; else echo "  creating $what"; put "$path" "$body"; fi
  wait_for "$path" "$ready" "$what"
}

enable() {
  echo "ASR: enabling replication of $VM ($LOC_A -> $LOC_B) in vault dra-rsv-$SFX"
  local vm_id="$RGID/providers/Microsoft.Compute/virtualMachines/$VM"
  local disk_id; disk_id=$(az vm show -g "$RG" -n "$VM" --query storageProfile.osDisk.managedDisk.id -o tsv)
  local cache_id="$RGID/providers/Microsoft.Storage/storageAccounts/draasrcache$SFX"
  local vnet_a="$RGID/providers/Microsoft.Network/virtualNetworks/dra-vnet-cin-$SFX"
  local vnet_b="$RGID/providers/Microsoft.Network/virtualNetworks/dra-vnet-sea-$SFX"

  ensure "$VAULT/replicationFabrics/$FAB_A" \
    "{\"properties\":{\"customDetails\":{\"instanceType\":\"Azure\",\"location\":\"$LOC_A\"}}}" "fabric A"
  ensure "$VAULT/replicationFabrics/$FAB_B" \
    "{\"properties\":{\"customDetails\":{\"instanceType\":\"Azure\",\"location\":\"$LOC_B\"}}}" "fabric B"
  ensure "$VAULT/replicationFabrics/$FAB_A/replicationProtectionContainers/$PC_A" \
    '{"properties":{"providerSpecificInput":[{"instanceType":"A2A"}]}}' "protection container A"
  ensure "$VAULT/replicationFabrics/$FAB_B/replicationProtectionContainers/$PC_B" \
    '{"properties":{"providerSpecificInput":[{"instanceType":"A2A"}]}}' "protection container B"
  ensure "$VAULT/replicationPolicies/$POLICY" \
    '{"properties":{"providerSpecificInput":{"instanceType":"A2A","recoveryPointHistory":1440,"crashConsistentFrequencyInMinutes":5,"appConsistentFrequencyInMinutes":0,"multiVmSyncStatus":"Disable"}}}' \
    "replication policy"
  ensure "$VAULT/replicationFabrics/$FAB_A/replicationProtectionContainers/$PC_A/replicationProtectionContainerMappings/$MAP" \
    "{\"properties\":{\"targetProtectionContainerId\":\"$VAULT/replicationFabrics/$FAB_B/replicationProtectionContainers/$PC_B\",\"policyId\":\"$VAULT/replicationPolicies/$POLICY\",\"providerSpecificInput\":{\"instanceType\":\"A2A\",\"agentAutoUpdateStatus\":\"Disabled\"}}}" \
    "container mapping A->B" '.properties.state == "Paired"'
  ensure "$VAULT/replicationFabrics/$FAB_A/replicationNetworks/azureNetwork/replicationNetworkMappings/$NETMAP" \
    "{\"properties\":{\"recoveryFabricName\":\"$FAB_B\",\"recoveryNetworkId\":\"$vnet_b\",\"fabricSpecificDetails\":{\"instanceType\":\"AzureToAzure\",\"primaryNetworkId\":\"$vnet_a\"}}}" \
    "network mapping A->B"
  ensure "$PI_PATH" \
    "{\"properties\":{\"policyId\":\"$VAULT/replicationPolicies/$POLICY\",\"providerSpecificDetails\":{\"instanceType\":\"A2A\",\"fabricObjectId\":\"$vm_id\",\"recoveryContainerId\":\"$VAULT/replicationFabrics/$FAB_B/replicationProtectionContainers/$PC_B\",\"recoveryResourceGroupId\":\"$RGID\",\"recoveryAzureNetworkId\":\"$vnet_b\",\"recoverySubnetName\":\"default\",\"vmManagedDisks\":[{\"diskId\":\"$disk_id\",\"primaryStagingAzureStorageAccountId\":\"$cache_id\",\"recoveryResourceGroupId\":\"$RGID\",\"recoveryReplicaDiskAccountType\":\"Standard_LRS\",\"recoveryTargetDiskAccountType\":\"StandardSSD_LRS\"}]}}}" \
    "protected item $VM (initial replication continues in the background)" '.properties.protectionState != null'
  status
}

status() {
  get "$PI_PATH" | jq '{protectionState: .properties.protectionStateDescription, health: .properties.replicationHealth,
    testFailover: .properties.testFailoverStateDescription, rpoSeconds: .properties.providerSpecificDetails.lastRpoCalculatedTime,
    progress: .properties.providerSpecificDetails.monitoringPercentageCompletion}'
}

disable() {
  echo "ASR: removing replication + configuration from dra-rsv-$SFX"
  [[ -n "$SFX" ]] && exists "$VAULT" || { echo "  no vault, nothing to do"; return 0; }
  if exists "$PI_PATH"; then
    # cleanup a pending test failover first, then purge the protected item (fast, no waiting for the agent)
    az rest --method post --url "$MGMT$PI_PATH/testFailoverCleanup?$API" --body '{"properties":{"comments":"teardown"}}' -o none 2>/dev/null || true
    echo "  purging protected item"; az rest --method delete --url "$MGMT$PI_PATH?$API" -o none || true
    local t=0; while exists "$PI_PATH" && (( t < 900 )); do sleep 15; t=$((t + 15)); done
  fi
  for p in "$VAULT/replicationFabrics/$FAB_A/replicationNetworks/azureNetwork/replicationNetworkMappings/$NETMAP" \
           "$VAULT/replicationFabrics/$FAB_A/replicationProtectionContainers/$PC_A/replicationProtectionContainerMappings/$MAP"; do
    exists "$p" && { echo "  deleting ${p##*/}"; az rest --method delete --url "$MGMT$p?$API" -o none || true; }
  done
  sleep 20
  for f in "$FAB_A" "$FAB_B"; do
    exists "$VAULT/replicationFabrics/$f" && { echo "  deleting fabric $f"; az rest --method delete --url "$MGMT$VAULT/replicationFabrics/$f?$API" -o none || true; }
  done
  local t=0; while { exists "$VAULT/replicationFabrics/$FAB_A" || exists "$VAULT/replicationFabrics/$FAB_B"; } && (( t < 600 )); do sleep 15; t=$((t + 15)); done
  echo "  ASR configuration removed"
}

case "$CMD" in
  enable) enable ;;
  status) status ;;
  disable) disable ;;
  *) echo "usage: $0 enable|status|disable"; exit 2 ;;
esac
