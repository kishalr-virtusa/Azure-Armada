<#
  Contoso daily payments report service (the IaaS workload protected by Azure Site Recovery).
  Windows PowerShell 5.1, no modules. Installed to C:\reports\reports-service.ps1 by install-reports.ps1.

    -Mode serve      HTTP listener on :8080 (scheduled task ContosoReports, at startup, as SYSTEM)
                       GET  /health          {"status":"ok","region":..,"last_report":..}
                       GET  /report/latest   latest report file on disk (source "cached")
                       GET|POST /report/generate[?date=yyyy-MM-dd]
                                             build the report NOW from SQL (source "live database");
                                             HTTP 503 {"status":"error","reason":..,"latest_cached":..} if the
                                             database cannot be reached or the credentials cannot be read
    -Mode generate   build one report and write C:\reports\payments-YYYY-MM-DD.json (scheduled tasks)
      -Date today|yesterday|yyyy-MM-dd   (UTC; default today)

  Credentials: C:\reports\config.json (server, database, user, password). The password is protected with
  DPAPI LocalMachine scope ("protection":"dpapi-machine") or, if the operator chose it, stored as plain text
  ("protection":"acl-only"); in both cases the file ACL allows only SYSTEM and Administrators.
  Keep this file ASCII-only (Windows PowerShell reads BOM-less files as ANSI).
#>
param(
  [ValidateSet('serve', 'generate')][string]$Mode = 'serve',
  [string]$Date = 'today'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = 'C:\reports'
$ConfigPath = Join-Path $Root 'config.json'
$LogPath = Join-Path $Root 'service.log'
$Entropy = [Text.Encoding]::UTF8.GetBytes('contoso-reports-v1')
$script:Region = $null

function Write-Log([string]$msg) {
  try {
    if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt 1MB) { Move-Item -Force $LogPath "$LogPath.old" }
    Add-Content -Path $LogPath -Value ('{0} [{1}] {2}' -f [DateTime]::UtcNow.ToString('o'), $Mode, $msg)
  } catch { }
}

