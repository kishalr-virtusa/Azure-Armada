// Embedding model for the Black box knowledge index (RAG): text-embedding-3-small on the existing Foundry (AI
// Services) account, GlobalStandard. The console calls it with its managed identity (the "Cognitive Services OpenAI
// User" role it already has for chat) at AZURE_OPENAI_ENDPOINT; the deployment name is the app setting
// EMBEDDING_DEPLOYMENT (default text-embedding-3-small).
// Cost: pay per token ($0.02 per 1M tokens); a full reindex of the DR records is well under 100k tokens.
// Content filters do not apply to embeddings, so no raiPolicyName.

@description('Foundry (AI Services) account name, dra-foundry-<suffix>')
param foundryName string

@description('Deployment name (the app setting EMBEDDING_DEPLOYMENT)')
param deploymentName string = 'text-embedding-3-small'

@description('Model version')
param modelVersion string = '1'

@description('Capacity in thousands of tokens per minute (small: indexing is batched and rare)')
param capacity int = 50

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryName
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: deploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

output deploymentName string = embedding.name
