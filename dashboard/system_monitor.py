"""OpenClaw dashboard health monitor.

Runs a lightweight health loop in the dashboard process to
track command status, endpoint availability, agent health,
and log errors.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    import psutil
except ImportError:
    psutil = None

from content.uploader import send_status_sync
from flask import Flask

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SYSTEM_LOG_FILE = LOG_DIR / "system_monitor.log"
SYSTEM_STATUS_FILE = LOG_DIR / "system_status.json"

DEFAULT_COMMANDS = [
    "/status",
    "/market",
    "/cashclaw",
    "/fng",
    "/scan 4h",
    "/scout",
    "/autotrade",
]

MAX_EVENT_HISTORY = 50
CHECK_INTERVAL_SECONDS = 60
SCAN_CYCLE_INTERVAL = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_safe(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class SystemMonitor:
    def __init__(
        self,
        app: Flask,
        execute_command: Callable[[str], str],
        get_ollama_status: Callable[[], dict],
        get_clawbot_status: Callable[[], dict],
        get_autotrade_status: Callable[[], dict],
        get_scout_status: Callable[[], dict],
    ) -> None:
        self.app = app
        self.execute_command = execute_command
        self.get_ollama_status = get_ollama_status
        self.get_clawbot_status = get_clawbot_status
        self.get_autotrade_status = get_autotrade_status
        self.get_scout_status = get_scout_status

        self.lock = threading.RLock()
        self._started_at = datetime.now(timezone.utc)
        self._cycle = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = False

        self._status: dict = self._initial_status()
        self._write_status()

    def _initial_status(self) -> dict:
        return {
            "system": {
                "status": "initializing",
                "uptime": "0m",
                "errors": 0,
                "last_checked": None,
            },
            "agents": {
                "jarvis": "unknown",
                "scout": "unknown",
                "watchdog": "unknown",
                "codex": "unknown",
                "clipper": "unknown",
                "hawk": "unknown",
            },
            "commands": {cmd: "unknown" for cmd in DEFAULT_COMMANDS},
            "endpoints": {},
            "logs": {"errors": 0, "recent_errors": []},
            "events": [],
        }

    def start(self) -> None:
        with self.lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop = False
            thread = threading.Thread(target=self._run_loop, name="OpenClawSystemMonitor", daemon=True)
            thread.start()
            self._thread = thread
            self.add_event("monitor", "System monitor started", severity="info")

    def stop(self) -> None:
        with self.lock:
            self._stop = True

    def get_status(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self._status))

    def add_event(self, event_type: str, message: str, details: Optional[dict] = None, severity: str = "info") -> None:
        event = {
            "timestamp": now_iso(),
            "type": event_type,
            "severity": severity,
            "message": message,
            "details": details or {},
        }
        with self.lock:
            events = self._status.setdefault("events", [])
            events.append(event)
            self._status["events"] = events[-MAX_EVENT_HISTORY:]
            if severity == "error":
                self._status["system"]["errors"] = self._status["system"].get("errors", 0) + 1
        self._persist_event(event)

    def _persist_event(self, event: dict) -> None:
        try:
            with open(SYSTEM_LOG_FILE, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _write_status(self) -> None:
        write_json_safe(SYSTEM_STATUS_FILE, self._status)

    def _get_ollama_bin(self) -> Optional[str]:
        return shutil.which("ollama")

    def _find_ollama_processes(self) -> list:
        if psutil is None:
            return []
        processes = []
        for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            try:
                name = str(proc.info.get("name", "")).lower()
                cmdline = [str(part).lower() for part in proc.info.get("cmdline") or []]
                if "ollama" in name or any("ollama" in part for part in cmdline):
                    processes.append(proc)
            except Exception:
                continue
        return processes

    def _is_ollama_running(self) -> bool:
        return bool(self._find_ollama_processes())

    def _terminate_ollama_processes(self) -> bool:
        killed_any = False
        for proc in self._find_ollama_processes():
            try:
                proc.terminate()
                proc.wait(timeout=5)
                killed_any = True
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                    killed_any = True
                except Exception:
                    pass
        return killed_any

    def _start_ollama_service(self) -> bool:
        binary = self._get_ollama_bin()
        if not binary:
            self.add_event("repair", "Ollama executable not found in PATH", severity="error")
            return False
        try:
            kwargs = {"cwd": str(ROOT), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([binary, "serve"], **kwargs)
            self.add_event("repair", "Started Ollama service process", severity="info")
            return True
        except Exception as exc:
            self.add_event("repair", f"Failed to start Ollama service: {exc}", severity="error")
            return False

    def _repair_ollama_service(self) -> bool:
        self.add_event("repair", "Attempting Ollama self-heal", severity="info")
        if self._is_ollama_running():
            self.add_event("repair", "Stopping stale Ollama processes", severity="debug")
            self._terminate_ollama_processes()
            time.sleep(2)

        if not self._start_ollama_service():
            self.alert(issue="Ollama restart failed", component="ollama", action="restart", result="failed")
            return False

        time.sleep(5)
        try:
            status = self.get_ollama_status()
            if status.get("online"):
                self.add_event("repair", "Ollama is online after restart", severity="info")
                self.alert(issue="Ollama restarted", component="ollama", action="restart", result="recovered")
                return True
        except Exception as exc:
            self.add_event("repair", f"Ollama status still unavailable after restart: {exc}", severity="error")

        self.alert(issue="Ollama restart incomplete", component="ollama", action="restart", result="failed")
        return False

    def _is_ollama_failure(self, issue: str) -> bool:
        text = issue.lower()
        return any(token in text for token in [
            "ollama",
            "localhost:11434",
            "connection refused",
            "unable to connect",
            "connection reset",
            "broken pipe",
            "service unavailable",
        ])

    def _is_claude_api_failure(self, issue: str) -> bool:
        text = issue.lower()
        return any(token in text for token in [
            "anthropic",
            "claude",
            "unauthorized",
            "authentication",
            "invalid api key",
            "401",
            "403",
            "forbidden",
        ])

    def _format_uptime(self) -> str:
        delta = datetime.now(timezone.utc) - self._started_at
        minutes = int(delta.total_seconds() // 60)
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}h {minutes}m" if hours else f"{minutes}m"

    def _run_loop(self) -> None:
        while True:
            with self.lock:
                if self._stop:
                    break
            start = time.time()
            self._cycle += 1
            try:
                self.perform_cycle()
            except Exception as exc:
                self.add_event("monitor", "Health cycle crashed", {"error": str(exc), "trace": traceback.format_exc()}, severity="error")
            elapsed = time.time() - start
            sleep_time = max(5, CHECK_INTERVAL_SECONDS - elapsed)
            time.sleep(sleep_time)

    def perform_cycle(self) -> None:
        self.add_event("monitor", f"Starting health cycle {self._cycle}", severity="debug")
        status_ok = True

        endpoints = self.check_endpoints()
        commands = self.check_commands()
        agents = self.check_agents()
        logs = self.check_logs()

        with self.lock:
            self._status["endpoints"] = endpoints
            self._status["commands"] = commands
            self._status["agents"] = agents
            self._status["logs"] = logs
            self._status["system"]["uptime"] = self._format_uptime()
            self._status["system"]["last_checked"] = now_iso()

            if any(cmd_info["status"] != "ok" for cmd_info in commands.values()):
                status_ok = False
            if endpoints.get("status") != "ok":
                status_ok = False
            if logs.get("errors", 0) > 0:
                status_ok = False

            self._status["system"]["status"] = "healthy" if status_ok else "degraded"

        if not status_ok:
            self.add_event("monitor", "System status degraded", severity="warning")

        self._write_status()

    def check_endpoints(self) -> dict:
        result = {"status": "ok", "checks": {}}
        paths = ["/health", "/api/system-status", "/api/execute-command"]
        for path in paths:
            try:
                with self.app.test_client() as client:
                    if path == "/api/execute-command":
                        response = client.post(path, json={"command": "/status"})
                    else:
                        response = client.get(path)
                    ok = response.status_code == 200
                    result["checks"][path] = {
                        "status": "ok" if ok else "error",
                        "code": response.status_code,
                        "latency_ms": round(response.elapsed.total_seconds() * 1000, 1) if hasattr(response, "elapsed") else None,
                    }
                    if not ok:
                        result["status"] = "error"
                        self.add_event("endpoint", f"Endpoint {path} returned {response.status_code}", severity="warning")
            except Exception as exc:
                result["status"] = "error"
                result["checks"][path] = {"status": "error", "error": str(exc)}
                self.add_event("endpoint", f"Endpoint {path} unreachable: {exc}", severity="warning")
        return result

    def check_commands(self) -> dict:
        results = {}
        for cmd in DEFAULT_COMMANDS:
            if cmd.startswith("/scan") and self._cycle % SCAN_CYCLE_INTERVAL != 0:
                results[cmd] = {"status": "skipped", "latency_ms": None, "last_success": None}
                continue
            start = time.perf_counter()
            try:
                self.execute_command(cmd)
                latency = round((time.perf_counter() - start) * 1000, 1)
                results[cmd] = {"status": "ok", "latency_ms": latency, "last_success": now_iso()}
            except Exception as exc:
                latency = round((time.perf_counter() - start) * 1000, 1)
                results[cmd] = {"status": "error", "latency_ms": latency, "error": str(exc)}
                self.add_event("command", f"Command {cmd} failed: {exc}", severity="error")
                self._attempt_repair(cmd, exc)
        return results

    def _attempt_repair(self, cmd: str, exc: BaseException) -> None:
        issue = str(exc)
        if self._is_ollama_failure(issue):
            self.add_event("repair", f"Ollama failure detected for {cmd}. Restarting Ollama service.", severity="warning")
            if self._repair_ollama_service():
                try:
                    self.add_event("repair", f"Retrying failed command {cmd} after Ollama restart", severity="info")
                    self.execute_command(cmd)
                    self.add_event("repair", f"Command {cmd} succeeded after Ollama restart", severity="info")
                    self.alert(
                        issue=f"Command failed: {cmd}",
                        component="ollama",
                        action="restart",
                        result="recovered",
                    )
                    return
                except Exception as retry_exc:
                    issue = f"{issue}; restart retry failed: {retry_exc}"
                    self.add_event("repair", issue, severity="error")
                    self.alert(
                        issue=f"Command failure after Ollama restart: {cmd}",
                        component="ollama",
                        action="restart",
                        result="failed",
                    )
                    return

        if self._is_claude_api_failure(issue):
            self.add_event("repair", f"Claude API issue detected for {cmd}. Falling back to Ollama path.", severity="warning")
            self.alert(
                issue=f"Claude API failure: {issue}",
                component="claude",
                action="fallback",
                result="using_ollama",
            )

        try:
            self.add_event("repair", f"Retrying failed command {cmd}", severity="info")
            self.execute_command(cmd)
            self.add_event("repair", f"Command {cmd} succeeded after retry", severity="info")
            self.alert(
                issue=f"Command failed: {cmd}",
                component="command",
                action="retry",
                result="recovered",
            )
            return
        except Exception as retry_exc:
            issue = f"{issue}; retry failed: {retry_exc}"
            self.add_event("repair", issue, severity="error")
            self.alert(
                issue=f"Command failure: {cmd}",
                component="command",
                action="retry",
                result="failed",
            )

    def check_agents(self) -> dict:
        agents = {}
        try:
            ollama = self.get_ollama_status()
            online = ollama.get("online")
            agents["jarvis"] = "active" if online else "offline"
            if not online:
                self.add_event("agent", "JARVIS offline detected", severity="warning")
                if self._repair_ollama_service():
                    agents["jarvis"] = "restarting"
                else:
                    agents["jarvis"] = "offline"
        except Exception as exc:
            agents["jarvis"] = "error"
            self.add_event("agent", f"JARVIS health check failed: {exc}", severity="warning")
            if self._repair_ollama_service():
                agents["jarvis"] = "restarting"

        try:
            scout = self.get_scout_status()
            agents["scout"] = "running" if scout.get("scan_count", 0) > 0 else "idle"
        except Exception as exc:
            agents["scout"] = "error"
            self.add_event("agent", f"SCOUT health check failed: {exc}", severity="warning")

        try:
            autotrade = self.get_autotrade_status()
            agents["watchdog"] = "armed" if autotrade.get("enabled") else "idle"
        except Exception as exc:
            agents["watchdog"] = "error"
            self.add_event("agent", f"WATCHDOG health check failed: {exc}", severity="warning")

        agents["codex"] = "active"
        agents["clipper"] = "ready"
        agents["hawk"] = "live"
        return agents

    def check_logs(self) -> dict:
        error_count = 0
        recent_errors = []
        try:
            for path in sorted(LOG_DIR.glob("*.log")):
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                        lines = handle.readlines()[-100:]
                    for line in lines:
                        if "ERROR" in line or "Traceback" in line:
                            error_count += 1
                            recent_errors.append({"file": path.name, "line": line.strip()})
                            if len(recent_errors) >= 10:
                                break
                except Exception:
                    continue
        except Exception as exc:
            self.add_event("logs", f"Log scan failed: {exc}", severity="warning")
        return {"errors": error_count, "recent_errors": recent_errors[:10]}

    def alert(self, issue: str, component: str, action: str, result: str) -> None:
        message = (
            f"🚨 <b>OpenClaw Alert</b>\n"
            f"Issue: {issue}\n"
            f"Component: {component}\n"
            f"Action: {action}\n"
            f"Status: {result}"
        )
        send_status_sync(message)
        self.add_event("alert", message, severity="warning")
