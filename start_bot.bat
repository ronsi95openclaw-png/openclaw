@echo off
:: ClawBot single-instance launcher
:: Kills any existing bot/dashboard instances, then starts fresh

echo Stopping any existing ClawBot instances...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq ClawBot*" 2>nul
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object {$_.MainModule.FileName -like '*venv*'} | Stop-Process -Force" 2>nul
timeout /t 2 /nobreak >nul

echo Starting ClawBot v0.8...
cd /d "%~dp0"

:: Start dashboard in background (separate window)
start "ClawBot Dashboard" .venv\Scripts\python.exe -m dashboard.app

:: Brief pause so dashboard binds port first
timeout /t 2 /nobreak >nul

:: Start Telegram bot (foreground)
.venv\Scripts\python.exe -m content.receiver
