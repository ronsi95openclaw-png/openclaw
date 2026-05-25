"""Write individual trade outcomes to 05_Trading/ in the Obsidian Vault.

Each closed trade gets a markdown note with full context:
outcome, strategy, regime, PnL, compressed lesson, and backlinks.

An index JSONL (05_Trading/_index.jsonl) is maintained for fast
retrieval by strategy/symbol/outcome without scanning all files.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from obsidian import vault_path

_FOLDER = "05_Trading"
_INDEX  = "_index.jsonl"


def _safe(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", str(s))


def write_trade(record: dict[str, Any]) -> None:
    """Write a closed trade outcome to the vault.

    Args:
        record: trade outcome dict as written to data/logs/trade_outcomes.jsonl
                Expected keys: symbol, strategy, side, entry_price, exit_price,
                pnl, outcome, regime_label, lesson (from qwen_compressor), ts
    """
    folder = vault_path(_FOLDER)

    ts_raw  = record.get("closed_at") or record.get("ts") or datetime.now(timezone.utc).isoformat()
    ts_dt   = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if isinstance(ts_raw, str) else datetime.now(timezone.utc)
    date_s  = ts_dt.strftime("%Y-%m-%d")
    time_s  = ts_dt.strftime("%H:%M")

    symbol   = record.get("symbol",   "UNKNOWN").replace("_USDT", "")
    strategy = record.get("strategy", "UNKNOWN")
    side     = (record.get("side") or record.get("action") or "").upper()
    outcome  = record.get("outcome",  "UNKNOWN")
    pnl      = float(record.get("pnl", 0))
    regime   = record.get("regime_label", "UNKNOWN")
    entry    = float(record.get("entry_price", 0))
    exit_p   = float(record.get("exit_price", 0) or 0)
    lesson   = record.get("lesson", "")
    trade_id = record.get("id", "")[:8] if record.get("id") else "noid"

    icon  = "✅" if outcome == "win" else "❌"
    sign  = "+" if pnl >= 0 else ""

    # ── Filename: YYYY-MM-DD_SYMBOL_STRATEGY_id ────────────────────────────
    filename = f"{date_s}_{_safe(symbol)}_{_safe(strategy)}_{trade_id}.md"
    filepath = folder / filename

    md = f"""---
date: {date_s}
time: {time_s}
symbol: {symbol}
strategy: {strategy}
side: {side}
outcome: {outcome}
pnl: {pnl:.4f}
regime: {regime}
tags: [trading, {symbol.lower()}, {strategy.lower()}, {outcome}]
---

# {icon} {symbol} {side} [{strategy}] — {sign}${pnl:,.2f}

**Date:** {date_s} {time_s} UTC
**Regime:** {regime}
**Entry:** ${entry:,.2f} | **Exit:** ${exit_p:,.2f}
**Outcome:** {outcome.upper()} | **PnL:** {sign}${pnl:,.4f}

## Lesson (Qwen)
{lesson if lesson else "_No lesson generated_"}

## Context
```json
{json.dumps({k: v for k, v in record.items() if k not in ("lesson",)}, indent=2, default=str)[:800]}
```

## Backlinks
- [[05_Trading/{date_s}_daily]]
- [[06_Strategies/{_safe(strategy)}]]
"""

    filepath.write_text(md, encoding="utf-8")
    _append_index(folder, {
        "file":     filename,
        "date":     date_s,
        "symbol":   symbol,
        "strategy": strategy,
        "outcome":  outcome,
        "pnl":      pnl,
        "regime":   regime,
        "ts":       ts_raw,
    })


def _append_index(folder: Path, entry: dict) -> None:
    idx = folder / _INDEX
    with idx.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
