# start_trader.ps1 — Platform launcher with validation
$ErrorActionPreference = "Stop"
$root = if ($env:RAZAGENT_ROOT) { $env:RAZAGENT_ROOT } else { Split-Path -Parent $PSScriptRoot }

$env:PYTHONPATH = $root
$env:PYTHONUNBUFFERED = "1"
$env:PAPER_MODE = if ($env:PAPER_MODE) { $env:PAPER_MODE } else { "true" }

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$metricsLog = Join-Path $logDir "metrics_server.log"
$metricsErr = Join-Path $logDir "metrics_server.err.log"
$botLog     = Join-Path $logDir "trade_crypto_bot_stdout.log"
$botErr     = Join-Path $logDir "trade_crypto_bot_stderr.log"

Write-Host "=== start_trader.ps1 ===" -ForegroundColor Cyan
Write-Host "  root: $root"
Write-Host "  PAPER_MODE: $env:PAPER_MODE"

function Fail-Startup {
    param([string]$Message)
    Write-Host "  [FATAL] $Message" -ForegroundColor Red
    exit 1
}

# 1. Stop stale processes
Write-Host ""
Write-Host "[1/6] Stopping stale processes..."
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { ($_.CommandLine -like "*RazAgent_Trader*") -and ($_.Name -eq "python.exe") } |
    ForEach-Object {
        Write-Host "  stopping stale PID=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# 2. Python check
Write-Host ""
Write-Host "[2/6] Python check..."
$pyCheck = & python --version 2>&1 | Out-String
Write-Host "  python --version: $($pyCheck.Trim())"
if (-not ($pyCheck -match "Python 3")) {
    Fail-Startup "'python' not resolving to Python 3. Check PATH."
}

# 3. Dependency check
Write-Host ""
Write-Host "[3/6] Dependency check..."
$depCheck = & python -c "import httpx, yaml, fastapi, uvicorn, ccxt; print('ok')" 2>&1
if ($depCheck -notmatch "ok") {
    Fail-Startup "Missing dependencies. Run: pip install -r requirements.txt`n$depCheck"
}
Write-Host "  dependencies: ok"

# 4. Platform validation
Write-Host ""
Write-Host "[4/6] Platform configuration validation..."
Push-Location $root
$validateOut = & python scripts/validate_platform.py 2>&1
$validateCode = $LASTEXITCODE
Pop-Location
Write-Host $validateOut
if ($validateCode -ne 0) {
    Fail-Startup "Platform validation failed (exit $validateCode)"
}

# 5. Launch metrics_server
Write-Host ""
Write-Host "[5/6] Launching metrics_server..."
Clear-Content -Path $metricsLog -ErrorAction SilentlyContinue
Clear-Content -Path $metricsErr -ErrorAction SilentlyContinue
if (-not (Test-Path $metricsLog)) { New-Item -ItemType File -Path $metricsLog -Force | Out-Null }
if (-not (Test-Path $metricsErr)) { New-Item -ItemType File -Path $metricsErr -Force | Out-Null }

$cmdMetrics = "cmd /c start `"`" /B /D `"$root`" python metrics_server.py 1>>`"$metricsLog`" 2>>`"$metricsErr`""
Write-Host "  launching metrics on port 9100"
Invoke-Expression $cmdMetrics
Start-Sleep -Seconds 3

$p9100 = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p9100) {
    Write-Host "  [WARN] Port 9100 not yet listening — check $metricsErr" -ForegroundColor Yellow
}

# 6. Launch trading bot (optional — requires Telegram token)
Write-Host ""
Write-Host "[6/6] Launching trade_crypto_bot..."
$hasToken = & python -c "from shared.keyring_loader import get_credential; print('yes' if get_credential('TRADE_CRYPTO_BOT_TOKEN') else 'no')" 2>&1
if ($hasToken.Trim() -ne "yes") {
    Write-Host "  [SKIP] TRADE_CRYPTO_BOT_TOKEN missing — metrics only" -ForegroundColor Yellow
} else {
    Clear-Content -Path $botLog -ErrorAction SilentlyContinue
    Clear-Content -Path $botErr -ErrorAction SilentlyContinue
    if (-not (Test-Path $botLog)) { New-Item -ItemType File -Path $botLog -Force | Out-Null }
    if (-not (Test-Path $botErr)) { New-Item -ItemType File -Path $botErr -Force | Out-Null }

    $cmdBot = "cmd /c start `"`" /B /D `"$root`" python crypto_bot\trade_crypto_bot.py 1>>`"$botLog`" 2>>`"$botErr`""
    Write-Host "  launching bot (PAPER_MODE=$env:PAPER_MODE)"
    Invoke-Expression $cmdBot
}
Start-Sleep -Seconds 3

# Report
Write-Host ""
Write-Host "=== Post-start diagnostics ==="
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*metrics_server*" -or $_.CommandLine -like "*trade_crypto_bot*" }
if ($procs) {
    foreach ($p in $procs) {
        Write-Host "  PID=$($p.ProcessId) $($p.CommandLine.Substring(0,[Math]::Min(100,$p.CommandLine.Length)))"
    }
} else {
    Write-Host "  [!] no matching Python processes" -ForegroundColor Red
}

$p9100 = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($p9100) {
    Write-Host "  Port 9100 listening (PID=$($p9100.OwningProcess))" -ForegroundColor Green
} else {
    Write-Host "  Port 9100 NOT listening" -ForegroundColor Red
}

Write-Host ""
Write-Host "Startup complete. PAPER_MODE=$env:PAPER_MODE (LIVE requires explicit operator activation)."
