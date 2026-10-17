// Azure Communication Services + Email Communication Service
// Uses Azure-managed domain for sender address

@description('Location for Communication Services (must support Email)')
param location string

@description('Data location for Communication Services')
param dataLocation string = 'India'

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Key Vault name to store connection string')
param keyVaultName string

var acsName = 'dra-acs-${suffix}'
var emailName = 'dra-email-${suffix}'

// Azure Communication Services resource
resource communicationService 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: acsName
  location: 'global'
  tags: {
    project: projectTag
  }
  properties: {
    dataLocation: dataLocation
  }
}

// Email Communication Service
resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: emailName
  location: 'global'
  tags: {
    project: projectTag
  }
  properties: {
    dataLocation: dataLocation
  }
}

// Azure-managed domain for email sending
resource azureManagedDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  properties: {
    domainManagement: 'AzureManagedDomain'
    userEngagementTracking: 'Disabled'
  }
}

// Link Email Service to Communication Service
resource emailLink 'Microsoft.Communication/communicationServices/emailServices@2023-04-01' = {
  name: '${acsName}/${emailName}'
  dependsOn: [
    communicationService
    emailService
    azureManagedDomain
  ]
}

// Store connection string in Key Vault
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource acsConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'acs-connection-string'
  properties: {
    value: communicationService.listKeys().primaryConnectionString
  }
}

@description('Communication Services Resource ID')
output acsId string = communicationService.id

@description('Communication Services Name')
output acsName string = communicationService.name

@description('Email Service Resource ID')
output emailServiceId string = emailService.id

@description('Email Service Name')
output emailServiceName string = emailService.name

@description('Azure Managed Domain Name')
output domainName string = azureManagedDomain.properties.mailFromSenderDomain

@description('Sender Address (DoNotReply)')
output senderAddress string = 'DoNotReply@${azureManagedDomain.properties.mailFromSenderDomain}'
