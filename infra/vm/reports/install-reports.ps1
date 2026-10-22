<#
  Installs / updates the Contoso payments report service on dra-vm-reports (idempotent).
  Run through Azure Run Command, as SYSTEM:
    - by infra/modules/iaas.bicep (no parameters: installs the service only, keeps an existing config.json)
    - by infra/scripts/reports-vm.sh setup (protected parameters: also writes the SQL read-only credentials)
  __SERVICE_B64__ is replaced with the base64 of reports-service.ps1 before the script is sent.

  Parameters arrive as named script parameters (managed Run Command); environment variables of the same
  name are accepted as a fallback. The password is never written to output.
#>
param(
  [string]$SqlServer = '',
  [string]$SqlDatabase = 'paymentsdb',
  [string]$ReaderUser = 'reports_reader',
  [string]$ReaderPassword = '',
  [string]$Protection = 'dpapi-machine'   # or 'acl-only'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
foreach ($n in 'SqlServer', 'ReaderPassword') {
  if (-not (Get-Variable $n -ValueOnly) -and (Test-Path "env:$n")) { Set-Variable $n (Get-Item "env:$n").Value }
}
if ($env:Protection) { $Protection = $env:Protection }

$Root = 'C:\reports'
$Svc = Join-Path $Root 'reports-service.ps1'
$Cfg = Join-Path $Root 'config.json'
$Entropy = [Text.Encoding]::UTF8.GetBytes('contoso-reports-v1')
New-Item -ItemType Directory -Force -Path $Root | Out-Null

# ---------------------------------------------------------------- 1. stop the running service (old or new)
foreach ($t in 'ContosoReports') {
  if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
}
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
  Where-Object { $_.CommandLine -match 'C:\\reports\\(app|reports-service)\.ps1' -and $_.CommandLine -notmatch '-Mode generate' -and $_.ProcessId -ne $PID } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ---------------------------------------------------------------- 2. service script
$code = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SERVICE_B64__'))
[IO.File]::WriteAllText($Svc, $code, (New-Object Text.UTF8Encoding $false))
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Root 'app.ps1')   # the old dummy service

# ---------------------------------------------------------------- 3. credentials (only when supplied)
if ($ReaderPassword) {
  if (-not $SqlServer) { throw 'SqlServer is required with ReaderPassword' }
  if ($Protection -eq 'dpapi-machine') {
    Add-Type -AssemblyName System.Security
    $blob = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($ReaderPassword), $Entropy,
      [Security.Cryptography.DataProtectionScope]::LocalMachine)
    $stored = [Convert]::ToBase64String($blob)
    $check = [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect($blob, $Entropy,
      [Security.Cryptography.DataProtectionScope]::LocalMachine))
    if ($check -ne $ReaderPassword) { throw 'DPAPI round-trip check failed' }
  } elseif ($Protection -eq 'acl-only') {
    $stored = $ReaderPassword
  } else { throw "unknown protection '$Protection' (dpapi-machine | acl-only)" }
  # create the file empty, lock it down, then write the secret into it
  if (-not (Test-Path $Cfg)) { New-Item -ItemType File -Path $Cfg | Out-Null }
  & icacls.exe $Cfg /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls failed ($LASTEXITCODE)" }
  $json = [ordered]@{ server = $SqlServer; database = $SqlDatabase; user = $ReaderUser; protection = $Protection;
                      password = $stored; written_at = [DateTime]::UtcNow.ToString('o') } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText($Cfg, $json, (New-Object Text.UTF8Encoding $false))
  Write-Output "config: written ($Protection, SYSTEM + Administrators only) for $ReaderUser@$SqlServer/$SqlDatabase"
} elseif (Test-Path $Cfg) {
  & icacls.exe $Cfg /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
  Write-Output 'config: kept existing C:\reports\config.json'
} else {
  Write-Output 'config: none yet (run infra/scripts/reports-vm.sh setup); /report/generate answers 503 until then'
}

# ---------------------------------------------------------------- 4. scheduled tasks (all SYSTEM)
$ps = 'powershell.exe'
$base = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Svc"
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$today = [DateTime]::UtcNow.ToString('yyyy-MM-dd')

# HTTP service: at boot, never time out, restart if it dies
$svcSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 99 `
  -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'ContosoReports' -Force -Principal $principal -Settings $svcSettings `
  -Action (New-ScheduledTaskAction -Execute $ps -Argument "$base -Mode serve") `
  -Trigger (New-ScheduledTaskTrigger -AtStartup) | Out-Null

$genSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

# Daily at 00:05 UTC: close out yesterday's report (final numbers). StartBoundary in UTC ("Z") so the
# schedule does not depend on the VM's time zone.
$daily = New-ScheduledTaskTrigger -Daily -At '00:05'
$daily.StartBoundary = "${today}T00:05:00Z"
Register-ScheduledTask -TaskName 'ContosoReportsDaily' -Force -Principal $principal -Settings $genSettings `
  -Action (New-ScheduledTaskAction -Execute $ps -Argument "$base -Mode generate -Date yesterday") `
  -Trigger $daily | Out-Null

# Hourly at :35: today's report so far. Keeps a recent report on disk, which ASR replicates to Region B.
$hourly = New-ScheduledTaskTrigger -Once -At '00:35' -RepetitionInterval (New-TimeSpan -Hours 1)
$hourly.StartBoundary = "${today}T00:35:00Z"
Register-ScheduledTask -TaskName 'ContosoReportsHourly' -Force -Principal $principal -Settings $genSettings `
  -Action (New-ScheduledTaskAction -Execute $ps -Argument "$base -Mode generate -Date today") `
  -Trigger $hourly | Out-Null

# ---------------------------------------------------------------- 5. start + health check
Start-ScheduledTask -TaskName 'ContosoReports'
$h = $null
for ($i = 0; $i -lt 30 -and -not $h; $i++) {
  try { $h = Invoke-RestMethod -TimeoutSec 3 -Uri 'http://localhost:8080/health' } catch { Start-Sleep 2 }
}
if (-not $h) { throw 'reports service did not answer on :8080 after install' }
Write-Output ('health: ' + ($h | ConvertTo-Json -Compress))
Write-Output ('tasks: ' + ((Get-ScheduledTask -TaskName 'ContosoReports*' | ForEach-Object { "$($_.TaskName)=$($_.State)" }) -join ', '))
