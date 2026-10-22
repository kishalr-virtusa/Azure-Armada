// Azure SQL with Failover Group
// - Two SQL servers (Region A + Region B)
// - paymentsdb Basic DTU database on primary
// - Failover group with Manual policy (geo-secondary auto-created)

@description('Location for primary server (Region A)')
param locationA string

@description('Location for secondary server (Region B)')
param locationB string

@description('Suffix for unique naming')
param suffix string

@description('SQL Admin username')
param sqlAdminUser string = 'sqladmin'

@description('SQL Admin password')
@secure()
param sqlAdminPassword string

@description('Project tag value')
param projectTag string = 'dr-agent'

@description('Database name')
param databaseName string = 'paymentsdb'

var serverNameA = 'dra-sql-eas-${suffix}'
var serverNameB = 'dra-sql-sea-${suffix}'
var fogName = 'dra-fog-${suffix}'

// Primary SQL Server (Region A - East Asia)
resource sqlServerA 'Microsoft.Sql/servers@2023-05-01-preview' = {
  name: serverNameA
  location: locationA
  tags: {
    project: projectTag
  }
  properties: {
    administratorLogin: sqlAdminUser
    administratorLoginPassword: sqlAdminPassword
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

// Secondary SQL Server (Region B - South India)
resource sqlServerB 'Microsoft.Sql/servers@2023-05-01-preview' = {
  name: serverNameB
  location: locationB
  tags: {
    project: projectTag
  }
  properties: {
    administratorLogin: sqlAdminUser
    administratorLoginPassword: sqlAdminPassword
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

// Firewall rule: Allow Azure services (primary)
resource firewallRuleA 'Microsoft.Sql/servers/firewallRules@2023-05-01-preview' = {
  parent: sqlServerA
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Firewall rule: Allow Azure services (secondary)
resource firewallRuleB 'Microsoft.Sql/servers/firewallRules@2023-05-01-preview' = {
  parent: sqlServerB
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Database on primary server (Basic DTU)
resource database 'Microsoft.Sql/servers/databases@2023-05-01-preview' = {
  parent: sqlServerA
  name: databaseName
  location: locationA
  tags: {
    project: projectTag
  }
  sku: {
    name: 'Basic'
    tier: 'Basic'
    capacity: 5
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 2147483648 // 2 GB
  }
}

// Failover Group with Manual failover policy
resource failoverGroup 'Microsoft.Sql/servers/failoverGroups@2023-05-01-preview' = {
  parent: sqlServerA
  name: fogName
  properties: {
    readWriteEndpoint: {
      failoverPolicy: 'Manual'
    }
    partnerServers: [
      {
        id: sqlServerB.id
      }
    ]
    databases: [
      database.id
    ]
  }
}

@description('Primary SQL Server ID')
output sqlServerAId string = sqlServerA.id

@description('Primary SQL Server Name')
output sqlServerAName string = sqlServerA.name

@description('Primary SQL Server FQDN')
output sqlServerAFqdn string = sqlServerA.properties.fullyQualifiedDomainName

@description('Secondary SQL Server ID')
output sqlServerBId string = sqlServerB.id

@description('Secondary SQL Server Name')
output sqlServerBName string = sqlServerB.name

@description('Secondary SQL Server FQDN')
output sqlServerBFqdn string = sqlServerB.properties.fullyQualifiedDomainName

@description('Failover Group Name')
output fogName string = failoverGroup.name

@description('Failover Group Listener FQDN (use this for connections)')
output fogListenerFqdn string = '${fogName}.database.windows.net'

@description('Database Name')
output databaseName string = database.name

@description('SQL Admin Username')
output sqlAdminUsername string = sqlAdminUser
