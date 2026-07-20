@echo off
title Hermes Web Dashboard Launcher
setlocal

set "HERMES=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe"

if not exist "%HERMES%" (
  echo [ERROR] hermes.exe not found at:
  echo   %HERMES%
  echo.
  echo Install hermes-agent and try again.
  pause
  exit /b 1
)

REM Already running?
netstat -ano | findstr ":9119" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo Hermes web dashboard already listening on :9119 -- opening browser.
  start "" "http://127.0.0.1:9119"
  exit /b 0
)

echo ============================================================
echo  Hermes Web Dashboard
echo  Starting: %HERMES% web
echo  URL: http://127.0.0.1:9119
echo  (First run builds the Vite frontend -- may take 1-2 min)
echo ============================================================
echo.

REM Launch hermes web in its own console so the server keeps running
start "Hermes Web Dashboard" cmd /k ""%HERMES%" dashboard"

echo Waiting for port 9119 to come up...
set /a TRIES=0
:WAIT_LOOP
timeout /t 3 /nobreak >nul
set /a TRIES+=1
netstat -ano | findstr ":9119" | findstr "LISTENING" >nul
if not errorlevel 1 goto :OPEN
if %TRIES% lss 40 goto :WAIT_LOOP
echo [TIMEOUT] Dashboard didn't come up after 2 minutes.
echo Check the "Hermes Web Dashboard" console window for errors.
pause
exit /b 1

:OPEN
echo.
echo Dashboard is live! Opening http://127.0.0.1:9119 ...
start "" "http://127.0.0.1:9119"
exit /b 0
