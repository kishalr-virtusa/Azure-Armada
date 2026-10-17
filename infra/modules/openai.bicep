// Azure OpenAI (AI Services) with gpt-4o-mini deployment
// Optional creation via useExistingOpenAI parameter

@description('Location for Azure OpenAI (default eastus2 for quota)')
param location string = 'eastus2'

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Skip creation if using existing OpenAI resource')
param useExistingOpenAI bool = false

@description('Deployment name for the model')
param deploymentName string = 'gpt-4o-mini'

@description('Model name')
param modelName string = 'gpt-4o-mini'

@description('Model version')
param modelVersion string = '2024-07-18'

var openAIName = 'dra-openai-${suffix}'

// Azure AI Services (OpenAI) account
resource openAI 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = if (!useExistingOpenAI) {
  name: openAIName
  location: location
  tags: {
    project: projectTag
  }
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openAIName
    publicNetworkAccess: 'Enabled'
  }
}

// Model deployment (gpt-4o-mini with Standard SKU)
resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = if (!useExistingOpenAI) {
  parent: openAI
  name: deploymentName
  sku: {
    name: 'Standard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

@description('Azure OpenAI Resource ID')
output openAIId string = useExistingOpenAI ? '' : openAI.id

@description('Azure OpenAI Resource Name')
output openAIName string = useExistingOpenAI ? '' : openAI.name

@description('Azure OpenAI Endpoint')
output openAIEndpoint string = useExistingOpenAI ? '' : openAI.properties.endpoint

@description('Deployment Name')
output openAIDeploymentName string = deploymentName
