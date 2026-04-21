# watchdog_trader.ps1 - checks trader processes and restarts them if down.
# Runs every 5 min via Task Scheduler (GodClawTrader_Watchdog).
# Idempotent: does nothing if healthy, minimal cost when OK.

$ErrorActionPreference = "Continue"
$root = "C:\RazAgent_Trader"
$logDir = Join-Path $root "logs"
$wdLog = Join-Path $logDir "watchdog.log"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Log($level, $msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $wdLog -Value "[$ts] [$level] $msg" -Encoding UTF8
}

# Probe metrics_server :9100
$metricsUp = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9100/healthz" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $metricsUp = $true }
} catch { $metricsUp = $false }

# Probe bot process
$botUp = $false
$botProc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*trade_crypto_bot.py*" } |
    Select-Object -First 1
if ($botProc) { $botUp = $true }

# Check token presence
$hasToken = (& python -c "import keyring; print('yes' if keyring.get_password('RazAgentTrader','TRADE_CRYPTO_BOT_TOKEN') else 'no')" 2>$null).Trim()

$status = "metrics=$metricsUp bot=$botUp token=$hasToken"

if ($metricsUp -and ($botUp -or $hasToken -ne "yes")) {
    # Healthy (or bot skipped due to missing token — still OK state)
    Log "INFO" "healthy: $status"
    exit 0
}

# Unhealthy -> restart
Log "WARN" "unhealthy, restarting: $status"

# Cooldown: don't restart if we already restarted within the last 4 minutes
$lastRestartFile = Join-Path $logDir ".last_watchdog_restart"
if (Test-Path $lastRestartFile) {
    $lastRestart = [datetime]::Parse((Get-Content $lastRestartFile -Raw).Trim())
    if (((Get-Date) - $lastRestart).TotalMinutes -lt 4) {
        Log "WARN" "cooldown active (last restart $lastRestart), skipping this cycle"
        exit 0
    }
}

(Get-Date).ToString("o") | Out-File -FilePath $lastRestartFile -Encoding UTF8

$startScript = Join-Path $root "scripts\start_trader.ps1"
if (Test-Path $startScript) {
    Log "INFO" "invoking start_trader.ps1"
    & powershell -ExecutionPolicy Bypass -NoProfile -File $startScript *>> $wdLog
    Log "INFO" "restart cycle complete"
} else {
    Log "ERROR" "start_trader.ps1 missing at $startScript"
    exit 1
}

# Post-restart probe (10s grace)
Start-Sleep -Seconds 10
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9100/healthz" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) {
        Log "INFO" "post-restart healthz OK"
    }
} catch {
    Log "ERROR" "post-restart healthz still FAIL: $_"
}
