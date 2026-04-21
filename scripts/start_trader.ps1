# start_trader.ps1 - starts metrics_server (always) + trade_crypto_bot (if token present).
# Idempotent: stops stale instances first.
# CRITICAL: sets PYTHONPATH so `from shared.X` imports work when bot is at crypto_bot/*.py.

$ErrorActionPreference = "Continue"
$root = "C:\RazAgent_Trader"
$python = "python"

# Ensure shared/ is importable from anywhere (needed because bot runs as crypto_bot/trade_crypto_bot.py
# from cwd=$root, which puts sys.path[0] = crypto_bot/, not $root).
$env:PYTHONPATH = $root
$env:PYTHONUNBUFFERED = "1"

# Log dir
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

# Stop any stale instances (but keep metrics if already healthy)
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*trade_crypto_bot.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

# --- metrics_server ---
$metricsAlive = $false
try {
    $probe = Invoke-WebRequest -Uri "http://127.0.0.1:9100/healthz" -UseBasicParsing -TimeoutSec 2
    if ($probe.StatusCode -eq 200) {
        $metricsAlive = $true
        Write-Host "[OK] metrics_server already running on :9100 (kept)"
    }
} catch { $metricsAlive = $false }

if (-not $metricsAlive) {
    $metricsLog = Join-Path $logDir "metrics_server.log"
    $metricsErr = Join-Path $logDir "metrics_server.err.log"
    Start-Process -WindowStyle Hidden -FilePath $python `
                  -ArgumentList "metrics_server.py" `
                  -WorkingDirectory $root `
                  -RedirectStandardOutput $metricsLog `
                  -RedirectStandardError $metricsErr
    Start-Sleep -Seconds 2
    Write-Host "[OK] metrics_server started on port 9100"
}

# --- trade_crypto_bot (only if token in keyring) ---
$hasToken = & $python -c "import keyring; print('yes' if keyring.get_password('RazAgentTrader','TRADE_CRYPTO_BOT_TOKEN') else 'no')"
if ($hasToken.Trim() -eq "yes") {
    $botLog = Join-Path $logDir "trade_crypto_bot.log"
    $botErr = Join-Path $logDir "trade_crypto_bot.err.log"
    # Truncate old logs so diagnostic is clean
    "" | Out-File -FilePath $botLog -Encoding UTF8
    "" | Out-File -FilePath $botErr -Encoding UTF8

    Start-Process -WindowStyle Hidden -FilePath $python `
                  -ArgumentList "crypto_bot\trade_crypto_bot.py" `
                  -WorkingDirectory $root `
                  -RedirectStandardOutput $botLog `
                  -RedirectStandardError $botErr
    Start-Sleep -Seconds 3
    Write-Host "[OK] trade_crypto_bot started (PYTHONPATH=$root)"
    Write-Host "     logs: $botLog"
} else {
    Write-Host "[SKIP] TRADE_CRYPTO_BOT_TOKEN not in keyring yet"
}

# Show tail after startup
Start-Sleep -Seconds 2
$botErr = Join-Path $logDir "trade_crypto_bot.err.log"
if (Test-Path $botErr) {
    $errSize = (Get-Item $botErr).Length
    if ($errSize -gt 0) {
        Write-Host ""
        Write-Host "[!] trade_crypto_bot.err.log has content:" -ForegroundColor Yellow
        Get-Content $botErr -Tail 10
    }
}
