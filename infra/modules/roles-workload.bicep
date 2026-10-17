// Console identity: Contributor on the WORKLOAD resource group only (deployed at workload RG scope)
@description('Console App Principal ID')
param consolePrincipalId string

var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

resource contributorOnWorkloadRg 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, consolePrincipalId, contributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
    principalId: consolePrincipalId
    principalType: 'ServicePrincipal'
  }
}
