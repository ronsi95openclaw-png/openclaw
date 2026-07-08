@echo off
title Hermes Restart (Anthropic)
echo [1/3] Stopping old Hermes gateway (PID 7892)...
taskkill /PID 7892 /F 2>nul
taskkill /IM hermes.exe /F 2>nul
del /Q "C:\Users\ronsi95openclaw\Claude-openclaw\hermes\hermes.pid" 2>nul
timeout /t 3 /nobreak >nul
echo [2/3] Starting Hermes with provider=anthropic config...
set "PY=C:\Users\ronsi95openclaw\Claude-openclaw\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
start "Hermes Gateway" /min "%PY%" "C:\Users\ronsi95openclaw\Claude-openclaw\hermes\launch.py"
timeout /t 5 /nobreak >nul
echo [3/3] Done. Tail Claude-openclaw\hermes\logs\gateway.out to verify.
pause
