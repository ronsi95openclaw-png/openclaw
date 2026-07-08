@echo off
setlocal enableextensions
title HaulYeah autostart launcher (guarded)
REM Logon-autostart wrapper for the HaulYeah bot (fixed 2026-06-11).
REM Guards: (1) bot python already running (detected by its venv ExecutablePath,
REM NOT by cmdline - cmdline is just "python main.py"); (2) a supervisor cmd
REM already running. Two bot instances collide on the Telegram token (409).

wmic process where "name='python.exe'" get executablepath 2>nul | findstr /i "trash_hauling_bot" >nul
if not errorlevel 1 (
  echo HaulYeah bot already running -- not launching a duplicate.
  exit /b 0
)
wmic process where "name='cmd.exe'" get commandline 2>nul | findstr /i "start_haulyeah.bat" | findstr /v /i "guarded" >nul
if not errorlevel 1 (
  echo HaulYeah supervisor already running -- not launching a duplicate.
  exit /b 0
)

start "HaulYeah supervisor" /min "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot\start_haulyeah.bat"
exit /b 0
