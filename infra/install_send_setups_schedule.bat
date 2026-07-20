@echo off
REM Registers a daily 7:00 AM task that sends ClawBot trade setups to Telegram.
REM Run this ONCE, manually. Re-running with /f overwrites the existing task.
schtasks /create /tn "ClawBot-SendSetups" /tr "\"%~dp0send_setups_run.bat\"" /sc daily /st 07:00 /f
echo.
echo Scheduled: "ClawBot-SendSetups" runs daily at 07:00 (local time).
echo To remove it later:  schtasks /delete /tn "ClawBot-SendSetups" /f
pause
