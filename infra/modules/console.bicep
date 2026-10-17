// DR Console App Service
// Control plane app with system identity for Azure management

@description('Location for resources')
param location string

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Key Vault name for secret references')
param keyVaultName string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Log Analytics Workspace ID')
param logWorkspaceId string

@description('Storage account name')
param storageAccountName string

@description('Storage table endpoint')
param storageTableEndpoint string

@description('Storage blob endpoint')
param storageBlobEndpoint string

@description('Azure OpenAI endpoint')
param openAIEndpoint string

@description('Azure OpenAI deployment name')
param openAIDeploymentName string

@description('Traffic Manager FQDN')
param tmFqdn string

@description('Traffic Manager profile name')
param tmProfileName string

@description('Web App A name')
param webAppAName string

@description('Web App B name')
param webAppBName string

@description('SQL Server A name')
param sqlServerAName string

@description('SQL Server B name')
param sqlServerBName string

@description('SQL Failover Group name')
param sqlFogName string

@description('Workload resource group name')
param workloadRgName string

@description('ACS sender address')
param acsSenderAddress string

@description('Azure AI Foundry project endpoint (Agent Service)')
param foundryProjectEndpoint string = ''

@description('Email address for notifications (empty = console only)')
param notifyEmailTo string = ''

@description('Public URL of Contoso Payments shown as a QR code in the presenter view')
param publicUrl string = ''

var planName = 'dra-console-plan-${suffix}'
@description('Console web app name (global hostname)')
param appName string = 'dra-console-${suffix}'

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
    reserved: true
  }
}

// Console Web App
resource consoleApp 'Microsoft.Web/sites@2023-01-01' = {
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
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'bash startup.sh'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'PUBLIC_URL'
          value: publicUrl
        }
        // Azure subscription context
        {
          name: 'WORKLOAD_RG'
          value: workloadRgName
        }
        // Web App identifiers
        {
          name: 'WEBAPP_A_NAME'
          value: webAppAName
        }
        {
          name: 'WEBAPP_B_NAME'
          value: webAppBName
        }
        // Traffic Manager
        {
          name: 'TM_PROFILE_NAME'
          value: tmProfileName
        }
        {
          name: 'TM_FQDN'
          value: tmFqdn
        }
        // SQL Failover Group
        {
          name: 'SQL_SERVER_A'
          value: sqlServerAName
        }
        {
          name: 'SQL_SERVER_B'
          value: sqlServerBName
        }
        {
          name: 'SQL_FOG_NAME'
          value: sqlFogName
        }
        // Azure OpenAI
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAIEndpoint
        }
        {
          name: 'AZURE_OPENAI_DEPLOYMENT'
          value: openAIDeploymentName
        }
        // Storage
        {
          name: 'STORAGE_ACCOUNT_NAME'
          value: storageAccountName
        }
        {
          name: 'STORAGE_TABLE_ENDPOINT'
          value: storageTableEndpoint
        }
        {
          name: 'STORAGE_BLOB_ENDPOINT'
          value: storageBlobEndpoint
        }
        // Console auth (Key Vault reference)
        {
          name: 'CONSOLE_PASSCODE'
          value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=console-passcode)'
        }
        // Email notifications
        {
          name: 'ACS_CONNECTION_STRING'
          value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=acs-connection-string)'
        }
        {
          name: 'ACS_SENDER'
          value: acsSenderAddress
        }
        {
          name: 'NOTIFY_EMAIL_TO'
          value: notifyEmailTo
        }
        {
          name: 'TM_ENDPOINT_A'
          value: 'endpoint-a'
        }
        {
          name: 'TM_ENDPOINT_B'
          value: 'endpoint-b'
        }
        {
          name: 'SQL_DB'
          value: 'paymentsdb'
        }
        {
          name: 'SQL_USER'
          value: 'sqladmin'
        }
        {
          name: 'SQL_PASSWORD'
          value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=sql-admin-password)'
        }
        {
          name: 'FOUNDRY_PROJECT_ENDPOINT'
          value: foundryProjectEndpoint
        }
        {
          name: 'AZURE_SUBSCRIPTION_ID'
          value: subscription().subscriptionId
        }
        // App Insights
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
      ]
    }
  }
}

// Diagnostic settings
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'logs-to-workspace'
  scope: consoleApp
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

@description('Console App ID')
output consoleAppId string = consoleApp.id

@description('Console App Name')
output consoleAppName string = consoleApp.name

@description('Console App Hostname')
output consoleAppHostname string = consoleApp.properties.defaultHostName

@description('Console App URL')
output consoleAppUrl string = 'https://${consoleApp.properties.defaultHostName}'

@description('Console App Principal ID (for RBAC)')
output consoleAppPrincipalId string = consoleApp.identity.principalId
