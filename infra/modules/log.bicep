// Log Analytics Workspace + Application Insights
// Shared across both resource groups

@description('Location for resources')
param location string

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

var workspaceName = 'dra-log-${suffix}'
var appInsightsName = 'dra-insights-${suffix}'

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  tags: {
    project: projectTag
  }
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: {
    project: projectTag
  }
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logWorkspace.id
  }
}

@description('Log Analytics Workspace ID')
output workspaceId string = logWorkspace.id

@description('Log Analytics Workspace Name')
output workspaceName string = logWorkspace.name

@description('Application Insights Instrumentation Key')
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey

@description('Application Insights Connection String')
output appInsightsConnectionString string = appInsights.properties.ConnectionString

@description('Application Insights ID')
output appInsightsId string = appInsights.id
