@echo off
REM Run the LiquiditySweep paper-watch with the right cwd + python venv.
REM Invoked by the Windows scheduled task `ClawBot-LiquiditySweep-Watch`.
REM Daily cadence; 1d candles only refresh once per day.

setlocal
set "REPO=%~dp0.."
cd /d "%REPO%" || exit /b 1

REM Prefer the trash_hauling_bot venv if present (currently the active env
REM that has all required deps). Fall back to system python on PATH.
set "VENV_PY=%REPO%\trash_hauling_bot\.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" "%REPO%\infra\paper_watch_liquiditysweep.py"
) else (
    python "%REPO%\infra\paper_watch_liquiditysweep.py"
)
exit /b %ERRORLEVEL%
