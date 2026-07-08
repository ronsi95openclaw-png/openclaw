$logPath = "$PSScriptRoot\kill_setup_telegram_results.txt"
$targetPids = @(24476, 22932, 19376, 24572)

"=== PID Check $(Get-Date) ===" | Out-File $logPath -Encoding UTF8

foreach ($p in $targetPids) {
    $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
    if (-not $proc) {
        "PID $p`: NOT FOUND (already gone - skip)" | Tee-Object -FilePath $logPath -Append
        continue
    }
    $wmi  = Get-WmiObject Win32_Process -Filter "ProcessId=$p" -EA SilentlyContinue
    $cmd  = if ($wmi) { $wmi.CommandLine } else { "(no cmdline)" }
    $name = $proc.ProcessName
    "PID $p`: [$name]  CMD: $cmd" | Tee-Object -FilePath $logPath -Append

    if ($name -eq "python" -and $cmd -match "setup_telegram") {
        "  --> CONFIRMED setup_telegram.py -- KILLING" | Tee-Object -FilePath $logPath -Append
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        if (Get-Process -Id $p -ErrorAction SilentlyContinue) {
            "  --> WARNING: still alive after kill!" | Tee-Object -FilePath $logPath -Append
        } else {
            "  --> KILLED OK" | Tee-Object -FilePath $logPath -Append
        }
    } elseif ($name -eq "python") {
        "  --> Python but NOT setup_telegram -- SKIPPING (live bot process)" | Tee-Object -FilePath $logPath -Append
    } else {
        "  --> Not python -- SKIPPING" | Tee-Object -FilePath $logPath -Append
    }
}

"" | Out-File $logPath -Append
"=== Remaining python.exe processes ===" | Out-File $logPath -Append
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" -EA SilentlyContinue
    "  PID $($_.Id): $($wmi.CommandLine)" | Tee-Object -FilePath $logPath -Append
}
"=== Done ===" | Out-File $logPath -Append
Write-Host "Done. See: $logPath" -ForegroundColor Green
Read-Host "Press Enter to close"
