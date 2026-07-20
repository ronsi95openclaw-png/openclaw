' HaulYeah 24/7 — launch start_haulyeah.bat with no visible console window.
' Launched by the HaulYeahBot scheduled task via wscript.exe (GUI subsystem,
' so no conhost flash). Bat runs its own restart loop on crash.
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\Users\ronsi95openclaw\Claude-openclaw\trash_hauling_bot\start_haulyeah.bat""", 0, False
