// Key Vault for secrets (RBAC mode)
// Stores: sql-admin-password, console-passcode, acs-connection-string

@description('Location for resources')
param location string

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Tenant ID for RBAC')
param tenantId string = subscription().tenantId

@secure()
param sqlAdminPassword string

@secure()
param consolePasscode string

@secure()
@description('Resend API key for instant email (optional)')
param resendApiKey string = ''

var keyVaultName = 'dra-kv-${suffix}'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: {
    project: projectTag
  }
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: true
  }
}

@description('Key Vault ID')
resource sqlPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'sql-admin-password'
  properties: {
    value: sqlAdminPassword
  }
}

resource resendSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(resendApiKey)) {
  parent: keyVault
  name: 'resend-api-key'
  properties: {
    value: resendApiKey
  }
}

resource consolePasscodeSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'console-passcode'
  properties: {
    value: consolePasscode
  }
}

output keyVaultId string = keyVault.id

@description('Key Vault Name')
output keyVaultName string = keyVault.name

@description('Key Vault URI')
output keyVaultUri string = keyVault.properties.vaultUri
