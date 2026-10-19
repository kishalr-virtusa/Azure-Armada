// WS3 observability: agent telemetry in the Foundry portal and an "Agent operations" workbook.
//
// - Foundry project connection (category AppInsights) to the existing Application Insights, so the Foundry
//   portal's Tracing view shows the console's GenAI spans (invoke_agent / execute_tool / chat).
// - Azure Workbook "DR Agent: agent operations": calls, p50/p95 latency, fallback %, blocked, tokens and $ per
//   agent per day, plus a per-drill drill-down by dr.exercise_id.
// - Optional log alert "agent fallback rate > 30% in 1 h" to the existing action group (≈$1.50 per month).
//
// Standalone: deployed by infra/scripts/deploy-ws3.sh into rg-dra-control; it only references existing resources.
// Span attributes come from apps/dr-console/dr_agent/trace.py (customDimensions in the App Insights schema).

@description('Location of the workbook and the alert (the Application Insights region)')
param location string = resourceGroup().location

@description('Resource suffix (as in main.bicep)')
param suffix string

@description('Project tag')
param projectTag string = 'dr-agent'

@description('Existing Application Insights component')
param appInsightsName string = 'dra-insights-${suffix}'

@description('Existing Foundry (AI Services) account')
param foundryName string = 'dra-foundry-${suffix}'

@description('Existing Foundry project')
param projectName string = 'dr-agent'

@description('Create the fallback-rate log alert (billed per evaluation, about $1.50 per month)')
param deployFallbackAlert bool = false

@description('Existing action group for the alert')
param actionGroupName string = 'dra-notify-${suffix}'

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundry
  name: projectName
}

// Foundry portal > project > Tracing reads from this App Insights resource.
resource tracingConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: appInsightsName
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    isSharedToAll: false
    credentials: {
      key: appInsights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
  }
}

// ------------------------------------------------------------------------------------------------ workbook
// One row per invoke_agent span. The final span of an agent call has dr.outcome accepted (model answer passed
// the checks) or fallback (deterministic rules); rejected/failed spans are earlier attempts of the same call.
var agentSpans = '''
dependencies
| where timestamp {TimeRange}
| where name startswith "invoke_agent"
| extend Agent = tostring(customDimensions["gen_ai.agent.name"]), Outcome = tostring(customDimensions["dr.outcome"]),
    Via = tostring(customDimensions["dr.via"]), IsBlocked = tostring(customDimensions["dr.blocked"]) =~ "true",
    Tokens = tolong(customDimensions["gen_ai.usage.input_tokens"]) + tolong(customDimensions["gen_ai.usage.output_tokens"]),
    CostUsd = todouble(customDimensions["dr.cost_usd"]), Exercise = tostring(customDimensions["dr.exercise_id"])
| extend Final = Outcome in ("accepted", "fallback"), ModelMs = iff(Via != "fallback", duration, real(null))
'''

var perAgentQuery = '${agentSpans}| summarize Calls = countif(Final), Attempts = count(), P50_ms = round(percentile(ModelMs, 50), 0), P95_ms = round(percentile(ModelMs, 95), 0), FallbackPct = round(100.0 * countif(Outcome == "fallback") / max_of(countif(Final), 1), 1), Blocked = countif(IsBlocked and Final), Tokens = sum(Tokens), CostUsd = round(sum(CostUsd), 4) by Agent\n| order by Calls desc'

var perDayQuery = '${agentSpans}| summarize Calls = countif(Final), P95_ms = round(percentile(ModelMs, 95), 0), FallbackPct = round(100.0 * countif(Outcome == "fallback") / max_of(countif(Final), 1), 1), Blocked = countif(IsBlocked and Final), Tokens = sum(Tokens), CostUsd = round(sum(CostUsd), 4) by Day = bin(timestamp, 1d), Agent\n| order by Day desc, Agent asc'

var costChartQuery = '${agentSpans}| summarize CostUsd = sum(CostUsd) by bin(timestamp, 1d), Agent'

var outcomeChartQuery = '${agentSpans}| summarize count() by bin(timestamp, 1d), Outcome'

var drillQuery = '''
dependencies
| where timestamp {TimeRange}
| where tostring(customDimensions["dr.exercise_id"]) == "{ExerciseId}"
| where name startswith "invoke_agent" or name startswith "execute_tool" or name startswith "validate" or name startswith "chat"
| project timestamp, Span = name, DurationMs = round(duration, 0), Success = success,
    Outcome = tostring(customDimensions["dr.outcome"]), Via = tostring(customDimensions["dr.via"]),
    Model = tostring(customDimensions["gen_ai.response.model"]), Version = tostring(customDimensions["gen_ai.agent.version"]),
    Tokens = tolong(customDimensions["gen_ai.usage.input_tokens"]) + tolong(customDimensions["gen_ai.usage.output_tokens"]),
    CostUsd = todouble(customDimensions["dr.cost_usd"]), operation_Id
| order by timestamp asc
'''

