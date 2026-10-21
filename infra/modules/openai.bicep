// Azure AI Foundry: AI Services account (Foundry resource) + Foundry project + model deployments with the
// "dra-guardrails" content filter (guardrails.bicep, also deployed on its own by scripts/deploy-ws2.sh).
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
param capacity int = 200

@description('Foundry project name')
param projectName string = 'dr-agent'

@description('Reasoning model deployment used by the Planner and Validator agents')
param reasoningDeploymentName string = 'gpt-5-mini'

@description('Reasoning model capacity (thousands of tokens per minute)')
param reasoningCapacity int = 100

@description('Embedding deployment used by the Black box knowledge index (app setting EMBEDDING_DEPLOYMENT)')
param embeddingDeploymentName string = 'text-embedding-3-small'

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

// both deployments, behind the dra-guardrails content filter (Prompt Shields: jailbreak + indirect attack)
module models 'guardrails.bicep' = if (!useExistingOpenAI) {
  name: 'foundry-models'
  // serialize after the project: parallel child operations on the account fail with RequestConflict
  dependsOn: [
    project
  ]
  params: {
    foundryName: foundryName
    deploymentName: deploymentName
    modelName: modelName
    modelVersion: modelVersion
    deploymentSku: deploymentSku
    capacity: capacity
    reasoningDeploymentName: reasoningDeploymentName
    reasoningCapacity: reasoningCapacity
  }
}

// text-embedding-3-small for the Black box knowledge index (also deployed alone by scripts/deploy-rag.sh);
// after the chat deployments: parallel deployment operations on one account fail with RequestConflict
module embeddings 'embeddings.bicep' = if (!useExistingOpenAI) {
  name: 'foundry-embeddings'
  dependsOn: [
    models
  ]
  params: {
    foundryName: foundryName
    deploymentName: embeddingDeploymentName
  }
}

output reasoningDeploymentName string = reasoningDeploymentName
output embeddingDeploymentName string = embeddingDeploymentName
output openAIId string = useExistingOpenAI ? '' : foundry.id
output openAIName string = useExistingOpenAI ? '' : foundry.name
output openAIEndpoint string = useExistingOpenAI ? '' : 'https://${foundryName}.openai.azure.com/'
output openAIDeploymentName string = deploymentName
output projectEndpoint string = useExistingOpenAI ? '' : 'https://${foundryName}.services.ai.azure.com/api/projects/${projectName}'
output projectName string = projectName
