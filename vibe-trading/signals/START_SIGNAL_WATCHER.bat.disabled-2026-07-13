@echo off
title MNQ Signal Watcher
echo ============================================================
echo  MNQ Signal Watcher
echo ============================================================
echo.

:: Navigate to this script's directory (vibe-trading\signals\)
cd /d "%~dp0"

:: Try project venv first (.venv at Claude-openclaw root), then system Python
set "VENV=%~dp0..\..\..\.venv\Scripts\python.exe"
set "VENV2=%~dp0..\..\.venv\Scripts\python.exe"

if exist "%VENV%" (
    echo Using venv: %VENV%
    set "PYTHON=%VENV%"
) else if exist "%VENV2%" (
    echo Using venv: %VENV2%
    set "PYTHON=%VENV2%"
) else (
    echo Using system Python
    set "PYTHON=python"
)

echo.
echo Starting signal_watcher.py ...
echo Press Ctrl+C to stop.
echo.

%PYTHON% signal_watcher.py

echo.
echo === Signal Watcher stopped ===
pause
