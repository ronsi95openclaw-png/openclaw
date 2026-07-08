$logPath = "$PSScriptRoot\restart_haulyeah_results.txt"
"=== HaulYeah Restart $(Get-Date) ===" | Tee-Object -FilePath $logPath

# Kill any python processes using the trash_hauling_bot venv
$killed = 0
Get-WmiObject Win32_Process -Filter "name='python.exe'" -EA SilentlyContinue | ForEach-Object {
    $cmd = $_.CommandLine
    $exe = $_.ExecutablePath
    if (($exe -match "trash_hauling_bot") -or ($cmd -match "trash_hauling_bot\\main" ) -or
        (($cmd -match "main\.py") -and ($exe -match "trash_hauling_bot"))) {
        "Killing PID $($_.ProcessId): $cmd" | Tee-Object -FilePath $logPath -Append
        Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue
        $killed++
    }
}

# Also kill any start_haulyeah supervisor cmd windows
Get-Process cmd -EA SilentlyContinue | ForEach-Object {
    $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" -EA SilentlyContinue
    if ($wmi.CommandLine -match "start_haulyeah") {
        "Killing supervisor cmd PID $($_.Id)" | Tee-Object -FilePath $logPath -Append
        Stop-Process -Id $_.Id -Force -EA SilentlyContinue
        $killed++
    }
}

"Killed $killed HaulYeah processes" | Tee-Object -FilePath $logPath -Append
Start-Sleep -Seconds 2

# Start fresh
"Starting HaulYeah bot..." | Tee-Object -FilePath $logPath -Append
Start-Process -FilePath "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot\start_haulyeah.bat" -WindowStyle Normal
"Started. Check bot window and data\bot.log for startup messages." | Tee-Object -FilePath $logPath -Append
"=== Done ===" | Tee-Object -FilePath $logPath -Append
Read-Host "Press Enter to close"
