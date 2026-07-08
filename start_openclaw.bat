@echo off
REM OpenClaw Startup Script — starts dashboard + bot, prevents duplicates
cd /d C:\Users\ronsi95openclaw\Claude-openclaw
set PYTHONPATH=C:\Users\ronsi95openclaw\Claude-openclaw

echo [OpenClaw] Starting Dashboard...
start "OpenClaw-Dashboard" /min .venv\Scripts\python.exe dashboard\app.py

timeout /t 3 /nobreak >nul

echo [OpenClaw] Starting Telegram Bot...
start "OpenClaw-Bot" /min .venv\Scripts\python.exe content\receiver.py

echo [OpenClaw] Both services started. Dashboard: http://localhost:8080
echo [OpenClaw] Tailscale: http://100.70.89.27:8080
