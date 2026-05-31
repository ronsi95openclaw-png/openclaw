@echo off
setlocal disabledelayedexpansion
title Sync project memory to Obsidian vault

REM Mirrors each project's memory\*.md notes into the matching vault section.
REM Uses %USERPROFILE% so it is not tied to a specific Windows username, and
REM points at the real active vault (Documents\Obsidian Vault), not a new one.

set "VAULT=%USERPROFILE%\Documents\Obsidian Vault"
set "REPO=%~dp0.."

if not exist "%VAULT%\" (
  echo Vault not found at "%VAULT%" -- edit VAULT in this script.
  exit /b 1
)

REM  source memory dir                          vault target section            label
call :sync "%REPO%\trash_hauling_bot\memory"    "%VAULT%\10 - HaulYA'LL!"        "HaulYeah"
call :sync "%REPO%\memory"                       "%VAULT%\20 - OpenClaw\Memory"  "OpenClaw"
call :sync "%REPO%\memory\strategy"              "%VAULT%\20 - OpenClaw\Memory\Strategy"  "OpenClaw-Strategy"

echo.
echo Vault sync complete. Open Obsidian to review.
exit /b 0

:sync
if not exist "%~1\*.md" (
  echo   [%~3] no memory notes yet -- skipped
  exit /b 0
)
if not exist "%~2\" mkdir "%~2"
echo   [%~3] syncing memory notes...
xcopy "%~1\*.md" "%~2\" /Y /Q >nul
exit /b 0
