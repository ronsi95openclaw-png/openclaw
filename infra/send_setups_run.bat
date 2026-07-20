@echo off
REM ============================================================
REM  send_setups_run.bat - run send_setups.py via the repo venv
REM  and push ClawBot trade-setup cards to Telegram (manual entry).
REM  Called by the "ClawBot-SendSetups" scheduled task.
REM ============================================================
setlocal
set "ROOT=C:\Users\ronsi95openclaw\Claude-openclaw"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%ROOT%"
if not exist "data\logs" mkdir "data\logs"
"%PY%" send_setups.py >> "data\logs\send_setups.log" 2>&1
