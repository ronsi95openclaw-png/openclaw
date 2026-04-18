"""
OpenClaw Launcher — starts dashboard + bot as truly independent Windows processes.
Run once: python start_openclaw.py
"""
import subprocess
import sys
import os
import time
import socket
from pathlib import Path

ROOT = Path(__file__).parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
ENV = {**os.environ, "PYTHONPATH": str(ROOT)}

CREATION_FLAGS = 0
if sys.platform == "win32":
    # CREATE_NEW_PROCESS_GROUP makes it independent from parent; no DETACHED_PROCESS
    # (cannot combine DETACHED_PROCESS with CREATE_NEW_CONSOLE)
    CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def launch(name: str, script: str, port: int | None = None) -> None:
    if port and port_in_use(port):
        print(f"[OpenClaw] {name} already running on port {port} — skipping")
        return
    proc = subprocess.Popen(
        [str(VENV_PYTHON), script],
        cwd=str(ROOT),
        env=ENV,
        creationflags=CREATION_FLAGS,
        stdout=open(ROOT / "data" / "logs" / f"{name.lower()}.log", "a"),
        stderr=subprocess.STDOUT,
    )
    print(f"[OpenClaw] {name} started — PID {proc.pid}")


if __name__ == "__main__":
    (ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)
    launch("Dashboard", "dashboard/app.py", port=8080)
    time.sleep(2)
    launch("Bot", "content/receiver.py")
    print("[OpenClaw] Done. Dashboard: http://localhost:8080 | Tailscale: http://100.70.89.27:8080")
