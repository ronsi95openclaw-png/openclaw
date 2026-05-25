# Future Roadmap

> Last updated: 2026-05-25

## Current State Summary

- **Balance**: $295.30 (started $98, +201%)
- **Goal**: $98 → $50,000 (8 milestones)
- **Next milestone**: $500
- **Railway**: ACTIVE, DEGRADED mode (simulation only, real trading = local machine)
- **Critical fix this session**: Obsidian vault writes restored (obsidian/ package)

---

## 30-Day Sprint (Priority Order)

### Sprint 1: Memory Retrieval (Week 1)
**Impact**: High — unlocks context-aware Opus analysis and QUIN historical awareness

**Task 1.1**: Build `obsidian/retriever.py`
```python
# JSONL-index based retrieval (no embeddings needed yet)
get_recent_trades(symbol, strategy, outcome, limit) → list[dict]
get_strategy_history(strategy, last_n) → list[dict]
get_daily_note(date) → str
get_weekly_pattern_summary() → str
```

**Task 1.2**: Inject vault context into Claude Opus prompt
```python
# runtime/claude_analyst.py — before building prompt:
from obsidian.retriever import get_recent_trades, get_weekly_pattern_summary
recent_similar = get_recent_trades(limit=20)
weekly_pattern = get_weekly_pattern_summary()
# prepend to analysis prompt
```

**Task 1.3**: Inject historical context into QUIN
```python
# runtime/quin_orchestrator.py — QUIN prompt:
from obsidian.retriever import get_recent_trades
similar = get_recent_trades(symbol=symbol, strategy=strategy, limit=5)
# adds "5 similar past setups" to QUIN context
```

---

### Sprint 2: Claude Sonnet Routing Tier (Week 1)
**Impact**: Medium — reduces Opus costs, improves mid-complexity reasoning

Add `ask_sonnet()` to `core/brain.py` and update `ask_hybrid()`:

```
Task word count < 50, no keywords     → Ollama / OpenRouter (Tier 0/1)
Task word count 50–150, has keywords  → Claude Sonnet (Tier 3)
Task word count > 150 OR nightly      → Claude Opus (Tier 4)
```

**Files to modify**: `core/brain.py`
**Test**: Run Telegram `/goal` query, verify Sonnet is used instead of Opus.

---

### Sprint 3: Weekly Consolidation (Week 2)
**Impact**: Medium — enables Opus to see multi-week trends, not just yesterday

Build `obsidian/consolidator.py`:
```python
def consolidate_weekly():
    """Run every Sunday at midnight UTC."""
    trades = get_recent_trades(limit=100)  # last 7 days
    by_strategy = group_by_strategy(trades)
    
    for strategy, t_list in by_strategy.items():
        wins   = [t for t in t_list if t["outcome"] == "win"]
        losses = [t for t in t_list if t["outcome"] == "loss"]
        wr     = len(wins) / len(t_list) * 100 if t_list else 0
        
        # Write weekly pattern note
        write_pattern_note(strategy, wr, wins, losses)
    
    # Write weekly summary to 04_Research/
    write_weekly_summary(date, total_pnl, best_strategy, worst_strategy)
```

Wire into `WeightApplicationDaemon` for weekly trigger.

---

### Sprint 4: Opus Context Chaining (Week 2)
**Impact**: High — Opus can see its own previous recommendations, enabling self-improvement

Currently: Each Opus call is independent (no memory of prior analysis).

Fix: Load last 3 analysis summaries from vault into Opus prompt:
```python
# In run_analysis():
from obsidian.retriever import get_recent_analyses
prior_analyses = get_recent_analyses(limit=3)
# Each: {date, health, key_actions, adjustments_applied}
# Prepend to prompt as "Historical context"
```

---

### Sprint 5: Ruflo HTTP Transport (Week 3)
**Impact**: Medium — enables HNSW memory advisory in Railway cloud

The RufloBridge supports HTTP+SSE transport but it's not implemented (`"not yet implemented"` in code).

