// Role assignments for DR Console managed identity
// - Contributor on workload RG only
// - Storage Table Data Contributor on control storage
// - Storage Blob Data Contributor on control storage
// - Key Vault Secrets User on Key Vault
// - Cognitive Services OpenAI User on OpenAI resource

@description('Console App Principal ID')
param consolePrincipalId string

@description('Workload Resource Group name')
param workloadRgName string

@description('Storage Account ID')
param storageAccountId string

@description('Key Vault ID')
param keyVaultId string

@description('Azure OpenAI Resource ID (empty if using existing)')
param openAIId string

@description('Web App A Principal ID (for Key Vault access)')
param webAppAPrincipalId string

@description('Web App B Principal ID (for Key Vault access)')
param webAppBPrincipalId string

// Built-in role definition IDs
var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

// Console identity: Contributor on workload RG
// Note: This requires the workload RG to exist and be passed as a scope
// We use subscriptionResourceId to reference the RG
resource contributorOnWorkloadRg 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, workloadRgName, consolePrincipalId, contributorRoleId)
  scope: resourceGroup(workloadRgName)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
    principalId: consolePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Console identity: Storage Table Data Contributor
resource storageTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, consolePrincipalId, storageTableDataContributorRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalId: consolePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Console identity: Storage Blob Data Contributor
resource storageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, consolePrincipalId, storageBlobDataContributorRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: consolePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Console identity: Key Vault Secrets User
resource consoleKeyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, consolePrincipalId, keyVaultSecretsUserRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: consolePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Console identity: Cognitive Services OpenAI User (if OpenAI resource exists)
resource openAIUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(openAIId)) {
  name: guid(openAIId, consolePrincipalId, cognitiveServicesOpenAIUserRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: consolePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Web App A: Key Vault Secrets User (for SQL password reference)
resource webAppAKeyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, webAppAPrincipalId, keyVaultSecretsUserRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: webAppAPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Web App B: Key Vault Secrets User (for SQL password reference)
resource webAppBKeyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, webAppBPrincipalId, keyVaultSecretsUserRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: webAppBPrincipalId
    principalType: 'ServicePrincipal'
  }
}
