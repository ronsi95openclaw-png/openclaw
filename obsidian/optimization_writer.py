"""Write strategy performance snapshots + Claude Opus analysis to 07_Optimization/.

Two note types:
  - YYYY-MM-DD_performance.md  — weight snapshot after each daily flush
  - analysis_<ts>.md           — Claude Opus analysis report (when available)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from obsidian import vault_path

_FOLDER = "07_Optimization"


def write_strategy_performance(weights: dict[str, Any]) -> None:
    """Write current strategy weights snapshot to vault.

    Args:
        weights: dict returned by StrategyWeightEngine.summary()
                 {strategy: {weight, win_rate, trades, wins, losses}}
    """
    folder  = vault_path(_FOLDER)
    date    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = datetime.now(timezone.utc).strftime("%H:%M UTC")

    rows = []
    for strat, data in sorted(weights.items()):
        w   = data.get("weight", 1.0) if isinstance(data, dict) else float(data)
        wr  = data.get("win_rate", 0.0) if isinstance(data, dict) else 0.0
        t   = data.get("trades", 0)     if isinstance(data, dict) else 0
        bar = "█" * int(w * 5) + "░" * max(0, 10 - int(w * 5))
        warn = " ⚠️" if w < 0.5 else (" 🏆" if w >= 1.5 else "")
        rows.append(f"| {strat}{warn} | {bar} {w:.2f}× | {wr:.1f}% | {t} |")

    table = "\n".join(rows) if rows else "| — | — | — | — |"

    md = f"""---
date: {date}
type: performance_snapshot
tags: [optimization, strategies, weights]
---

# Strategy Performance — {date}

| Strategy | Weight | Win Rate | Trades |
|----------|--------|----------|--------|
{table}

## Raw Weights JSON
```json
{json.dumps(weights, indent=2, default=str)[:1000]}
```

## Backlinks
- [[20_Daily_Notes/{date}]]
- [[06_Strategies/weight_history]]

---
_Snapshot written by OpenClaw at {written}_
"""

    filepath = folder / f"{date}_performance.md"
    filepath.write_text(md, encoding="utf-8")


def write_analysis(report: dict[str, Any]) -> None:
    """Write a Claude Opus analysis report to the vault.

    Args:
        report: AnalysisReport dict from runtime/claude_analyst.py
    """
    folder  = vault_path(_FOLDER)
    ts      = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    date    = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    health  = report.get("overall_health", "UNKNOWN")
    wr      = report.get("win_rate_pct", 0.0)
    exp     = report.get("expectancy_usd", 0.0)
    actions = report.get("immediate_actions", [])
    wadj    = report.get("weight_adjustments", {})

    icon = {"STRONG": "🟢", "MODERATE": "🟡", "WEAK": "🔴", "UNKNOWN": "⚪"}.get(health, "⚪")
    action_lines = "\n".join(f"- {a}" for a in actions) if actions else "- None"
    wadj_lines   = "\n".join(f"- **{k}**: ×{v:.2f}" for k, v in wadj.items()) if wadj else "- No adjustments"

    md = f"""---
date: {date}
type: claude_analysis
health: {health}
win_rate: {wr:.1f}
expectancy: {exp:.4f}
tags: [claude, analysis, optimization]
---

# {icon} Claude Opus Analysis — {date}

**Health:** {health} | **Win Rate:** {wr:.1f}% | **Expectancy:** ${exp:,.4f}

## Immediate Actions
{action_lines}

## Weight Adjustments
{wadj_lines}

## Full Report
```json
{json.dumps(report, indent=2, default=str)[:2000]}
```

## Backlinks
- [[20_Daily_Notes/{date}]]
- [[07_Optimization/{date}_performance]]

---
_Claude Opus analysis written at {ts}_
"""

    filepath = folder / f"analysis_{ts}.md"
    filepath.write_text(md, encoding="utf-8")
