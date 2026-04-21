# status.ps1 - quick status check for RazAgent_Trader on laptop.
$root = "C:\RazAgent_Trader"

Write-Host "=== RazAgent Trader Status ===" -ForegroundColor Cyan
Write-Host ""

# 1. Scheduled Tasks
Write-Host "[Task Scheduler]"
foreach ($name in @("GodClawTrader_AutoStart","GodClawTrader_Watchdog")) {
    $info = schtasks /Query /TN $name /FO LIST 2>$null
    if ($LASTEXITCODE -eq 0) {
        $state = ($info | Select-String "^Status:\s+(.+)$").Matches.Groups[1].Value.Trim()
        $last  = ($info | Select-String "^Last Run Time:\s+(.+)$").Matches.Groups[1].Value.Trim()
        $next  = ($info | Select-String "^Next Run Time:\s+(.+)$").Matches.Groups[1].Value.Trim()
        Write-Host "  $name : state=$state last=$last next=$next"
    } else {
        Write-Host "  $name : NOT INSTALLED" -ForegroundColor Yellow
    }
}

# 2. Processes
Write-Host ""
Write-Host "[Processes]"
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { ($_.CommandLine -like "*RazAgent_Trader*") -and ($_.Name -eq "python.exe") }
if ($procs) {
    foreach ($p in $procs) {
        $short = $p.CommandLine
        if ($short.Length -gt 90) { $short = $short.Substring(0,90) + "..." }
        Write-Host "  PID=$($p.ProcessId)  start=$($p.CreationDate)  cmd=$short"
    }
} else {
    Write-Host "  (no trader processes running)" -ForegroundColor Yellow
}

# 3. Port
Write-Host ""
Write-Host "[Port 9100]"
$p = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($p) {
    Write-Host "  listening PID=$($p.OwningProcess)" -ForegroundColor Green
    try {
        $h = Invoke-WebRequest -Uri "http://127.0.0.1:9100/healthz" -UseBasicParsing -TimeoutSec 3
        Write-Host "  /healthz: $($h.StatusCode) $($h.Content)"
    } catch {
        Write-Host "  /healthz: FAIL $($_.Exception.Message)" -ForegroundColor Red
    }
} else {
    Write-Host "  NOT listening" -ForegroundColor Red
}

# 4. Logs
Write-Host ""
Write-Host "[Recent log entries]"
foreach ($log in @("watchdog.log","trade_crypto_bot.log","trade_crypto_bot_stderr.log","trade_crypto_bot_stdout.log","metrics_server.log","metrics_server.err.log")) {
    $lp = Join-Path $root "logs\$log"
    if (Test-Path $lp) {
        $size = (Get-Item $lp).Length
        Write-Host "  $log ($size bytes):"
        if ($size -gt 0) {
            Get-Content $lp -Tail 3 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
        }
    }
}
