@echo off
title OpenRouter Setup — Ronsi95 AI OS
color 0B
echo.
echo  ================================================
echo   Ronsi95 AI OS — OpenRouter Setup
echo   Wiring free cloud models into Hermes
echo  ================================================
echo.
cd /d C:\Users\ronsi95openclaw\Claude-openclaw
call .venv\Scripts\activate.bat 2>nul
python setup_openrouter.py
pause