function Get-Region {
  if ($script:Region) { return $script:Region }
  try {
    $r = Invoke-RestMethod -Headers @{ Metadata = 'true' } -TimeoutSec 3 `
      -Uri 'http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text'
    if ($r) { $script:Region = ([string]$r).Trim(); return $script:Region }
  } catch { }
  return 'unknown'
}

function Get-ReportDay([string]$d) {
  $today = [DateTime]::UtcNow.Date
  if (-not $d -or $d -eq 'today') { return $today }
  if ($d -eq 'yesterday') { return $today.AddDays(-1) }
  return [DateTime]::ParseExact($d, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
}

function Format-Utc($v) {
  if ($null -eq $v -or $v -is [DBNull]) { return $null }
  return ([DateTime]$v).ToString('yyyy-MM-ddTHH:mm:ss.fff') + 'Z'
}

function Read-Config {
  if (-not (Test-Path $ConfigPath)) {
    throw 'not configured: C:\reports\config.json is missing (run infra/scripts/reports-vm.sh setup)'
  }
  $c = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
  if ($c.protection -eq 'dpapi-machine') {
    Add-Type -AssemblyName System.Security
    try {
      $bytes = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String([string]$c.password),
        $Entropy, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    } catch {
      throw "credentials cannot be decrypted on this machine (DPAPI LocalMachine): $($_.Exception.Message)"
    }
    $pw = [Text.Encoding]::UTF8.GetString($bytes)
  } else {
    $pw = [string]$c.password
  }
  return [pscustomobject]@{ server = [string]$c.server; database = [string]$c.database; user = [string]$c.user; password = $pw }
}

function Invoke-ReportQuery($cfg, [string]$server, [bool]$readOnly, [DateTime]$day) {
  $b = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
  $b['Data Source'] = "tcp:$server,1433"
  $b['Initial Catalog'] = $cfg.database        # contained user: must connect to the database itself
  $b['User ID'] = $cfg.user
  $b['Password'] = $cfg.password
  $b['Encrypt'] = $true
  $b['TrustServerCertificate'] = $false
  $b['Connect Timeout'] = 15
  $b['Application Name'] = 'contoso-reports'
  if ($readOnly) { $b['ApplicationIntent'] = 'ReadOnly' }
  $conn = New-Object System.Data.SqlClient.SqlConnection $b.ConnectionString
  try {
    $conn.Open()
    $cmd = $conn.CreateCommand()
    $cmd.CommandTimeout = 30
    $cmd.CommandText = @'
SELECT @@SERVERNAME AS server_name, CAST(DATABASEPROPERTYEX(DB_NAME(), 'Updateability') AS nvarchar(20)) AS updateability;
SELECT ISNULL(region, N'unknown') AS region, COUNT_BIG(*) AS cnt, ISNULL(SUM(amount), 0) AS total,
       MIN(created_at) AS first_at, MAX(created_at) AS last_at
FROM dbo.transactions
WHERE created_at >= @from AND created_at < @to
GROUP BY region
ORDER BY region;
'@
    $p1 = $cmd.Parameters.Add('@from', [Data.SqlDbType]::DateTime2); $p1.Value = $day
    $p2 = $cmd.Parameters.Add('@to', [Data.SqlDbType]::DateTime2); $p2.Value = $day.AddDays(1)
    $rd = $cmd.ExecuteReader()
    $meta = @{ server_name = $null; updateability = $null }
    if ($rd.Read()) { $meta.server_name = [string]$rd['server_name']; $meta.updateability = [string]$rd['updateability'] }
    [void]$rd.NextResult()
    $rows = @()
    while ($rd.Read()) {
      $rows += [pscustomobject]@{ region = [string]$rd['region']; count = [int64]$rd['cnt']; amount = [decimal]$rd['total'];
                                  first = $rd['first_at']; last = $rd['last_at'] }
    }
    $rd.Close()
    return [pscustomobject]@{ meta = $meta; rows = $rows; server = $server }
  } finally {
    $conn.Dispose()
  }
}

function New-Report([string]$d) {
  $day = Get-ReportDay $d
  $cfg = Read-Config
  # read-write listener first (current primary), then the read-only listener (geo-secondary) in case the
  # primary region is the one that is down
  $targets = @(@{ name = $cfg.server; ro = $false })
  if ($cfg.server -match '^([^.]+)\.database\.windows\.net$') {
    $targets += @{ name = "$($Matches[1]).secondary.database.windows.net"; ro = $true }
  }
  $q = $null; $errors = @()
  foreach ($t in $targets) {
    try { $q = Invoke-ReportQuery $cfg $t.name $t.ro $day; break }
    catch { $m = $_.Exception.Message; if ($m.Length -gt 300) { $m = $m.Substring(0, 300) }; $errors += "$($t.name): $m" }
  }
  if (-not $q) { throw ('database unreachable: ' + ($errors -join ' | ')) }

  $byRegion = [ordered]@{}
  $count = [int64]0; $total = [decimal]0; $first = $null; $last = $null
  foreach ($r in $q.rows) {
    $byRegion[$r.region] = [ordered]@{ count = $r.count; amount = [decimal]::Round($r.amount, 2) }
    $count += $r.count; $total += $r.amount
    if ($r.first -isnot [DBNull] -and ($null -eq $first -or $r.first -lt $first)) { $first = $r.first }
    if ($r.last -isnot [DBNull] -and ($null -eq $last -or $r.last -gt $last)) { $last = $r.last }
  }
  $now = [DateTime]::UtcNow
  $report = [ordered]@{
    report              = 'daily-payments'
    date                = $day.ToString('yyyy-MM-dd')
    final               = ($day -lt $now.Date)
    generated_at        = $now.ToString('yyyy-MM-ddTHH:mm:ss') + 'Z'
    generated_in_region = (Get-Region)
    generated_on_host   = $env:COMPUTERNAME
    source              = 'live database'
    database            = [ordered]@{ endpoint = $q.server; server = $q.meta.server_name; database = $cfg.database;
                                      updateability = $q.meta.updateability }
    payments_count      = $count
    total_amount        = [decimal]::Round($total, 2)
    by_region           = $byRegion
    first_payment_at    = (Format-Utc $first)
    last_payment_at     = (Format-Utc $last)
  }
  $json = $report | ConvertTo-Json -Depth 6 -Compress
  $file = Join-Path $Root ('payments-{0}.json' -f $report.date)
  $tmp = "$file.$PID.tmp"   # write + rename so a reader never sees a half-written file
  [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding $false))
  Move-Item -Force -Path $tmp -Destination $file
  Write-Log "report $($report.date): $count payments, $($report.total_amount) via $($q.server)"
  return $json
}

function Get-LatestFile {
  Get-ChildItem -Path $Root -Filter 'payments-*.json' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^payments-\d{4}-\d{2}-\d{2}\.json$' } |
    Sort-Object Name -Descending | Select-Object -First 1
}

function Get-LatestReport {
  $f = Get-LatestFile
  if (-not $f) { return $null }
  $o = Get-Content -Raw -Path $f.FullName | ConvertFrom-Json
  $o | Add-Member -NotePropertyName source -NotePropertyValue 'cached' -Force
  $o | Add-Member -NotePropertyName cached_file -NotePropertyValue $f.FullName -Force
  try {
    $age = ([DateTime]::UtcNow - [DateTime]::Parse($o.generated_at, [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::AdjustToUniversal)).TotalMinutes
    $o | Add-Member -NotePropertyName age_minutes -NotePropertyValue ([math]::Round($age, 1)) -Force
  } catch { }
  return $o
}

# ------------------------------------------------------------------ generate mode (scheduled tasks)
if ($Mode -eq 'generate') {
  try { New-Report $Date; exit 0 }
  catch { Write-Log "generate failed: $($_.Exception.Message)"; Write-Output $_.Exception.Message; exit 1 }
}

# ------------------------------------------------------------------ serve mode
function Send-Json($ctx, $obj, [int]$code = 200) {
  if ($obj -is [string]) { $body = $obj } else { $body = $obj | ConvertTo-Json -Depth 6 -Compress }
  $bytes = [Text.Encoding]::UTF8.GetBytes($body)
  $ctx.Response.StatusCode = $code
  $ctx.Response.ContentType = 'application/json; charset=utf-8'
  $ctx.Response.ContentLength64 = $bytes.Length
  $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
  $ctx.Response.Close()
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add('http://+:8080/')
for ($i = 0; ; $i++) {   # the previous instance may still hold the port for a few seconds after a restart
  try { $listener.Start(); break }
  catch { if ($i -ge 30) { Write-Log "cannot listen on :8080: $($_.Exception.Message)"; exit 1 }; Start-Sleep 2 }
}
Write-Log "listening on :8080 (region $(Get-Region))"

while ($listener.IsListening) {
  $ctx = $listener.GetContext()
  try {
    $path = $ctx.Request.Url.AbsolutePath.TrimEnd('/').ToLowerInvariant()
    $method = $ctx.Request.HttpMethod
    if ($path -eq '' -or $path -eq '/health') {
      $f = Get-LatestFile
      $last = $null
      if ($f) { $last = $f.BaseName.Substring(9) }
      Send-Json $ctx ([ordered]@{ service = 'contoso-reports'; status = 'ok'; host = $env:COMPUTERNAME;
                                  region = (Get-Region); last_report = $last })
    } elseif ($path -eq '/report/latest' -and $method -eq 'GET') {
      $o = Get-LatestReport
      if ($o) { Send-Json $ctx $o } else { Send-Json $ctx ([ordered]@{ status = 'error'; reason = 'no report on disk yet' }) 404 }
    } elseif ($path -eq '/report/generate' -and ($method -eq 'GET' -or $method -eq 'POST')) {
      $d = $ctx.Request.QueryString['date']
      if (-not $d) { $d = 'today' }
      try {
        Send-Json $ctx (New-Report $d)
      } catch {
        Write-Log "generate failed: $($_.Exception.Message)"
        Send-Json $ctx ([ordered]@{ status = 'error'; reason = $_.Exception.Message; region = (Get-Region);
                                    latest_cached = (Get-LatestReport) }) 503
      }
    } else {
      Send-Json $ctx ([ordered]@{ status = 'error'; reason = 'not found'; endpoints = @('/health', '/report/latest', '/report/generate') }) 404
    }
  } catch {
    Write-Log "request failed: $($_.Exception.Message)"
    try { Send-Json $ctx ([ordered]@{ status = 'error'; reason = $_.Exception.Message }) 500 } catch { }
  }
}
