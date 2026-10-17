// DR Agent Infrastructure - Subscription-scope deployment
// Creates two resource groups and deploys all modules

targetScope = 'subscription'

@description('Location for Region A (primary)')
param locationA string = 'centralindia'

@description('Location for Region B (secondary)')
param locationB string = 'southeastasia'

@description('Location for control plane resources')
param controlLocation string = 'centralindia'

@description('Location for Azure OpenAI')
param openAILocation string = 'eastus2'

@description('SQL Admin password')
@secure()
param sqlAdminPassword string

@description('Console passcode')
@secure()
param consolePasscode string

@secure()
@description('Resend API key: instant email to NOTIFY_EMAIL_TO (optional; Azure Monitor email is the fallback)')
param resendApiKey string = ''

@description('Email address for DR notifications (empty = console only)')
param notifyEmailTo string = ''

@description('Deploy Azure Communication Services email')
param deployEmail bool = false

@description('Plant the demo config gap: Region B app deployed WITHOUT PAYMENT_PROVIDER')
param plantConfigGap bool = true

var paymentProvider = 'virtusa-pay-sandbox'

@description('Skip OpenAI creation if using existing')
param useExistingOpenAI bool = false

@description('Project tag')
param projectTag string = 'dr-agent'

// Generate a suffix from subscription ID for unique naming
var suffix = take(uniqueString(subscription().id), 4)

@description('Public URL judges open on their phones (custom domain CNAME -> Traffic Manager). Empty = console /go redirector.')
param publicUrl string = ''

@description('Second hostname for the same app (forces a fresh DNS lookup on phones after failover). Empty = same as publicUrl.')
param altPublicUrl string = ''

@description('Console app name = public hostname <name>.azurewebsites.net (global). Phones open https://<name>.azurewebsites.net/pay')
param consoleAppName string = 'azure-armada'

var consoleBaseUrl = 'https://${consoleAppName}.azurewebsites.net'
var effectivePublicUrl = empty(publicUrl) ? '${consoleBaseUrl}/pay' : publicUrl
var effectiveAltUrl = empty(altPublicUrl) ? effectivePublicUrl : altPublicUrl

var workloadRgName = 'rg-dra-workload'
var controlRgName = 'rg-dra-control'

// Resource Group for workload (demo app, SQL, Traffic Manager)
resource workloadRg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: workloadRgName
  location: locationA
  tags: {
    project: projectTag
  }
}

// Resource Group for control plane (console, storage, Key Vault, OpenAI)
resource controlRg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: controlRgName
  location: controlLocation
  tags: {
    project: projectTag
  }
}

// Log Analytics + App Insights (in control RG, shared)
module log 'modules/log.bicep' = {
  name: 'log-deployment'
  scope: controlRg
  params: {
    location: controlLocation
    suffix: suffix
    projectTag: projectTag
  }
}

// Key Vault (in control RG)
module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault-deployment'
  scope: controlRg
  params: {
    location: controlLocation
    suffix: suffix
    projectTag: projectTag
    sqlAdminPassword: sqlAdminPassword
    consolePasscode: consolePasscode
    resendApiKey: resendApiKey
  }
}

// Storage Account (in control RG)
module storage 'modules/storage.bicep' = {
  name: 'storage-deployment'
  scope: controlRg
  params: {
    location: controlLocation
    suffix: suffix
    projectTag: projectTag
  }
}

// SQL Servers + Failover Group (in workload RG)
module sql 'modules/sql.bicep' = {
  name: 'sql-deployment'
  scope: workloadRg
  params: {
    locationA: locationA
    locationB: locationB
    suffix: suffix
    sqlAdminPassword: sqlAdminPassword
    projectTag: projectTag
  }
}

// Web App A (Region A - Central India)
module webAppA 'modules/webapp.bicep' = {
  name: 'webapp-a-deployment'
  scope: workloadRg
  params: {
    location: locationA
    suffix: suffix
    regionShort: 'cin'
    regionName: 'Central India'
    drLiveUrl: '${consoleBaseUrl}/public/live'
    publicUrl: effectivePublicUrl
    altPublicUrl: effectiveAltUrl
    paymentProvider: paymentProvider
    keyVaultName: keyVault.outputs.keyVaultName
    sqlFogFqdn: sql.outputs.fogListenerFqdn
    sqlDatabaseName: sql.outputs.databaseName
    sqlAdminUser: sql.outputs.sqlAdminUsername
    appInsightsConnectionString: log.outputs.appInsightsConnectionString
    logWorkspaceId: log.outputs.workspaceId
    allowHttp: true
    projectTag: projectTag
  }
}

// Web App B (Region B - South India)
module webAppB 'modules/webapp.bicep' = {
  name: 'webapp-b-deployment'
  scope: workloadRg
  params: {
    location: locationB
    suffix: suffix
    regionShort: 'sea'
    regionName: 'Southeast Asia'
    drLiveUrl: '${consoleBaseUrl}/public/live'
    publicUrl: effectivePublicUrl
    altPublicUrl: effectiveAltUrl
    paymentProvider: plantConfigGap ? '' : paymentProvider
    keyVaultName: keyVault.outputs.keyVaultName
    sqlFogFqdn: sql.outputs.fogListenerFqdn
    sqlDatabaseName: sql.outputs.databaseName
    sqlAdminUser: sql.outputs.sqlAdminUsername
    appInsightsConnectionString: log.outputs.appInsightsConnectionString
    logWorkspaceId: log.outputs.workspaceId
    allowHttp: true
    projectTag: projectTag
  }
}

