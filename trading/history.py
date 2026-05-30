"""
Trade history — a small structured JSON store of executed trades, plus
summary/report helpers for the /report Telegram command.

The bot places market orders but does not track exits, so realized P&L / win
rate are not computable here — this is a trade-ACTIVITY record (counts, volume,
recent trades), not a performance ledger.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_DEFAULT_PATH = Path(__file__).parent.parent / "data" / "trades.json"


def _resolve(path: Optional[str | Path]) -> Path:
    return Path(path) if path else _DEFAULT_PATH


def load_trades(path: Optional[str | Path] = None) -> List[dict]:
    """Load the trade list; returns [] if missing or corrupt."""
    p = _resolve(path)
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def record_trade(entry: dict, path: Optional[str | Path] = None) -> None:
    """Append an executed-trade entry to the store, stamping recorded_at."""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    trades = load_trades(p)
    entry = dict(entry)
    entry.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    trades.append(entry)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)


def summarize(trades: List[dict]) -> dict:
    """Aggregate counts/volume from a list of trade entries. Pure."""
    by_action: dict = {}
    by_coin: dict = {}
    total_usd = 0.0
    for t in trades:
        action = t.get("action", "?")
        coin = t.get("coin", "?")
        by_action[action] = by_action.get(action, 0) + 1
        by_coin[coin] = by_coin.get(coin, 0) + 1
        total_usd += float(t.get("usd_amount", 0) or 0)
    return {
        "total": len(trades),
        "by_action": by_action,
        "by_coin": by_coin,
        "total_usd": round(total_usd, 2),
        "recent": trades[-5:],
    }


def format_report(summary: dict) -> str:
    """Render a summary as a Telegram (HTML) message."""
    if summary["total"] == 0:
        return "📊 <b>Trade Report</b>\n\nNo executed trades recorded yet."

    lines = [
        "📊 <b>Trade Report</b> — executed trades",
        "",
        f"Total trades: <b>{summary['total']}</b>",
        f"Volume traded: <b>${summary['total_usd']:.2f}</b>",
    ]
    if summary["by_action"]:
        lines.append("By action: " + ", ".join(f"{k} {v}" for k, v in summary["by_action"].items()))
    if summary["by_coin"]:
        lines.append("By coin: " + ", ".join(f"{k} {v}" for k, v in summary["by_coin"].items()))
    lines += ["", "<i>Activity only — realized P&L requires exit tracking.</i>"]
    return "\n".join(lines)
