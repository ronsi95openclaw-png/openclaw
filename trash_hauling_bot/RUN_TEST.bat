@echo off
title HaulYeah Scraper Test
cd /d "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot"
call ".venv\Scripts\activate.bat"
echo.
echo Running scraper test (keyword: junk removal)...
echo This opens a headless Chrome and hits FB Marketplace.
echo Should take 10-20 seconds.
echo.
python test_scraper.py
echo.
pause
