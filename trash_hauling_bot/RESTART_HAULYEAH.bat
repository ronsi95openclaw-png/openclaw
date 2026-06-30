@echo off
title HaulYeah Restart (picks up new .env)
echo Stopping existing HaulYeah python process...
wmic process where "executablepath like '%%trash_hauling_bot%%'" delete >nul 2>&1
timeout /t 3 /nobreak >nul
echo Starting HaulYeah supervisor with new env (DRY_RUN=false)...
cd /d "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot"
start "HaulYeah supervisor" start_haulyeah.bat
echo Done. HaulYeah will start in a moment.
timeout /t 2 /nobreak >nul
exit /b 0
