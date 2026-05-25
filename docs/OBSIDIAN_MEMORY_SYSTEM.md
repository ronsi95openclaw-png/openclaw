# Obsidian Memory System

> Last updated: 2026-05-25

## Status: FIXED 2026-05-25

The Obsidian vault integration was completely broken. All trade closes, daily flushes, and weight changes attempted `import obsidian.*` via `~/ai-system` path injection — a directory that doesn't exist in this environment. All writes silently failed (caught by bare `try/except`).

**Fix**: Shipped `obsidian/` package directly in the repo. All vault writes now work.

---

## Vault Location

```
~/AI-Operating-System-Vault/   (confirmed exists)
```

### Folder Structure

```
00_Dashboard/     — live bot status, KPI snapshots
01_Architecture/  — system design, component diagrams
02_Projects/      — project plans, roadmaps
03_Agents/        — AI agent definitions, system prompts
04_Research/      — market research, regime studies
05_Trading/       — trade journal entries (one note per trade)
06_Strategies/    — strategy weight history, evolution notes
07_Optimization/  — daily performance snapshots, Opus analysis reports
08_Logs/          — system event logs
09_Replay/        — decision replay journals
10_Governance/    — governance decisions, approvals
11_Security/      — security events, audit
12_Deployments/   — Railway deployments, build history
13_Memory/        — Ruflo memory exports, HNSW snapshots
14_Prompts/       — system prompts, CLAWBOT_SYSTEM versions
15_Workflows/     — automation workflows
16_Documentation/ — technical docs
17_Postmortems/   — incident reports
18_Roadmaps/      — feature roadmaps
19_Resources/     — reference material
20_Daily_Notes/   — daily session notes (one per day)
```

---

## obsidian/ Package (repo: /home/user/openclaw/obsidian/)

Four writers, one vault:

### obsidian/trade_journal_writer.py

**Writes to**: `05_Trading/YYYY-MM-DD_SYMBOL_STRATEGY_id.md`  
**Triggered by**: Every trade close (`trading/cryptocom_bot.py:1279`)  
**Index**: `05_Trading/_index.jsonl` (fast retrieval by strategy/symbol/outcome)

```python
write_trade(outcome_record: dict) -> None
```

Fields used from `outcome_record`:
- symbol, strategy, side, entry_price, exit_price, pnl, outcome
- regime_label (from SkillClock S2)
- lesson (from qwen_compressor — compressed 2-sentence lesson)
- closed_at / ts
- id (first 8 chars used in filename)

Output note structure:
```markdown
---
date: 2026-05-25
symbol: BTC
strategy: EMA_CROSS
outcome: win
pnl: 42.30
regime: TRENDING_BULL
tags: [trading, btc, ema_cross, win]
---

# ✅ BTC LONG [EMA_CROSS] — +$42.30
## Lesson (Qwen)
[2-sentence compressed lesson from qwen_compressor]
## Context
[raw trade record JSON, truncated at 800 chars]
## Backlinks
- [[05_Trading/2026-05-25_daily]]
- [[06_Strategies/EMA_CROSS]]
```

---

### obsidian/vault_manager.py

**Writes to**: `20_Daily_Notes/YYYY-MM-DD.md`  
**Triggered by**: `flush_daily_summary()` in cryptocom_bot.py  
**Frequency**: Once per day (UTC midnight boundary)

```python
write_daily_note(date, total_pnl, trades_today, wins, losses, notes) -> None
```

Output: P&L summary table, win rate, backlinks to trade journal and performance snapshot.

---

### obsidian/optimization_writer.py

**Writes to**: `07_Optimization/`  
**Two functions**:

`write_strategy_performance(weights)` — called at daily flush  
→ `07_Optimization/YYYY-MM-DD_performance.md`  
→ Weight table with visual bars (█ █ █ ░ ░), warn icons for low-weight strategies

`write_analysis(report)` — called after Claude Opus analysis completes  
→ `07_Optimization/analysis_YYYYMMDDTHHMMSS.md`  
→ Full Opus report: health, win rate, expectancy, immediate actions, weight adjustments

---

### obsidian/strategy_writer.py

**Writes to**: `06_Strategies/STRATEGY_NAME.md`  
**Triggered by**: Every strategy weight change (`trading/strategies.py:516`)  
**Index**: `06_Strategies/weight_history.jsonl`

```python
write_strategy_evolution(strategy, old_weight, new_weight, reason, trades, win_rate) -> None
```

Creates strategy note on first write. Appends a table row on every subsequent write:
```
| 2026-05-25T14:32 | 0.380× | **0.400×** | ⬆️ +0.020 | win recorded | 25 | 52.0% |
```

---

## Memory System Map

