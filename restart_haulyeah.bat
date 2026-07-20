@echo off
title HaulYeah Restart
echo Stopping old HaulYeah processes...

REM Kill any python using the trash_hauling_bot venv
for /f "tokens=2" %%p in ('wmic process where "ExecutablePath like '%%trash_hauling_bot%%' and name='python.exe'" get ProcessId /format:list 2^>nul ^| findstr /r "[0-9]"') do (
    echo Killing PID %%p
    taskkill /PID %%p /F >nul 2>&1
)

REM Also kill supervisor loops
for /f "tokens=2" %%p in ('wmic process where "CommandLine like '%%start_haulyeah%%' and name='cmd.exe'" get ProcessId /format:list 2^>nul ^| findstr /r "[0-9]"') do (
    echo Killing supervisor %%p
    taskkill /PID %%p /F >nul 2>&1
)

timeout /t 3 /nobreak >nul
echo Starting HaulYeah bot...
start "HaulYeah Bot" /min cmd /c "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot\start_haulyeah.bat"
echo Done - bot starting in background. Check data\bot.log in a moment.
timeout /t 5 /nobreak >nul
