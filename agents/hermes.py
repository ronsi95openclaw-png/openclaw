"""Hermes — daily knowledge-graph agent for OpenClaw.

Runs graphify on the codebase, writes a snapshot to memory/HERMES_GRAPH_REPORT.md
(picked up by sync_to_vault.bat → Obsidian), and sends a Telegram digest.

Two run modes:
  update (default/daily) — `graphify update .`  — code re-extraction only, no LLM.
  full                   — `graphify . --backend claude` — full extraction incl. docs.

Usage:
    Scheduled daily via core/scheduler.py (HERMES_ENABLED=true in .env).
    Triggered on-demand via /hermes now in Telegram.
    Direct:  python -m agents.hermes          # update mode
             python -m agents.hermes --full   # full mode (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

_PROJECT_ROOT = Path(__file__).parent.parent
_OUT_DIR      = _PROJECT_ROOT / "graphify-out"
_REPORT_FILE  = _OUT_DIR / "GRAPH_REPORT.md"
_MEMORY_FILE  = _PROJECT_ROOT / "memory" / "HERMES_GRAPH_REPORT.md"

HERMES_JOB = "clawbot_hermes_daily"


# ---------------------------------------------------------------------------
# graphify runner
# ---------------------------------------------------------------------------

def graphify_available() -> bool:
    try:
        r = subprocess.run(
            ["graphify", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _subprocess_env() -> dict:
    """Build env for graphify subprocess — inherits current env + .env values."""
    env = os.environ.copy()
    dotenv_path = _PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                if key and key not in env:  # don't override already-set vars
                    env[key] = val.strip().strip('"').strip("'")
    return env


def run_graphify(full: bool = False) -> tuple[bool, str]:
    """Invoke graphify CLI on the project root. Returns (success, log_output).

    Args:
        full: If True, run full extraction (docs + code, needs ANTHROPIC_API_KEY).
              If False (default/daily), run code-only update — no LLM required.
    """
    env = _subprocess_env()

    if full:
        # Full extraction: docs + code — requires LLM backend
        api_key = env.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return False, "Full mode needs ANTHROPIC_API_KEY in .env"
        cmd = ["graphify", str(_PROJECT_ROOT), "--backend", "claude", "--wiki"]
    else:
        # Daily update: code re-extraction only — no LLM needed
        cmd = ["graphify", "update", str(_PROJECT_ROOT)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=_PROJECT_ROOT,
            env=env,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "graphify timed out (> 5 min)"
    except FileNotFoundError:
        return False, "graphify not found — pip install graphifyy"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------

def _read_report() -> Optional[str]:
    if _REPORT_FILE.exists():
        return _REPORT_FILE.read_text(encoding="utf-8")
    return None


def _extract_digest(report: str, max_chars: int = 3000) -> str:
    """Pull the most informative lines from GRAPH_REPORT.md for a Telegram message."""
    lines, kept, total = report.splitlines(), [], 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("- ") or stripped.startswith("* "):
            kept.append(stripped)
            total += len(stripped) + 1
            if total >= max_chars:
                break
    return "\n".join(kept) if kept else report[:max_chars]


def _write_memory_snapshot(report: str, ts: str) -> None:
    """Persist the graph report to memory/ so sync_to_vault.bat can pick it up."""
    _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    date = ts[:10]
    header = (
        f"---\n"
        f"title: Hermes Graph Report {date}\n"
        f"created: {date}\n"
        f"type: log\n"
        f"tags:\n  - openclaw\n  - openclaw/knowledge-graph\n"
        f"status: active\n"
        f"---\n\n"
        f"# Hermes Knowledge Graph — {ts}\n\n"
    )
    _MEMORY_FILE.write_text(header + report, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_hermes(
    send_fn: Optional[Callable[[int, str], Awaitable[None]]] = None,
    chat_id: Optional[int] = None,
) -> None:
    """Orchestrate the full Hermes knowledge-graph cycle."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    async def _send(text: str) -> None:
        if send_fn and chat_id:
            await send_fn(chat_id, text)

    if not graphify_available():
        await _send(
            "🧠 <b>Hermes</b>\n"
            "⚠️ graphify not installed.\n"
            "Run: <code>pip install graphifyy && graphify install</code>"
        )
        return

    await _send("🧠 <b>Hermes</b> — scanning codebase, building knowledge graph…")

    # Code-only update by default (no LLM/API cost).
    # Pass full=True to run_hermes() for a first-time full extraction with docs.
    success, log = run_graphify(full=False)

    if not success:
        snippet = log[:400] if log else "unknown error"
        await _send(
            f"🧠 <b>Hermes</b>\n"
            f"❌ graphify failed:\n<code>{snippet}</code>"
        )
        return

    report = _read_report()
    if not report:
        await _send(
            "🧠 <b>Hermes</b>\n"
            "⚠️ Graph built but GRAPH_REPORT.md not found in graphify-out/."
        )
        return

    _write_memory_snapshot(report, ts)

    digest = _extract_digest(report)
    await _send(
        f"🧠 <b>Hermes Knowledge Graph — {ts}</b>\n\n"
        f"<pre>{digest}</pre>\n\n"
        f"📁 Full report: <code>graphify-out/GRAPH_REPORT.md</code>\n"
        f"🗂 Obsidian: <code>graphify-out/obsidian/</code>\n"
        f"📝 Memory: <code>memory/HERMES_GRAPH_REPORT.md</code>"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def _cli() -> None:
        if not graphify_available():
            print("graphify not found. Install with: pip install graphifyy")
            sys.exit(1)
        use_full = "--full" in sys.argv
        mode_label = "full (docs + code, needs ANTHROPIC_API_KEY)" if use_full else "update (code only)"
        print(f"Running graphify [{mode_label}]…")
        ok, log = run_graphify(full=use_full)
        print(log or "(no output)")
        if not ok:
            sys.exit(1)
        report = _read_report()
        if report:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            _write_memory_snapshot(report, ts)
            print(f"\n✅ Hermes complete. Report: {_REPORT_FILE}")
            print(f"   Memory snapshot: {_MEMORY_FILE}")
        else:
            print("⚠️  GRAPH_REPORT.md not found after run.")

    asyncio.run(_cli())