// Traffic Manager (global, references workload RG apps)
module trafficManager 'modules/trafficmanager.bicep' = {
  name: 'trafficmanager-deployment'
  scope: workloadRg
  params: {
    suffix: suffix
    webAppAId: webAppA.outputs.webAppId
    webAppBId: webAppB.outputs.webAppId
    webAppAName: webAppA.outputs.webAppName
    webAppBName: webAppB.outputs.webAppName
    workloadRgName: workloadRgName
    projectTag: projectTag
  }
}

// Azure OpenAI (optional, in control RG but deployed to eastus2)
module openAI 'modules/openai.bicep' = {
  name: 'openai-deployment'
  scope: controlRg
  params: {
    location: openAILocation
    suffix: suffix
    useExistingOpenAI: useExistingOpenAI
    projectTag: projectTag
  }
}

// Email via Azure Monitor alerts (works on Free Trial; ACS email does not)
module alerts 'modules/alerts.bicep' = if (!empty(notifyEmailTo)) {
  name: 'alerts-deployment'
  scope: controlRg
  params: {
    location: controlLocation
    suffix: suffix
    projectTag: projectTag
    appInsightsId: log.outputs.appInsightsId
    notifyEmailTo: notifyEmailTo
  }
}

// Email / ACS (in control RG)
module email 'modules/email.bicep' = if (deployEmail) {
  name: 'email-deployment'
  scope: controlRg
  params: {
    location: controlLocation
    suffix: suffix
    keyVaultName: keyVault.outputs.keyVaultName
    projectTag: projectTag
  }
}

// Console App (in control RG)
module console 'modules/console.bicep' = {
  name: 'console-deployment'
  scope: controlRg
  params: {
    appName: consoleAppName
    useResend: !empty(resendApiKey)
    publicUrl: effectivePublicUrl
    location: controlLocation
    suffix: suffix
    keyVaultName: keyVault.outputs.keyVaultName
    appInsightsConnectionString: log.outputs.appInsightsConnectionString
    logWorkspaceId: log.outputs.workspaceId
    storageAccountName: storage.outputs.storageAccountName
    storageTableEndpoint: storage.outputs.tableEndpoint
    storageBlobEndpoint: storage.outputs.blobEndpoint
    openAIEndpoint: useExistingOpenAI ? '' : openAI.outputs.openAIEndpoint
    openAIDeploymentName: openAI.outputs.openAIDeploymentName
    foundryProjectEndpoint: useExistingOpenAI ? '' : openAI.outputs.projectEndpoint
    tmFqdn: trafficManager.outputs.tmFqdn
    tmProfileName: trafficManager.outputs.tmProfileName
    webAppAName: webAppA.outputs.webAppName
    webAppBName: webAppB.outputs.webAppName
    sqlServerAName: sql.outputs.sqlServerAName
    sqlServerBName: sql.outputs.sqlServerBName
    sqlFogName: sql.outputs.fogName
    workloadRgName: workloadRgName
    acsSenderAddress: deployEmail ? email!.outputs.senderAddress : ''
    notifyEmailTo: notifyEmailTo
    projectTag: projectTag
  }
}

// Role assignments (in control RG, but also cross-RG for workload)
module roles 'modules/roles.bicep' = {
  name: 'roles-deployment'
  scope: controlRg
  params: {
    consolePrincipalId: console.outputs.consoleAppPrincipalId
    storageAccountId: storage.outputs.storageAccountId
    keyVaultId: keyVault.outputs.keyVaultId
    openAIId: useExistingOpenAI ? '' : openAI.outputs.openAIId
    webAppAPrincipalId: webAppA.outputs.webAppPrincipalId
    webAppBPrincipalId: webAppB.outputs.webAppPrincipalId
  }
  dependsOn: [
    workloadRg
  ]
}

module rolesWorkload 'modules/roles-workload.bicep' = {
  name: 'roles-workload-deployment'
  scope: workloadRg
  params: {
    consolePrincipalId: console.outputs.consoleAppPrincipalId
  }
}

// Outputs
output suffix string = suffix
output workloadRgName string = workloadRgName
output controlRgName string = controlRgName

output trafficManagerUrl string = trafficManager.outputs.tmUrl
output trafficManagerFqdn string = trafficManager.outputs.tmFqdn

output webAppAUrl string = webAppA.outputs.webAppUrl
output webAppBUrl string = webAppB.outputs.webAppUrl

output consoleUrl string = console.outputs.consoleAppUrl
output consoleAppName string = console.outputs.consoleAppName
output publicUrl string = effectivePublicUrl

output sqlFogListener string = sql.outputs.fogListenerFqdn

output foundryProjectEndpoint string = useExistingOpenAI ? '' : openAI.outputs.projectEndpoint
output keyVaultName string = keyVault.outputs.keyVaultName
output storageAccountName string = storage.outputs.storageAccountName
