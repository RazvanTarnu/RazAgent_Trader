# uninstall_autostart.ps1 - removes GodClawTrader Task Scheduler entries.
foreach ($name in @("GodClawTrader_AutoStart","GodClawTrader_Watchdog")) {
    schtasks /Query /TN $name 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        schtasks /Delete /TN $name /F | Out-Null
        Write-Host "[OK] removed $name"
    } else {
        Write-Host "[SKIP] $name not installed"
    }
}
