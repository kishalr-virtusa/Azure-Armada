// Storage Account for exercise state (Tables) and reports (Blob)
// Tables: exercises, events, heartbeats, notifications
// Container: reports

@description('Location for resources')
param location string

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

// Storage account names must be 3-24 chars, lowercase alphanumeric only
var storageAccountName = 'drastorage${suffix}'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  tags: {
    project: projectTag
  }
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource exercisesTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
  parent: tableService
  name: 'exercises'
}

resource eventsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
  parent: tableService
  name: 'events'
}

resource heartbeatsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
  parent: tableService
  name: 'heartbeats'
}

resource notificationsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
  parent: tableService
  name: 'notifications'
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource reportsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'reports'
  properties: {
    publicAccess: 'None'
  }
}

@description('Storage Account ID')
output storageAccountId string = storageAccount.id

@description('Storage Account Name')
output storageAccountName string = storageAccount.name

@description('Table Service Endpoint')
output tableEndpoint string = storageAccount.properties.primaryEndpoints.table

@description('Blob Service Endpoint')
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
