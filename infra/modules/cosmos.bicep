// Azure Cosmos DB (NoSQL API): the DR console's own records (drills, timelines, approvals, Autopilot runs,
// notifications, Black box conversations). Drill report files stay in Blob storage.
//   - free tier: 1000 RU/s + 25 GB free for the life of the account (one free-tier account per subscription;
//     it can only be chosen at creation). 500 RU/s shared by the containers x 2 regions = the free 1000 RU/s.
//   - two regions: a primary with the control plane, a replica in a second region, so the agent's evidence
//     survives a regional outage (automatic failover on)
//   - no keys: local auth is disabled; the console signs in with its managed identity (data-plane RBAC)
//   - continuous backup, 7 days (free): point-in-time restore

@description('Primary (write) region')
param location string

@description('Replica region')
param secondaryLocation string

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Principal id of the DR console managed identity (data contributor); empty to skip')
param consolePrincipalId string = ''

@description('Principal id of the operator running migrations (data contributor); empty to skip')
param operatorPrincipalId string = ''

@description('Use the free tier (only one account per subscription can have it)')
param enableFreeTier bool = true

@description('Shared database throughput (RU/s). With two regions, 500 stays within the free tier.')
param throughput int = 500

var accountName = 'dra-cosmos-${suffix}'
var databaseName = 'dra'
// every container is partitioned by /pk: a drill id, a feed id or a conversation id
var containers = [
  'exercises'
  'events'
  'notifications'
  'heartbeats'
  'conversations'
]
// Cosmos DB Built-in Data Contributor (data plane: read/write items, no account or key access)
var dataContributor = '00000000-0000-0000-0000-000000000002'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  tags: {
    project: projectTag
  }
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: enableFreeTier
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
      {
        locationName: secondaryLocation
        failoverPriority: 1
        isZoneRedundant: false
      }
    ]
    enableAutomaticFailover: true
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Enabled'
    backupPolicy: {
      type: 'Continuous'
      continuousModeProperties: {
        tier: 'Continuous7Days'
      }
    }
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
    options: {
      throughput: throughput
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for c in containers: {
  parent: database
  name: c
  properties: {
    resource: {
      id: c
      partitionKey: {
        paths: [
          '/pk'
        ]
        kind: 'Hash'
      }
    }
  }
}]

resource consoleAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(consolePrincipalId)) {
  parent: account
  name: guid(account.id, consolePrincipalId, dataContributor)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributor}'
    principalId: consolePrincipalId
    scope: account.id
  }
}

resource operatorAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(operatorPrincipalId)) {
  parent: account
  name: guid(account.id, operatorPrincipalId, dataContributor)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributor}'
    principalId: operatorPrincipalId
    scope: account.id
  }
}

output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = databaseName
