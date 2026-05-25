"""Write daily session notes to 20_Daily_Notes/ in the Obsidian Vault.

Each day gets a structured note with P&L summary, trade count,
win rate, and links to individual trade entries.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from obsidian import vault_path

_FOLDER = "20_Daily_Notes"


def write_daily_note(
    date: str,
    total_pnl: float,
    trades_today: int,
    wins: int,
    losses: int,
    notes: str = "",
) -> None:
    """Write or overwrite the daily note for the given date.

    Args:
        date:         YYYY-MM-DD
        total_pnl:    net P&L for the day
        trades_today: total closed trades
        wins:         number of winning trades
        losses:       number of losing trades
        notes:        free-form annotation (e.g. 'catch_up', session notes)
    """
    folder  = vault_path(_FOLDER)
    wr      = (wins / trades_today * 100) if trades_today else 0.0
    sign    = "+" if total_pnl >= 0 else ""
    icon    = "🟢" if total_pnl >= 0 else "🔴"
    written = datetime.now(timezone.utc).strftime("%H:%M UTC")

    md = f"""---
date: {date}
pnl: {total_pnl:.4f}
trades: {trades_today}
wins: {wins}
losses: {losses}
win_rate: {wr:.1f}
tags: [daily, trading]
---

# {icon} Daily Note — {date}

| Metric | Value |
|--------|-------|
| Net P&L | {sign}${total_pnl:,.4f} |
| Trades | {trades_today} |
| Wins / Losses | {wins}W / {losses}L |
| Win Rate | {wr:.1f}% |

## Notes
{notes if notes else "_No notes_"}

## Trade Journal Entries
> See [[05_Trading/]] for individual trade notes from {date}

## Strategy Performance
> See [[07_Optimization/{date}_performance]] for weight snapshot

## Claude Analysis
> See [[07_Optimization/]] for tonight's Opus analysis

---
_Written by OpenClaw at {written}_
"""

    filepath = folder / f"{date}.md"
    filepath.write_text(md, encoding="utf-8")