var exerciseListQuery = 'dependencies\n| where timestamp {TimeRange}\n| extend x = tostring(customDimensions["dr.exercise_id"])\n| where isnotempty(x)\n| summarize Last = max(timestamp) by x\n| order by Last desc\n| project x'

var aiSource = {
  queryType: 0
  resourceType: 'microsoft.insights/components'
  timeContextFromParameter: 'TimeRange'
  timeContext: { durationMs: 604800000 }
}

var workbookContent = {
  version: 'Notebook/1.0'
  items: [
    {
      type: 1
      name: 'header'
      content: {
        json: '## DR Agent: agent operations\nEvery agent call of the console and the drill engine, from the OpenTelemetry GenAI spans (`invoke_agent`). **Calls** counts final answers (model answer accepted, or deterministic fallback); **Attempts** includes rejected answers, repair rounds and failed model calls. Latency is per model attempt. $ uses the list prices in `dr_agent/telemetry.py`.'
      }
    }
    {
      type: 9
      name: 'parameters'
      content: {
        version: 'KqlParameterItem/1.0'
        style: 'pills'
        queryType: 0
        resourceType: 'microsoft.insights/components'
        parameters: [
          {
            id: '6f0c6b1e-1d9e-4a53-9a51-1f4f3f5b9a01'
            version: 'KqlParameterItem/1.0'
            name: 'TimeRange'
            label: 'Time range'
            type: 4
            isRequired: true
            value: { durationMs: 604800000 }
            typeSettings: {
              allowCustom: true
              selectableValues: [
                { durationMs: 3600000 }
                { durationMs: 86400000 }
                { durationMs: 604800000 }
                { durationMs: 2592000000 }
              ]
            }
          }
          {
            id: '6f0c6b1e-1d9e-4a53-9a51-1f4f3f5b9a02'
            version: 'KqlParameterItem/1.0'
            name: 'ExerciseId'
            label: 'Drill'
            type: 2
            isRequired: false
            query: exerciseListQuery
            queryType: 0
            resourceType: 'microsoft.insights/components'
            typeSettings: { showDefault: false }
          }
        ]
      }
    }
    {
      type: 3
      name: 'per-agent'
      content: union(aiSource, {
        version: 'KqlItem/1.0'
        title: 'Per agent: calls, latency (ms), fallback %, blocked, tokens, $'
        query: perAgentQuery
        size: 0
        visualization: 'table'
      })
    }
    {
      type: 3
      name: 'cost-per-day'
      customWidth: '50'
      content: union(aiSource, {
        version: 'KqlItem/1.0'
        title: '$ per agent per day'
        query: costChartQuery
        size: 0
        visualization: 'barchart'
      })
    }
    {
      type: 3
      name: 'outcomes-per-day'
      customWidth: '50'
      content: union(aiSource, {
        version: 'KqlItem/1.0'
        title: 'Attempts by outcome per day'
        query: outcomeChartQuery
        size: 0
        visualization: 'barchart'
      })
    }
    {
      type: 3
      name: 'per-agent-per-day'
      content: union(aiSource, {
        version: 'KqlItem/1.0'
        title: 'Per agent per day'
        query: perDayQuery
        size: 0
        visualization: 'table'
      })
    }
    {
      type: 3
      name: 'drill'
      conditionalVisibility: { parameterName: 'ExerciseId', comparison: 'isNotEqualTo', value: '' }
      content: union(aiSource, {
        version: 'KqlItem/1.0'
        title: 'Agent calls during drill {ExerciseId}'
        query: drillQuery
        size: 0
        visualization: 'table'
      })
    }
  ]
  isLocked: false
  fallbackResourceIds: [ appInsights.id ]
}

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'dra-agent-operations')
  location: location
  kind: 'shared'
  tags: { project: projectTag }
  properties: {
    displayName: 'DR Agent: agent operations'
    category: 'workbook'
    sourceId: appInsights.id
    serializedData: string(workbookContent)
    version: 'Notebook/1.0'
  }
}

// ------------------------------------------------------------------------------------------------ alert
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = if (deployFallbackAlert) {
  name: actionGroupName
}

resource fallbackAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = if (deployFallbackAlert) {
  name: 'DR Agent fallback rate'
  location: location
  tags: { project: projectTag }
  properties: {
    displayName: 'DR Agent fallback rate'
    description: 'More than 30% of agent calls fell back to the deterministic rules in the last hour (at least 3 calls).'
    severity: 2
    enabled: true
    scopes: [ appInsights.id ]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT1H'
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: 'dependencies | where name startswith "invoke_agent" | extend Outcome = tostring(customDimensions["dr.outcome"]) | where Outcome in ("accepted", "fallback") | summarize Calls = count(), Fallbacks = countif(Outcome == "fallback") | where Calls >= 3 and 100.0 * Fallbacks / Calls > 30'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [ actionGroup.id ]
    }
  }
}

output workbookId string = workbook.id
output workbookPortalUrl string = 'https://portal.azure.com/#@/resource${workbook.id}/workbook'
output connectionName string = tracingConnection.name
