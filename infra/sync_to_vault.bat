@echo off
setlocal disabledelayedexpansion
title Sync project memory + knowledge graph to Obsidian vault

REM Mirrors each project's memory\*.md notes into the matching vault section,
REM using the DOMAIN_ prefix convention established in the 2026-05-31 vault reorg.
REM Uses %USERPROFILE% so it is not tied to a specific Windows username.

set "VAULT=%USERPROFILE%\Documents\Obsidian Vault"
set "REPO=%~dp0.."

if not exist "%VAULT%\" (
  echo Vault not found at "%VAULT%" -- edit VAULT in this script.
  exit /b 1
)

REM  source dir                                vault target                              label               prefix
call :sync "%REPO%\trash_hauling_bot\memory"  "%VAULT%\10 - HaulYA'LL!"                "HaulYeah"          HAULYALL_
call :sync "%REPO%\memory"                    "%VAULT%\20 - OpenClaw\Memory"            "OpenClaw"          OPENCLAW_
call :sync "%REPO%\memory\strategy"           "%VAULT%\20 - OpenClaw\Memory\Strategy"   "OpenClaw-Strategy" OPENCLAW_

call :sync_graph "%REPO%\graphify-out"        "%VAULT%\20 - OpenClaw\Knowledge-Graph"   "Hermes-Graph"

echo.
echo Vault sync complete. Open Obsidian to review.
exit /b 0


REM ── :sync  source  target  label  prefix ────────────────────────────────────
REM Copies *.md from source into target, prepending prefix to each filename.
REM Example: ACTIVE_TASKS.md -> OPENCLAW_ACTIVE_TASKS.md
:sync
if not exist "%~1\*.md" (
  echo   [%~3] no notes yet -- skipped
  exit /b 0
)
if not exist "%~2\" mkdir "%~2"
echo   [%~3] syncing notes with prefix %~4 ...
for %%F in ("%~1\*.md") do (
  copy "%%F" "%~2\%~4%%~nxF" /Y >nul
)
exit /b 0


REM ── :sync_graph  graphify-out  target  label ────────────────────────────────
REM Copies GRAPH_REPORT.md -> OPENCLAW_GRAPH_REPORT.md in the Knowledge-Graph section.
:sync_graph
if not exist "%~1\GRAPH_REPORT.md" (
  echo   [%~3] no graph report yet -- run /hermes now in Telegram first
  exit /b 0
)
if not exist "%~2\" mkdir "%~2"
echo   [%~3] syncing knowledge graph report...
copy "%~1\GRAPH_REPORT.md" "%~2\OPENCLAW_GRAPH_REPORT.md" /Y >nul
exit /b 0
