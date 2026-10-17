// App Service Web App (reusable per region)
// Linux Python 3.11, B1 plan, system identity, Key Vault references

@description('Location for resources')
param location string

@description('Suffix for unique naming')
param suffix string

@description('Region short name (cin for centralindia, sin for southindia)')
param regionShort string

@description('Full region name for app settings')
param regionName string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Key Vault name for secret references')
param keyVaultName string

@description('SQL Failover Group listener FQDN')
param sqlFogFqdn string

@description('SQL Database name')
param sqlDatabaseName string

@description('SQL Admin username')
param sqlAdminUser string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Log Analytics Workspace ID for diagnostics')
param logWorkspaceId string

@description('Allow HTTP traffic (true for Traffic Manager, false for console)')
param allowHttp bool = true

@description('PAYMENT_PROVIDER app setting; empty = omitted (planted config gap)')
param paymentProvider string = ''

var planName = 'dra-plan-${regionShort}-${suffix}'
var appName = 'dra-app-${regionShort}-${suffix}'

// App Service Plan (B1)
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: planName
  location: location
  tags: {
    project: projectTag
  }
  sku: {
    name: 'B1'
    tier: 'Basic'
    size: 'B1'
    capacity: 1
  }
  kind: 'linux'
  properties: {
    reserved: true // Required for Linux
  }
}

// Web App
resource webApp 'Microsoft.Web/sites@2023-01-01' = {
  name: appName
  location: location
  tags: {
    project: projectTag
  }
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: !allowHttp
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'bash startup.sh'
      alwaysOn: true
      healthCheckPath: '/health'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: concat(empty(paymentProvider) ? [] : [
        {
          name: 'PAYMENT_PROVIDER'
          value: paymentProvider
        }
      ], [
        {
          name: 'REGION_NAME'
          value: regionName
        }
        {
          name: 'SQL_SERVER'
          value: sqlFogFqdn
        }
        {
          name: 'SQL_DB'
          value: sqlDatabaseName
        }
        {
          name: 'SQL_USER'
          value: sqlAdminUser
        }
        {
          name: 'SQL_PASSWORD'
          value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=sql-admin-password)'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
      ])
    }
  }
}

// Diagnostic settings to Log Analytics
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'logs-to-workspace'
  scope: webApp
  properties: {
    workspaceId: logWorkspaceId
    logs: [
      {
        category: 'AppServiceHTTPLogs'
        enabled: true
      }
      {
        category: 'AppServiceConsoleLogs'
        enabled: true
      }
      {
        category: 'AppServiceAppLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

@description('Web App ID')
output webAppId string = webApp.id

@description('Web App Name')
output webAppName string = webApp.name

@description('Web App Default Hostname')
output webAppHostname string = webApp.properties.defaultHostName

@description('Web App URL')
output webAppUrl string = 'https://${webApp.properties.defaultHostName}'

@description('Web App Principal ID (for RBAC)')
output webAppPrincipalId string = webApp.identity.principalId

@description('App Service Plan ID')
output planId string = appServicePlan.id
