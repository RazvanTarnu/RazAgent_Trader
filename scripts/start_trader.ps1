# start_trader.ps1 - robust launcher using cmd /c (Start-Process -RedirectStd* is fragile on PS 5.1).
$ErrorActionPreference = "Continue"
$root = "C:\RazAgent_Trader"

# Set env for current session AND for spawned children (via cmd /c inheritance).
$env:PYTHONPATH = $root
$env:PYTHONUNBUFFERED = "1"

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$metricsLog = Join-Path $logDir "metrics_server.log"
$metricsErr = Join-Path $logDir "metrics_server.err.log"
# NOTE: trade_crypto_bot.py already has an internal logging.FileHandler that writes
# to logs/trade_crypto_bot.log. If cmd redirects stdout to that SAME file,
# Windows denies the second append-open -> PermissionError, bot crashes silently.
# So cmd stdout+stderr go to *_stdout.log / *_stderr.log; the bot's own logger
# keeps owning trade_crypto_bot.log for structured logs.
$botLog     = Join-Path $logDir "trade_crypto_bot_stdout.log"
$botErr     = Join-Path $logDir "trade_crypto_bot_stderr.log"

Write-Host "=== start_trader.ps1 ===" -ForegroundColor Cyan
Write-Host "  root: $root"
Write-Host "  PYTHONPATH: $env:PYTHONPATH"

# 1. Stop stale
Write-Host ""
Write-Host "[1/4] Stopping stale processes..."
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { ($_.CommandLine -like "*RazAgent_Trader*") -and ($_.Name -eq "python.exe") } |
    ForEach-Object {
        Write-Host "  stopping stale PID=$($_.ProcessId) cmd=$($_.CommandLine.Substring(0,[Math]::Min(80,$_.CommandLine.Length)))"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# 2. Verify python
Write-Host ""
Write-Host "[2/4] Python check..."
$pyCheck = & python --version 2>&1 | Out-String
Write-Host "  python --version: $($pyCheck.Trim())"
if (-not ($pyCheck -match "Python 3")) {
    Write-Host "  [FATAL] 'python' not resolving to Python 3. Check PATH." -ForegroundColor Red
    exit 1
}

# 3. Launch metrics_server using cmd /c start (detached, reliable redirect)
Write-Host ""
Write-Host "[3/4] Launching metrics_server..."
# Truncate logs via PowerShell (not cmd — avoids BOM issues)
Clear-Content -Path $metricsLog -ErrorAction SilentlyContinue
Clear-Content -Path $metricsErr -ErrorAction SilentlyContinue
if (-not (Test-Path $metricsLog)) { New-Item -ItemType File -Path $metricsLog -Force | Out-Null }
if (-not (Test-Path $metricsErr)) { New-Item -ItemType File -Path $metricsErr -Force | Out-Null }

# cmd /c start /B = launch detached without a console window
# NOTE: 'start "" /B ...' — the empty quoted string IS the window title.
# Without it, 'start /B "metrics_server"' makes Windows try to launch a program
# called metrics_server (error: Windows cannot find 'metrics_server').
$cmdMetrics = "cmd /c start `"`" /B /D `"$root`" python metrics_server.py 1>>`"$metricsLog`" 2>>`"$metricsErr`""
Write-Host "  $cmdMetrics"
Invoke-Expression $cmdMetrics
Start-Sleep -Seconds 3

# 4. Launch bot
Write-Host ""
Write-Host "[4/4] Launching trade_crypto_bot..."
$hasToken = & python -c "import keyring; print('yes' if keyring.get_password('RazAgentTrader','TRADE_CRYPTO_BOT_TOKEN') else 'no')" 2>&1
if ($hasToken.Trim() -ne "yes") {
    Write-Host "  [SKIP] token missing (hasToken=$hasToken)" -ForegroundColor Yellow
} else {
    Clear-Content -Path $botLog -ErrorAction SilentlyContinue
    Clear-Content -Path $botErr -ErrorAction SilentlyContinue
    if (-not (Test-Path $botLog)) { New-Item -ItemType File -Path $botLog -Force | Out-Null }
    if (-not (Test-Path $botErr)) { New-Item -ItemType File -Path $botErr -Force | Out-Null }

    $cmdBot = "cmd /c start `"`" /B /D `"$root`" python crypto_bot\trade_crypto_bot.py 1>>`"$botLog`" 2>>`"$botErr`""
    Write-Host "  $cmdBot"
    Invoke-Expression $cmdBot
}
Start-Sleep -Seconds 5

# 5. Report
Write-Host ""
Write-Host "=== Post-start diagnostics ==="
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*RazAgent_Trader*" -or $_.CommandLine -like "*metrics_server*" -or $_.CommandLine -like "*trade_crypto_bot*" }
if ($procs) {
    Write-Host "  Python processes found:"
    foreach ($p in $procs) {
        Write-Host "    PID=$($p.ProcessId) - $($p.CommandLine.Substring(0,[Math]::Min(100,$p.CommandLine.Length)))"
    }
} else {
    Write-Host "  [!] no Python processes matching RazAgent_Trader" -ForegroundColor Red
}

$p9100 = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($p9100) {
    Write-Host "  Port 9100 listening by PID=$($p9100.OwningProcess)" -ForegroundColor Green
} else {
    Write-Host "  Port 9100 NOT listening" -ForegroundColor Red
}

Write-Host ""
Write-Host "  metrics_server.err.log tail:"
if ((Test-Path $metricsErr) -and ((Get-Item $metricsErr).Length -gt 0)) {
    Get-Content $metricsErr -Tail 10 | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "    (empty or missing)"
}

Write-Host ""
Write-Host "  trade_crypto_bot_stderr.log tail:"
if ((Test-Path $botErr) -and ((Get-Item $botErr).Length -gt 0)) {
    Get-Content $botErr -Tail 10 | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "    (empty or missing)"
}
