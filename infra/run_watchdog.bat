@echo off
REM Runs the ClawBot watchdog once using the project virtualenv.
REM Used by the scheduled task created by install_watchdog.bat.
cd /d "%~dp0.."
"%~dp0..\.venv\Scripts\python.exe" -m infra.watchdog
