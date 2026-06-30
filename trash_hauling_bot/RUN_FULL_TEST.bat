@echo off
title HaulYeah — Full System Test
cd /d "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot"
call ".venv\Scripts\activate.bat"
echo.
echo ============================================================
echo  HaulYeah Full System Test
echo  Tests: Config, Google Sheets, Outreach, Telegram, FB
echo ============================================================
echo.
python test_full_system.py
echo.
pause