Options:
- A: Run Ruflo as a separate Railway service (HTTP transport between services)
- B: Use Railway's internal networking + HTTP transport

```python
# runtime/ruflo_bridge.py — HTTP transport:
class RufloBridge:
    def __init__(self, transport="http", http_port=3001):
        # Connect to Ruflo HTTP server instead of spawning subprocess
        self._http_url = f"http://ruflo-service:{http_port}"
```

**Prerequisite**: Add `ruflo-service` to Railway project with Node.js Dockerfile.

---

### Sprint 6: Dashboard Next.js on Railway (Week 3)
**Impact**: Low-Medium — visual monitoring from anywhere

Currently: Next.js dashboard runs locally only (:3000), Railway only has the API (:8000).

Option A: Add second Railway service for Next.js
Option B: Build a minimal HTML dashboard served by FastAPI (no Next.js dependency)

Option B is simpler — add a static HTML file to `dashboard/api/server.py`:
```python
@app.get("/", response_class=HTMLResponse)
async def dashboard_home():
    return Path("dashboard/static/index.html").read_text()
```

---

## 90-Day Vision

### Month 1: Memory + Routing

- ✅ Obsidian writes working (done)
- Obsidian reads + context injection
- Claude Sonnet routing tier
- Weekly consolidation
- Opus context chaining

### Month 2: Intelligence Amplification

- QUIN learns from Obsidian (not just rule-based)
- Regime-aware strategy selection improves
- Pre-trade advice from vault patterns (not just Ruflo HNSW)
- Automated postmortem generation after losing days

### Month 3: Scaling + Autonomy

- Ruflo HTTP transport for cloud
- Dashboard on Railway
- Self-healing: auto-restart on anomaly detection
- Autonomous weight optimization via Sonnet (not just Opus nightly)
- Monthly strategy retirement/promotion pipeline

---

## Milestone Tracker ($98 → $50,000)

| Milestone | Target | Status |
|-----------|--------|--------|
| 1 | $200 | ✅ Hit |
| 2 | $500 | 🎯 Next (~$295 now) |
| 3 | $1,000 | — |
| 4 | $2,500 | — |
| 5 | $5,000 | — |
| 6 | $10,000 | — |
| 7 | $25,000 | — |
| 8 | $50,000 | — |

At current trajectory (+201% from $98 in ~90 days):
- Estimated: 2–4× per 90 days in DEMO mode
- Real trading (when enabled): depends on live slippage, funding, API reliability

---

## Architecture Evolution Path

```
TODAY:
Claude Opus ←→ JSONL logs
    ↓
QUIN ← rule-based (fallback always)
    ↓
Obsidian (writes only)

3 MONTHS:
Claude Opus ←→ JSONL + Obsidian vault (with context injection)
    ↓
Claude Sonnet ← mid-complexity coordination
    ↓
QUIN ← vault-enhanced context (similar past setups)
    ↓
Obsidian (read + write, weekly consolidation, pattern summaries)

6 MONTHS:
Claude Opus ←→ full memory graph (vault + HNSW)
    ↓
Ruflo HTTP ← cloud HNSW memory (Railway service)
    ↓
QUIN ← fine-tuned on past decision outcomes
    ↓
Obsidian ← self-generating: postmortems, roadmaps, reflections
```

---

## What Never Changes

These design principles are permanent regardless of evolution:

1. **AI systems never have execution authority** — all AI outputs flow through IntentPipeline
2. **Fail-closed everywhere** — broken subsystems → HOLD, not pass-through
3. **Append-only audit trails** — EventStore + ReplayJournal + JSONL logs are immutable
4. **Local-first inference** — Ollama before OpenRouter before Claude
5. **DEMO_MODE=true default** — never auto-switch to live trading
6. **Claude + Qwen ecosystem** — no GPT/OpenAI primary models
7. **Capital preservation takes precedence** — SAFE > DEFENSIVE > CRITICAL > HALT
