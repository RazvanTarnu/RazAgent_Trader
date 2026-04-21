# install_autostart.ps1 - installs Windows Task Scheduler entries for:
#  1. GodClawTrader_AutoStart  - starts trader at user logon + system boot
#  2. GodClawTrader_Watchdog   - probes every 5 min, restarts if down
# Idempotent: safe to re-run. Uses `schtasks` (works on Win 10/11 Home + Pro).

$ErrorActionPreference = "Stop"
$root = "C:\RazAgent_Trader"
$startScript = Join-Path $root "scripts\start_trader.ps1"
$watchdogScript = Join-Path $root "scripts\watchdog_trader.ps1"

Write-Host "=== install_autostart.ps1 ===" -ForegroundColor Cyan
Write-Host "  root: $root"

if (-not (Test-Path $startScript))    { throw "Missing $startScript" }
if (-not (Test-Path $watchdogScript)) { throw "Missing $watchdogScript" }

# Common arguments for powershell when launched by schtasks.
# Detached, hidden window, bypass execution policy, no profile.
$psBin = "powershell.exe"

function Install-Task($name, $trigger, $tr, $desc) {
    # Delete existing (ignore error if not found)
    schtasks /Query /TN $name 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  removing existing task: $name"
        schtasks /Delete /TN $name /F | Out-Null
    }
    Write-Host "  creating task: $name"
    # Build via XML for richer control (hidden, multiple triggers)
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$desc</Description>
  </RegistrationInfo>
  <Triggers>$trigger</Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$env:USERNAME</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    $tr
  </Actions>
</Task>
"@
    $xmlPath = Join-Path $env:TEMP "${name}.xml"
    [System.IO.File]::WriteAllText($xmlPath, $xml, [System.Text.Encoding]::Unicode)
    schtasks /Create /TN $name /XML $xmlPath /F | Out-Null
    Remove-Item $xmlPath -Force -ErrorAction SilentlyContinue
    Write-Host "    [OK] $name installed"
}

# ---- Task 1: AutoStart on logon ----
$autoStartTrigger = @"
<LogonTrigger>
  <Enabled>true</Enabled>
  <UserId>$env:USERDOMAIN\$env:USERNAME</UserId>
</LogonTrigger>
<BootTrigger>
  <Enabled>true</Enabled>
  <Delay>PT1M</Delay>
</BootTrigger>
"@
$autoStartAction = @"
    <Exec>
      <Command>$psBin</Command>
      <Arguments>-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "$startScript"</Arguments>
      <WorkingDirectory>$root</WorkingDirectory>
    </Exec>
"@
Install-Task "GodClawTrader_AutoStart" $autoStartTrigger $autoStartAction "Starts RazAgent Trader (metrics_server + crypto_bot) at user logon and after system boot"

# ---- Task 2: Watchdog every 5 min ----
$watchdogTrigger = @"
<CalendarTrigger>
  <StartBoundary>$(Get-Date -Format 'yyyy-MM-ddTHH:mm:00')</StartBoundary>
  <Enabled>true</Enabled>
  <ScheduleByDay>
    <DaysInterval>1</DaysInterval>
  </ScheduleByDay>
  <Repetition>
    <Interval>PT5M</Interval>
    <Duration>P1D</Duration>
    <StopAtDurationEnd>false</StopAtDurationEnd>
  </Repetition>
</CalendarTrigger>
"@
$watchdogAction = @"
    <Exec>
      <Command>$psBin</Command>
      <Arguments>-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "$watchdogScript"</Arguments>
      <WorkingDirectory>$root</WorkingDirectory>
    </Exec>
"@
Install-Task "GodClawTrader_Watchdog" $watchdogTrigger $watchdogAction "Probes trader health every 5 min, restarts if metrics_server (port 9100) or bot process is down"

# ---- Post-install summary ----
Write-Host ""
Write-Host "=== Installed tasks ===" -ForegroundColor Cyan
schtasks /Query /TN "GodClawTrader_AutoStart" /V /FO LIST 2>$null | Select-String -Pattern "TaskName|Status|Next Run Time|Last Run|Task To Run"
Write-Host ""
schtasks /Query /TN "GodClawTrader_Watchdog" /V /FO LIST 2>$null | Select-String -Pattern "TaskName|Status|Next Run Time|Last Run|Task To Run"

Write-Host ""
Write-Host "[DONE] Auto-start + watchdog configured." -ForegroundColor Green
Write-Host "  Next boot/logon -> trader starts automatically."
Write-Host "  Every 5 min -> watchdog probes + restarts if down."
Write-Host "  Logs: $root\logs\watchdog.log"
Write-Host ""
Write-Host "To test immediately:" -ForegroundColor Yellow
Write-Host "  schtasks /Run /TN GodClawTrader_AutoStart"
