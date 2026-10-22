// Foundry model deployments with the DR Agent's content filter (RAI policy) "dra-guardrails".
// Used by openai.bicep, and deployed on its own by scripts/deploy-ws2.sh against the existing Foundry resource.
//
// dra-guardrails = Microsoft.DefaultV2 (hate, sexual, violence, self-harm at medium; jailbreak; protected material)
// plus Prompt Shields "Indirect Attack" (blocking): instructions hidden in tool results (drill event messages,
// Autopilot AI summaries) are blocked like a direct jailbreak. No extra resource, hop or latency.
// Capacity is pay-per-token on GlobalStandard, so raising it only adds throttling headroom, not cost.

@description('Existing Foundry (AI Services) account name, e.g. dra-foundry-e23x')
param foundryName string

@description('Default model deployment name')
param deploymentName string = 'gpt-4.1-mini'

@description('Default model name')
param modelName string = 'gpt-4.1-mini'

@description('Default model version')
param modelVersion string = '2025-04-14'

@description('Default deployment SKU')
param deploymentSku string = 'GlobalStandard'

@description('Default deployment capacity in thousands of tokens per minute (one Black box answer uses 15-25K)')
param capacity int = 200

@description('Reasoning model deployment used by the Planner agent')
param reasoningDeploymentName string = 'gpt-5-mini'

@description('Reasoning model capacity (thousands of tokens per minute)')
param reasoningCapacity int = 100

@description('Content filter (RAI policy) name')
param raiPolicyName string = 'dra-guardrails'

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryName
}

var harms = [
  'Hate'
  'Sexual'
  'Violence'
  'Selfharm'
]

resource guardrails 'Microsoft.CognitiveServices/accounts/raiPolicies@2025-06-01' = {
  parent: foundry
  name: raiPolicyName
  properties: {
    mode: 'Blocking'
    basePolicyName: 'Microsoft.DefaultV2'
    contentFilters: concat(
      flatten(map(harms, h => [
        { name: h, source: 'Prompt', severityThreshold: 'Medium', blocking: true, enabled: true }
        { name: h, source: 'Completion', severityThreshold: 'Medium', blocking: true, enabled: true }
      ])),
      [
        { name: 'Jailbreak', source: 'Prompt', blocking: true, enabled: true }
        { name: 'Indirect Attack', source: 'Prompt', blocking: true, enabled: true }
        { name: 'Protected Material Text', source: 'Completion', blocking: true, enabled: true }
        { name: 'Protected Material Code', source: 'Completion', blocking: false, enabled: true }
      ]
    )
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: deploymentName
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
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: guardrails.name
  }
}

resource reasoningDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: reasoningDeploymentName
  dependsOn: [
    deployment // serialize child operations on the account: parallel ones fail with RequestConflict
  ]
  sku: {
    name: 'GlobalStandard'
    capacity: reasoningCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5-mini'
      version: '2025-08-07'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: guardrails.name
  }
}

output raiPolicyName string = guardrails.name
output deploymentName string = deployment.name
output reasoningDeploymentName string = reasoningDeployment.name
