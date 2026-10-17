// IaaS tier protected by Azure Site Recovery (ASR): a small "reports" VM in Region A, replicated to Region B.
// The DR Agent validates it with an ASR *test failover* into an isolated network (non-disruptive).
// Replication itself (fabrics, containers, policy, mappings, protected item) is configured by
// infra/scripts/asr.sh because those resources are asynchronous and must be created in order.

@description('Region A (source)')
param locationA string

@description('Region B (recovery)')
param locationB string

@description('Resource suffix')
param suffix string

@description('Project tag')
param projectTag string = 'dr-agent'

@description('SSH public key for the VM admin (login is never needed; Run Command is used)')
param sshPublicKey string

@description('VM size (Free Trial: B-series, 4 cores per region; ~$0.01/h)')
param vmSize string = 'Standard_B2ts_v2' // B1s often has no capacity in centralindia

var tags = { project: projectTag, tier: 'iaas' }
var vmName = 'dra-vm-reports'

// cloud-init: tiny "reports" service on :8080 with /health reporting the hostname and region (IMDS)
var cloudInit = '''#cloud-config
write_files:
  - path: /opt/reports/app.py
    permissions: '0755'
    content: |
      import json, socket, urllib.request
      from http.server import BaseHTTPRequestHandler, HTTPServer
      def region():
          try:
              req = urllib.request.Request("http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text", headers={"Metadata": "true"})
              return urllib.request.urlopen(req, timeout=2).read().decode()
          except Exception:
              return "unknown"
      class H(BaseHTTPRequestHandler):
          def do_GET(self):
              body = json.dumps({"service": "contoso-reports", "status": "ok", "host": socket.gethostname(), "region": region()}).encode()
              self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
          def log_message(self, *a): pass
      HTTPServer(("0.0.0.0", 8080), H).serve_forever()
  - path: /etc/systemd/system/reports.service
    content: |
      [Unit]
      Description=Contoso reports service
      After=network.target
      [Service]
      ExecStart=/usr/bin/python3 /opt/reports/app.py
      Restart=always
      [Install]
      WantedBy=multi-user.target
runcmd:
  - systemctl daemon-reload
  - systemctl enable --now reports.service
'''

// ---------------------------------------------------------------- Region A: source VM
resource vnetA 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'dra-vnet-cin-${suffix}'
  location: locationA
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.10.0.0/16' ] }
    subnets: [
      {
        name: 'default'
        properties: {
          addressPrefix: '10.10.1.0/24'
          // ASR's mobility agent needs outbound access to Azure endpoints (no NAT gateway: cost)
          defaultOutboundAccess: true
        }
      }
    ]
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${vmName}-nic'
  location: locationA
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: { id: vnetA.properties.subnets[0].id }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: locationA
  tags: tags
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: 'reports'
      adminUsername: 'draadmin'
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/draadmin/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        // Ubuntu 22.04 (kernel 6.8): the 24.04 image now ships kernel 7.0, which the ASR mobility agent does not support yet
        publisher: 'Canonical'
        offer: '0001-com-ubuntu-server-jammy'
        sku: '22_04-lts-gen2'
        version: 'latest'
      }
      osDisk: {
        name: '${vmName}-osdisk'
        createOption: 'FromImage'
        diskSizeGB: 30
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Delete'
      }
    }
    networkProfile: {
      networkInterfaces: [ { id: nic.id, properties: { deleteOption: 'Delete' } } ]
    }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } } // managed boot diagnostics (free)
  }
}

// ASR cache storage must be in the source region
resource cache 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'draasrcache${suffix}'
  location: locationA
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ---------------------------------------------------------------- Region B: vault + recovery networks
resource vault 'Microsoft.RecoveryServices/vaults@2024-04-01' = {
  name: 'dra-rsv-${suffix}'
  location: locationB
  tags: tags
  sku: { name: 'RS0', tier: 'Standard' }
  properties: {
    publicNetworkAccess: 'Enabled'
  }
}

resource vnetB 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'dra-vnet-sea-${suffix}'
  location: locationB
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.20.0.0/16' ] }
    subnets: [ { name: 'default', properties: { addressPrefix: '10.20.1.0/24' } } ]
  }
}

// isolated network for test failovers: no peering, no route to production
resource vnetTest 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'dra-vnet-sea-test-${suffix}'
  location: locationB
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.30.0.0/16' ] }
    subnets: [ { name: 'default', properties: { addressPrefix: '10.30.1.0/24' } } ]
  }
}

output vmName string = vm.name
output vmId string = vm.id
output osDiskId string = vm.properties.storageProfile.osDisk.managedDisk.id
output vaultName string = vault.name
output cacheStorageId string = cache.id
output vnetAId string = vnetA.id
output vnetBId string = vnetB.id
output vnetTestId string = vnetTest.id
