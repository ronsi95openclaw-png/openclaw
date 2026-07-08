@echo off
echo ================================================
echo  HaulYA'LL - Facebook Login Setup
echo  Run this ONCE to save your FB session cookie.
echo  YOU will type your credentials in the browser.
echo  Claude never sees your username or password.
echo ================================================
echo.
cd /d "C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot"
echo Activating virtual environment...
call ".venv\Scripts\activate.bat"
echo.
echo Opening Facebook login in a visible browser window...
echo   --> Log in manually, then close the browser when done.
echo.
python -m agents.scraper --login
echo.
echo ================================================
echo  Login session saved. The bot will reuse it.
echo ================================================
pause
