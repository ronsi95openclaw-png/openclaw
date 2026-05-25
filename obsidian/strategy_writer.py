"""Write strategy weight evolution events to 06_Strategies/ in the Obsidian Vault.

Each strategy gets a persistent markdown note that tracks its full
weight history, win rate trend, and governance actions.

A shared weight_history.jsonl index powers the dashboard chart.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from obsidian import vault_path

_FOLDER  = "06_Strategies"
_HISTORY = "weight_history.jsonl"


def write_strategy_evolution(
    strategy: str,
    old_weight: float,
    new_weight: float,
    reason: str,
    trades: int = 0,
    win_rate: float = 0.0,
) -> None:
    """Append a weight change event to the strategy's vault note.

    The note is appended (not overwritten) so the full evolution is preserved.
    The shared _HISTORY index is also updated for cross-strategy queries.

    Args:
        strategy:   strategy name (e.g. "EMA_CROSS")
        old_weight: weight before the change
        new_weight: weight after the change
        reason:     human-readable reason ("win recorded", "loss recorded", etc.)
        trades:     total trade count for this strategy
        win_rate:   current win rate (0.0–100.0)
    """
    folder = vault_path(_FOLDER)
    ts     = datetime.now(timezone.utc).isoformat()
    date   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    delta  = new_weight - old_weight
    arrow  = "⬆️" if delta > 0.001 else ("⬇️" if delta < -0.001 else "➡️")
    sign   = "+" if delta >= 0 else ""

    # ── Ensure strategy note exists ────────────────────────────────────────
    filepath = folder / f"{strategy}.md"
    if not filepath.exists():
        header = f"""---
strategy: {strategy}
tags: [strategy, weights]
---

# Strategy: {strategy}

## Weight History

| Timestamp | Old | New | Δ | Reason | Trades | WR |
|-----------|-----|-----|---|--------|--------|-----|
"""
        filepath.write_text(header, encoding="utf-8")

    # ── Append new row ────────────────────────────────────────────────────
    row = (
        f"| {ts[:16]} | {old_weight:.3f}× | **{new_weight:.3f}×** | "
        f"{arrow} {sign}{delta:+.3f} | {reason} | {trades} | {win_rate:.1f}% |\n"
    )
    with filepath.open("a", encoding="utf-8") as f:
        f.write(row)

    # ── Update shared index ────────────────────────────────────────────────
    idx = folder / _HISTORY
    entry = {
        "ts":         ts,
        "strategy":   strategy,
        "old_weight": round(old_weight, 4),
        "new_weight": round(new_weight, 4),
        "delta":      round(delta, 4),
        "reason":     reason,
        "trades":     trades,
        "win_rate":   round(win_rate, 2),
        "date":       date,
    }
    with idx.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
