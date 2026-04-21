# stop_trader.ps1 - stops trade_crypto_bot and metrics_server.

Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*RazAgent_Trader*" -and $_.Name -eq "python.exe" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 500
Write-Host "[OK] trader stopped"
