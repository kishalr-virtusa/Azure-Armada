<#
  Creates / updates the contained read-only user reports_reader in paymentsdb by running
  infra/sql/reports_reader.sql from inside dra-vm-reports (the VM is allowed through the SQL firewall as an
  Azure service; the operator's machine may not be). Sent by infra/scripts/reports-vm.sh setup through a
  managed Run Command: the passwords arrive as protected parameters and are never printed.
  __SQL_B64__ is replaced with the base64 of reports_reader.sql before the script is sent.
#>
param(
  [string]$SqlServer = '',
  [string]$SqlDatabase = 'paymentsdb',
  [string]$AdminUser = 'sqladmin',
  [string]$AdminPassword = '',
  [string]$ReaderPassword = ''
)
$ErrorActionPreference = 'Stop'
foreach ($n in 'SqlServer', 'AdminUser', 'AdminPassword', 'ReaderPassword') {
  if (-not (Get-Variable $n -ValueOnly) -and (Test-Path "env:$n")) { Set-Variable $n (Get-Item "env:$n").Value }
}
if (-not $SqlServer -or -not $AdminPassword -or -not $ReaderPassword) {
  throw 'SqlServer, AdminPassword and ReaderPassword are required'
}

$sql = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SQL_B64__'))
$sql = $sql.Replace('__READER_PASSWORD__', $ReaderPassword.Replace("'", "''"))

$b = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
$b['Data Source'] = "tcp:$SqlServer,1433"
$b['Initial Catalog'] = $SqlDatabase
$b['User ID'] = $AdminUser
$b['Password'] = $AdminPassword
$b['Encrypt'] = $true
$b['TrustServerCertificate'] = $false
$b['Connect Timeout'] = 30
$conn = New-Object System.Data.SqlClient.SqlConnection $b.ConnectionString
try {
  $conn.Open()
  $cmd = $conn.CreateCommand()
  $cmd.CommandText = $sql
  $cmd.CommandTimeout = 60
  $rd = $cmd.ExecuteReader()
  do {
    while ($rd.Read()) {
      Write-Output ('permission: {0} {1} ON {2}' -f $rd['state_desc'], $rd['permission_name'], $rd['on_object'])
    }
  } while ($rd.NextResult())
  $rd.Close()
  Write-Output "reports_reader ready in $SqlDatabase (via $SqlServer)"
} finally {
  $conn.Dispose()
}
