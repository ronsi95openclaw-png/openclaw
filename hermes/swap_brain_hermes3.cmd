@echo off
REM ============================================================
REM  One command to put Hermes Agent on the hermes3:8b brain.
REM  Run this AFTER the GPU driver update (>=570) + reboot.
REM  Double-click it, or run from a terminal.
REM ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0swap_brain_hermes3.ps1"
echo.
pause
