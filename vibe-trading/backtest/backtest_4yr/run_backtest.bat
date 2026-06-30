@echo off
setlocal enabledelayedexpansion
title TJR 4-Year Backtest

echo ============================================================
echo   TJR 4-Year Backtest Pipeline
echo   %DATE% %TIME%
echo ============================================================
echo.

cd /d "%~dp0"

REM ── Find Python ──────────────────────────────────────────────
set PYTHON=

REM Option 1: backtest-lab venv (already has yfinance, pandas, numpy)
set VENV_BL=C:\Users\ronsi95openclaw\Claude-openclaw\backtest-lab\.venv-backtest\Scripts\python.exe
if exist "%VENV_BL%" (
    echo [OK] Using venv: backtest-lab\.venv-backtest
    set PYTHON=%VENV_BL%
    goto :check_packages
)

REM Option 2: relative path 3 levels up (backtest_4yr -> backtest -> vibe-trading -> Claude-openclaw)
set VENV_REL=%~dp0..\..\..\backtest-lab\.venv-backtest\Scripts\python.exe
if exist "%VENV_REL%" (
    echo [OK] Using venv (relative): .venv-backtest
    set PYTHON=%VENV_REL%
    goto :check_packages
)

REM Option 3: System Python 3.13 (known location)
set PY313=C:\Users\ronsi95openclaw\AppData\Local\Programs\Python\Python313\python.exe
if exist "%PY313%" (
    echo [OK] Using system Python 3.13
    set PYTHON=%PY313%
    goto :check_packages
)

REM Option 4: Windows Python Launcher
where py >nul 2>&1
if %errorlevel% == 0 (
    set PYTHON=py
    echo [OK] Using Windows Python Launcher (py)
    goto :check_packages
)

REM Option 5: python/python3 in PATH
where python >nul 2>&1
if %errorlevel% == 0 (
    set PYTHON=python
    echo [OK] Using system python
    goto :check_packages
)

echo [ERROR] Python not found. Install Python 3.10+ or ensure .venv-backtest exists.
pause
exit /b 1

:check_packages
echo.
echo [STEP 1] Checking / installing Python packages...
%PYTHON% -c "import yfinance" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing yfinance + dependencies...
    %PYTHON% -m pip install yfinance pandas numpy tqdm --quiet --trusted-host pypi.org --trusted-host files.pythonhosted.org
    if %errorlevel% neq 0 (
        echo   [WARN] pip install had issues. Trying without --trusted-host...
        %PYTHON% -m pip install yfinance pandas numpy tqdm --quiet
    )
) else (
    echo   yfinance already installed.
)

%PYTHON% -c "import pandas" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] pandas still not available. Check pip and network.
    pause
    exit /b 1
)

echo.
echo [STEP 2] Running smoke test...
%PYTHON% smoke_test.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Smoke test failed. Fix issues before running full backtest.
    pause
    exit /b 1
)

echo.
echo [STEP 3] Running full 4-year backtest pipeline...
echo   (This will download ~4yr of ES + NQ data, then run 4 backtests)
echo   Expected time: 2-10 minutes depending on network speed.
echo.

%PYTHON% run_all.py

set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE% == 0 (
    echo ============================================================
    echo   DONE - Results saved to results\ folder
    echo ============================================================
) else (
    echo ============================================================
    echo   PIPELINE COMPLETED WITH ERRORS (exit code %EXIT_CODE%)
    echo ============================================================
)

echo.
echo Press any key to exit...
pause >nul
exit /b %EXIT_CODE%
