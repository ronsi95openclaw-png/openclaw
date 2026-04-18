"""
OpenClaw Launcher — starts dashboard + bot as truly independent Windows processes.
Run once: python start_openclaw.py
- Kills any stale process on port 8080 before starting
- Prevents duplicate bot instances
- Logs to data/logs/
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
    CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def kill_port(port: int) -> None:
    """Kill any process listening on the given port (Windows)."""
    if not port_in_use(port):
        return
    try:
        result = subprocess.run(
            f'netstat -ano | findstr ":{port} "',
            shell=True, capture_output=True, text=True
        )
        pids = set()
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if parts and "LISTENING" in line:
                pids.add(parts[-1])
        for pid in pids:
            try:
                subprocess.run(
                    ["wmic", "process", "where", f"processid={pid}", "delete"],
                    capture_output=True
                )
                print(f"[OpenClaw] Killed stale process PID {pid} on port {port}")
            except Exception:
                pass
        time.sleep(1)
    except Exception as e:
        print(f"[OpenClaw] Warning: could not clear port {port}: {e}")


def kill_script(script_name: str) -> None:
    """Kill any python process running the given script."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "processid,commandline"],
            capture_output=True, text=True, errors="ignore"
        )
        for line in result.stdout.splitlines():
            if script_name in line:
                parts = line.strip().split()
                pid = parts[-1] if parts else None
                if pid and pid.isdigit():
                    subprocess.run(
                        ["wmic", "process", "where", f"processid={pid}", "delete"],
                        capture_output=True
                    )
                    print(f"[OpenClaw] Killed stale {script_name} process PID {pid}")
        time.sleep(1)
    except Exception:
        pass


def launch(name: str, script: str) -> None:
    log_path = ROOT / "data" / "logs" / f"{name.lower()}.log"
    proc = subprocess.Popen(
        [str(VENV_PYTHON), script],
        cwd=str(ROOT),
        env=ENV,
        creationflags=CREATION_FLAGS,
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
    )
    print(f"[OpenClaw] {name} started — PID {proc.pid}")


if __name__ == "__main__":
    (ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)

    print("[OpenClaw] Clearing stale processes...")
    kill_port(8080)
    kill_script("receiver.py")

    time.sleep(1)

    print("[OpenClaw] Starting services...")
    launch("Dashboard", "dashboard/app.py")
    time.sleep(3)
    launch("Bot", "content/receiver.py")

    # Verify dashboard came up
    time.sleep(5)
    if port_in_use(8080):
        print("[OpenClaw] OK Dashboard live at http://localhost:8080")
        print("[OpenClaw] OK Tailscale:  http://100.70.89.27:8080")
    else:
        print("[OpenClaw] FAIL Dashboard did not start -- check data/logs/dashboard.log")
