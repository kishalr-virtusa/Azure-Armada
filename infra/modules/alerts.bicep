// Email notifications via Azure Monitor (ACS email is blocked on Free Trial; Gmail/Chat are blocked by the tenant).
// The console sends a "DRNotify" custom event to Application Insights; this log search alert fires once per event
// (split by title/body/kind) and the action group emails the DR team from azure-noreply@microsoft.com.
// Latency: ~2-7 min (ingestion + 5 min evaluation). Background audit trail; live alerts are in the console.

@description('Azure region for the alert rule')
param location string

@description('Resource suffix')
param suffix string

@description('Project tag')
param projectTag string = 'dr-agent'

@description('Application Insights resource id (where DRNotify events land)')
param appInsightsId string

@description('Recipient (empty = no email)')
param notifyEmailTo string

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'dra-notify-${suffix}'
  location: 'global'
  tags: { project: projectTag }
  properties: {
    groupShortName: 'DRAgent'
    enabled: true
    emailReceivers: [
      {
        name: 'dr-team'
        emailAddress: notifyEmailTo
        useCommonAlertSchema: true
      }
    ]
  }
}

resource notifyRule 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'DR Agent notification'
  location: location
  tags: { project: projectTag }
  properties: {
    displayName: 'DR Agent notification'
    description: 'Virtusa DR Agent: approval requests, outage/recovery updates and reports (see dimensions for details).'
    severity: 3
    enabled: true
    scopes: [ appInsightsId ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    autoMitigate: false
    criteria: {
      allOf: [
        {
          // each event is counted in exactly one evaluation: filter on ingestion time of the last 5 minutes
          query: 'customEvents | where name == "DRNotify" | where ingestion_time() > ago(5m) | extend Title = tostring(customDimensions["title"]), Details = tostring(customDimensions["body"]), Kind = tostring(customDimensions["kind"]), Link = tostring(customDimensions["link"])'
          timeAggregation: 'Count'
          dimensions: [
            { name: 'Title', operator: 'Include', values: [ '*' ] }
            { name: 'Details', operator: 'Include', values: [ '*' ] }
            { name: 'Kind', operator: 'Include', values: [ '*' ] }
            { name: 'Link', operator: 'Include', values: [ '*' ] }
          ]
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
      customProperties: {
        System: 'Virtusa DR Agent - Autonomous Disaster Recovery Validation'
        Team: 'Azure Armada'
      }
    }
  }
}

output actionGroupId string = actionGroup.id
