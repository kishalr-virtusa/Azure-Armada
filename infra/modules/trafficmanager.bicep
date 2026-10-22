// Traffic Manager with Priority routing
// - Profile with DNS TTL 10s
// - HTTPS health probe on /health every 10s
// - Azure endpoints: A (prio 1), B (prio 2)
// - Hostname bindings on both web apps for TM FQDN

@description('Suffix for unique naming')
param suffix string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Web App A resource ID')
param webAppAId string

@description('Web App B resource ID')
param webAppBId string

@description('Web App A name')
param webAppAName string

@description('Web App B name')
param webAppBName string

@description('Workload resource group name')
param workloadRgName string

var profileName = 'dra-tm-${suffix}'

// Traffic Manager Profile
resource tmProfile 'Microsoft.Network/trafficmanagerprofiles@2022-04-01' = {
  name: profileName
  location: 'global'
  tags: {
    project: projectTag
  }
  properties: {
    profileStatus: 'Enabled'
    trafficRoutingMethod: 'Priority'
    dnsConfig: {
      relativeName: profileName
      ttl: 10
    }
    monitorConfig: {
      protocol: 'HTTPS'
      port: 443
      path: '/health'
      intervalInSeconds: 10
      timeoutInSeconds: 5
      toleratedNumberOfFailures: 1
    }
  }
}

// Endpoint A (Region A - East Asia) - Priority 1
resource endpointA 'Microsoft.Network/trafficmanagerprofiles/azureEndpoints@2022-04-01' = {
  parent: tmProfile
  name: 'endpoint-a'
  properties: {
    targetResourceId: webAppAId
    endpointStatus: 'Enabled'
    priority: 1
  }
}

// Endpoint B (Region B - South India) - Priority 2
resource endpointB 'Microsoft.Network/trafficmanagerprofiles/azureEndpoints@2022-04-01' = {
  parent: tmProfile
  name: 'endpoint-b'
  properties: {
    targetResourceId: webAppBId
    endpointStatus: 'Enabled'
    priority: 2
  }
}

// Hostname binding for App A to accept Traffic Manager hostname
// This must be created after TM endpoints exist (Azure validates the app is a TM endpoint)
resource hostBindingA 'Microsoft.Web/sites/hostNameBindings@2023-01-01' = {
  name: '${webAppAName}/${profileName}.trafficmanager.net'
  dependsOn: [endpointA]
  properties: {
    siteName: webAppAName
    hostNameType: 'Verified'
  }
}

// Hostname binding for App B to accept Traffic Manager hostname
resource hostBindingB 'Microsoft.Web/sites/hostNameBindings@2023-01-01' = {
  name: '${webAppBName}/${profileName}.trafficmanager.net'
  dependsOn: [endpointB]
  properties: {
    siteName: webAppBName
    hostNameType: 'Verified'
  }
}

@description('Traffic Manager Profile ID')
output tmProfileId string = tmProfile.id

@description('Traffic Manager Profile Name')
output tmProfileName string = tmProfile.name

@description('Traffic Manager FQDN')
output tmFqdn string = tmProfile.properties.dnsConfig.fqdn

@description('Traffic Manager URL (HTTP - no TLS cert for trafficmanager.net)')
output tmUrl string = 'http://${tmProfile.properties.dnsConfig.fqdn}'
