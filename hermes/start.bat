@echo off
REM ============================================================
REM  hermes\start.bat - launch the Hermes gateway + write a pidfile.
REM  Run AFTER reboot and `hermes gateway setup` (Telegram token).
REM
REM  NOTE: if you used `hermes gateway install` (background service),
REM  you do NOT need this - the service already runs. Use ONE, not both.
REM ============================================================
setlocal
set "PY=C:\Users\ronsi95openclaw\Claude-openclaw\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0launch.py"
echo.
pause
