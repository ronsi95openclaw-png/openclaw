' ClawBot watchdog — run the batch with no visible console window.
' Launched by the ClawBot-Watchdog scheduled task via wscript.exe (GUI subsystem,
' so no conhost flash). The batch runs hidden (window mode 0).
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c ""C:\Users\ronsi95openclaw\Claude-openclaw\infra\run_watchdog.bat""", 0, False
