"""Regenerate .ai/docs/SESSION_COMPACT.md from live state files.

Called by ~/.claude/stop-hook-git-check.sh on session end, or manually:
    python scripts/generate_session_compact.py
"""
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parents[1]
OUT = ROOT / ".ai/docs/SESSION_COMPACT.md"


def _load(path, default=None):
    try:
        return json.loads((ROOT / path).read_text())
    except Exception:
        return default if default is not None else {}


def _git(cmd):
    try:
        return subprocess.check_output(cmd, cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "(unavailable)"


def main():
    bot = _load("data/cryptocom_state.json")
    capital = _load("data/capital_state.json")
    weights = _load("data/strategy_weights.json")
    goal = _load("data/goal_tracker.json")

    trades = []
    try:
        lines = (ROOT / "data/logs/trade_outcomes.jsonl").read_text().strip().split("\n")
        trades = [json.loads(l) for l in lines if l.strip()]
    except Exception:
        pass

    wins = sum(1 for t in trades if t.get("outcome") == "win")
    wr = f"{wins / len(trades) * 100:.0f}%" if trades else "n/a"

    balance = bot.get("balance") or (bot.get("starting_balance", 98) + bot.get("total_pnl", 0))
    total_pnl = bot.get("total_pnl", 0)

    commits = _git(["git", "log", "--oneline", "-5"])
    branch = _git(["git", "branch", "--show-current"])

    milestones = goal.get("milestones", [])
    milestones_hit = sum(1 for m in milestones if m.get("hit"))
    next_milestone = next((m for m in milestones if not m.get("hit")), None)
    next_target = f"${next_milestone['target']:,.0f}" if next_milestone else "ALL HIT"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines_out = [
        "# SESSION_COMPACT — AUTO-GENERATED",
        f"**Last updated:** {now} (by scripts/generate_session_compact.py)",
        "**DO NOT edit manually — regenerated on session end.**",
        "",
        "## SYSTEM STATE",
        "| Key | Value |",
        "|-----|-------|",
        f"| Balance | ${balance:,.2f} |",
        f"| Total PnL | ${total_pnl:+,.2f} |",
        f"| Capital state | {capital.get('state', 'UNKNOWN')} |",
        f"| All-time peak | ${capital.get('alltime_peak', 0):,.2f} |",
        f"| Demo mode | {bot.get('demo_mode', True)} |",
        f"| Branch | `{branch}` |",
        f"| Trades (total) | {len(trades)} |",
        f"| Win rate | {wr} |",
        f"| Milestones hit | {milestones_hit} / {len(milestones)} |",
        f"| Next target | {next_target} |",
        "",
        "## RECENT COMMITS",
        "```",
        commits,
        "```",
        "",
        "## STRATEGY WEIGHTS",
        "| Strategy | Weight | Win Rate | Trades | Status |",
        "|----------|--------|----------|--------|--------|",
    ]

    for name, w in sorted((weights or {}).items()):
        if isinstance(w, dict):
            weight = w.get("weight", 1.0)
            wr_s = f"{w.get('win_rate', 0) * 100:.0f}%"
            n = w.get("trades", 0)
            status = "DISABLED" if w.get("disabled") else ("LOW" if weight < 0.3 else "OK")
            lines_out.append(f"| {name} | {weight:.2f}× | {wr_s} | {n} | {status} |")

    lines_out += [
        "",
        "## ACTIVE POSITIONS",
    ]

    open_pos = bot.get("open_positions", [])
    if isinstance(open_pos, dict):
        open_pos = list(open_pos.values())
    if open_pos:
        lines_out += ["| Symbol | Side | Entry | Size |", "|--------|------|-------|------|"]
        for pos in open_pos:
            sym = pos.get("symbol", "?")
            lines_out.append(
                f"| {sym} | {pos.get('side','?')} | ${pos.get('entry_price', 0):,.2f} | {pos.get('size', 0)} |"
            )
    else:
        lines_out.append("*(no open positions)*")

    lines_out += [
        "",
        "## KEY FILES QUICK REFERENCE",
        "| File | Purpose |",
        "|------|---------|",
        "| `trading/cryptocom_bot.py` | Main bot, 60s scan loop |",
        "| `trading/strategies.py` | 6 active strategies + weight engine |",
        "| `runtime/intent_pipeline.py` | 5-gate safety filter (never bypass) |",
        "| `risk/capital_preservation.py` | SAFE/DEFENSIVE/CRITICAL/HALT state machine |",
        "| `runtime/telegram_bot.py` | 14 Telegram commands |",
        "| `dashboard/api/server.py` | FastAPI + WebSocket (port 8000) |",
        "| `data/cryptocom_state.json` | Live bot state |",
        "| `data/logs/trade_outcomes.jsonl` | Closed trades (Claude Analyst input) |",
        "",
        "## HARD RULES",
        "- NEVER commit `.env`, `credentials.json`, `setup.sh`",
        "- NEVER set `DEMO_MODE=false` without explicit user instruction",
        "- NEVER bypass IntentPipeline gate",
        "- NEVER push to `main` branch",
        "- Always develop on branch: `claude/blofin-trading-bot-dashboard-TUJBC`",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines_out) + "\n")
    print(f"SESSION_COMPACT.md regenerated — {now}")
    print(f"  Balance: ${balance:,.2f}  |  Trades: {len(trades)}  |  WR: {wr}  |  Branch: {branch}")


if __name__ == "__main__":
    main()
