@echo off
REM Registers the ClawBot watchdog to run every 5 minutes via Task Scheduler.
REM Run this ONCE, manually. Re-running with /f overwrites the existing task.
schtasks /create /tn "ClawBot-Watchdog" /tr "\"%~dp0run_watchdog.bat\"" /sc minute /mo 5 /f
echo.
echo Watchdog scheduled: "ClawBot-Watchdog" runs every 5 minutes.
echo To remove it later:  schtasks /delete /tn "ClawBot-Watchdog" /f
pause
