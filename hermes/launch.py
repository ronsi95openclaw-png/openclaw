#!/usr/bin/env python3
"""
hermes/launch.py - start the Hermes gateway (non-trading orchestrator) and write a
pidfile the dashboard adapter can detect.

- Refuses to start if hermes/KILL_SWITCH exists.
- Uses the Ollama provider already configured in Hermes' config.yaml (no keys here).
- Launches `hermes gateway run` detached; records its PID in hermes/hermes.pid.
- Won't double-launch if Hermes is already running (our pidfile OR an installed service).

Usage:  hermes/start.bat        (or:  python hermes/launch.py)
"""
from __future__ import annotations
import os
import sys
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent              # ...\Claude-openclaw\hermes
PIDFILE = HERE / "hermes.pid"
KILL_SWITCH = HERE / "KILL_SWITCH"
LOGDIR = HERE / "logs"
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
HERMES_FALLBACK = Path(_LOCALAPPDATA) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"


def _find_hermes():
    exe = shutil.which("hermes")
    if exe:
        return exe
    return str(HERMES_FALLBACK) if HERMES_FALLBACK.exists() else None


def _already_running():
    # (a) our pidfile, PID still alive
    try:
        if PIDFILE.exists():
            pid = int((PIDFILE.read_text(encoding="utf-8").strip() or "0"))
            if pid > 0:
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                     capture_output=True, text=True, timeout=5)
                if str(pid) in (out.stdout or ""):
                    return str(pid)
    except Exception:
        pass
    # (b) any hermes.exe (installed gateway service)
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq hermes.exe", "/NH"],
                             capture_output=True, text=True, timeout=5)
        if "hermes.exe" in (out.stdout or ""):
            return "service"
    except Exception:
        pass
    return None


def main() -> int:
    if KILL_SWITCH.exists():
        print(f"[launch] KILL_SWITCH present ({KILL_SWITCH}) - refusing to start Hermes.")
        return 2

    running = _already_running()
    if running:
        print(f"[launch] Hermes already running ({running}). Nothing to do.")
        return 0

    hermes = _find_hermes()
    if not hermes:
        print("[launch] hermes.exe not found - install Hermes / run setup first.")
        return 1

    LOGDIR.mkdir(parents=True, exist_ok=True)
    logf = open(LOGDIR / "gateway.out", "a", encoding="utf-8")

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    print(f"[launch] starting: {hermes} gateway run   (Ollama provider from config.yaml)")
    proc = subprocess.Popen(
        [hermes, "gateway", "run"],
        stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=creationflags, cwd=str(HERE.parent),
    )
    PIDFILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"[launch] Hermes gateway launched (pid {proc.pid}).")
    print(f"[launch] pidfile: {PIDFILE}")
    print(f"[launch] logs:    {LOGDIR / 'gateway.out'}")
    print("[launch] The dashboard's Hermes card flips to LIVE on its next poll.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
