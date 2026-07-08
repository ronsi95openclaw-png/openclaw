@echo off
REM ============================================================
REM HaulYeah Trash Hauling Bot - 24/7 launcher with auto-restart
REM Started automatically at logon by the "HaulYeahBot" scheduled task.
REM ============================================================
cd /d "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot"
call ".venv\Scripts\activate.bat"

:loop
echo [%date% %time%] starting main.py >> "data\supervisor.log"
python main.py >> "data\stdout.log" 2>> "data\stderr.log"
echo [%date% %time%] main.py exited with code %errorlevel% - restarting in 15s >> "data\supervisor.log"
timeout /t 15 /nobreak >nul
goto loop