```
WRITE PATHS (all working post-fix):

Trade closes ──────────────────────→ 05_Trading/*.md
                                      05_Trading/_index.jsonl

Strategy weight changes ────────────→ 06_Strategies/*.md
                                      06_Strategies/weight_history.jsonl

Daily flush ────────────────────────→ 20_Daily_Notes/YYYY-MM-DD.md
                                      07_Optimization/YYYY-MM-DD_performance.md

Claude Opus analysis (nightly) ─────→ 07_Optimization/analysis_*.md
                                      data/optimization/analysis_*.json (also)

HNSW memory (Ruflo, local only):
Pre-trade advice ───────────────────→ Ruflo Node.js subprocess
Trade outcomes recorded ─────────────→ Ruflo HNSW memory store

READ PATHS (not yet built):
Obsidian → Claude context injection ← MISSING
Obsidian → retrieval API            ← MISSING
HNSW     → pre-trade advisory       ← Works locally, fails in cloud
```

---

## Retrieval Pipeline (Not Yet Built)

The vault is write-only. No code currently reads from it to inject context into Claude calls.

### Proposed retrieval design

**Step 1: Index-based retrieval** (no embeddings needed)

```python
# obsidian/retriever.py
def get_recent_trades(symbol=None, strategy=None, outcome=None, limit=10) -> list[dict]:
    """Read from 05_Trading/_index.jsonl and filter."""
    idx = load_jsonl("~/AI-Operating-System-Vault/05_Trading/_index.jsonl")
    filtered = [e for e in idx
                if (symbol is None or e["symbol"] == symbol)
                and (strategy is None or e["strategy"] == strategy)
                and (outcome is None or e["outcome"] == outcome)]
    return filtered[-limit:]

def get_strategy_history(strategy: str, last_n: int = 20) -> list[dict]:
    """Read from 06_Strategies/weight_history.jsonl."""
    history = load_jsonl(f"~/AI-Operating-System-Vault/06_Strategies/weight_history.jsonl")
    return [e for e in history if e["strategy"] == strategy][-last_n:]

def get_daily_note(date: str) -> str:
    """Read a daily note by date."""
    path = f"~/AI-Operating-System-Vault/20_Daily_Notes/{date}.md"
    return Path(path).read_text() if Path(path).exists() else ""
```

**Step 2: Context injection into Claude Analyst**

```python
# In runtime/claude_analyst.py, before building the prompt:
from obsidian.retriever import get_recent_trades, get_strategy_history

recent_wins  = get_recent_trades(outcome="win", limit=5)
recent_loss  = get_recent_trades(outcome="loss", limit=5)
ema_history  = get_strategy_history("EMA_CROSS", last_n=10)

context = f"""
Recent wins: {json.dumps(recent_wins[:3])}
Recent losses: {json.dumps(recent_loss[:3])}
EMA_CROSS last 10 weights: {[e['new_weight'] for e in ema_history]}
"""
# Prepend to analysis prompt
```

**Step 3: Context injection into QUIN**

```python
# Before QUIN decides, inject relevant historical context:
similar = get_recent_trades(symbol=symbol, strategy=strategy, limit=5)
lesson_ctx = "\n".join(
    f"- {t['date']}: {t['outcome']} PnL={t['pnl']:+.2f} regime={t['regime']}"
    for t in similar
)
# Add to QUIN prompt
```

---

## Memory Consolidation (Proposed)

Currently: notes accumulate indefinitely.  
Proposed: nightly consolidation run by Claude Opus at midnight.

```python
# obsidian/consolidator.py
def consolidate_weekly():
    """Summarize the past 7 days of trades into a weekly reflection note."""
    trades   = get_recent_trades(limit=50)  # last week
    patterns = identify_patterns(trades)    # win patterns, loss patterns
    
    # Write to 17_Postmortems/ or 04_Research/
    write_reflection_note(patterns)
```

This creates a hierarchical memory: individual trade notes → weekly patterns → monthly strategy arcs → lifetime knowledge graph.

---

## Integration Checklist

| Component | Status |
|-----------|--------|
| obsidian/ package in repo | ✅ Done |
| trade_journal_writer | ✅ Done |
| vault_manager (daily note) | ✅ Done |
| optimization_writer (performance) | ✅ Done |
| optimization_writer (analysis) | ✅ Done — call `write_analysis(report)` from claude_analyst.py |
| strategy_writer (weight evolution) | ✅ Done |
| Retrieval API (obsidian/retriever.py) | ❌ Not built |
| Context injection → Claude Analyst | ❌ Not built |
| Context injection → QUIN | ❌ Not built |
| Weekly consolidation | ❌ Not built |
| Vector embeddings | ❌ Not planned (JSONL index is sufficient) |
