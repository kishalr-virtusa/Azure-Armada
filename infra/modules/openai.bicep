// Azure AI Foundry: AI Services account (Foundry resource) + Foundry project + gpt-4.1-mini model deployment.
// The DR Agent's agents use this project (model calls, Agent Service, tracing in the Foundry portal).

@description('Location for the Foundry resource (eastus2 has GlobalStandard gpt-4.1-mini quota on Free Trial)')
param location string = 'eastus2'

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Skip creation if an existing Foundry resource is used')
param useExistingOpenAI bool = false

@description('Model deployment name')
param deploymentName string = 'gpt-4.1-mini'

@description('Model name')
param modelName string = 'gpt-4.1-mini'

@description('Model version')
param modelVersion string = '2025-04-14'

@description('Deployment SKU (Free Trial has GlobalStandard quota for gpt-4.1-mini)')
param deploymentSku string = 'GlobalStandard'

@description('Capacity in thousands of tokens per minute')
param capacity int = 30

@description('Foundry project name')
param projectName string = 'dr-agent'

var foundryName = 'dra-foundry-${suffix}'

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = if (!useExistingOpenAI) {
  name: foundryName
  location: location
  tags: {
    project: projectTag
  }
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: foundryName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (!useExistingOpenAI) {
  parent: foundry
  name: projectName
  location: location
  tags: {
    project: projectTag
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'DR Agent: Autonomous Disaster Recovery Validation'
    description: 'Agents for DR strategy, readiness, runbook review, exercise planning, validation and reporting.'
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (!useExistingOpenAI) {
  parent: foundry
  name: deploymentName
  // serialize after the project: parallel child operations on the account fail with RequestConflict
  dependsOn: [
    project
  ]
  sku: {
    name: deploymentSku
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

output openAIId string = useExistingOpenAI ? '' : foundry.id
output openAIName string = useExistingOpenAI ? '' : foundry.name
output openAIEndpoint string = useExistingOpenAI ? '' : 'https://${foundryName}.openai.azure.com/'
output openAIDeploymentName string = deploymentName
output projectEndpoint string = useExistingOpenAI ? '' : 'https://${foundryName}.services.ai.azure.com/api/projects/${projectName}'
output projectName string = projectName
