@echo off
REM Isolation wrapper for vibe-trading-ai (HaulYeah/CryptoBot strangler-fig rule).
REM Forces the tool's HOME (~/.vibe-trading runtime root: .env, run history,
REM memory DB, alpha zoo) to live INSIDE backtest-lab so nothing is written
REM outside this directory. All invocations of the CLI should go through here.
setlocal
set "USERPROFILE=C:\Users\ronsi95openclaw\Claude-openclaw\backtest-lab"
set "HOME=C:\Users\ronsi95openclaw\Claude-openclaw\backtest-lab"
set "HOMEDRIVE=C:"
set "HOMEPATH=\Users\ronsi95openclaw\Claude-openclaw\backtest-lab"
"C:\Users\ronsi95openclaw\Claude-openclaw\backtest-lab\.venv-backtest\Scripts\vibe-trading.exe" %*
endlocal
