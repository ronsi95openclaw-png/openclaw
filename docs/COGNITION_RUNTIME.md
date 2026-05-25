# OpenClaw — Cognitive Runtime Design

**Last Updated**: 2026-05-25

---

## Overview

OpenClaw's cognition is distributed across four layers with distinct responsibilities. No single model handles everything. The design separates executive reasoning (Claude Opus), operational decision-making (QUIN/qwen2.5), per-scan structured analysis (SkillClock), and compression/memory (qwen3). Each layer has a defined scope and authority level.

---

## Cognitive Hierarchy

```
LAYER 4: Executive Intelligence (Claude Opus 4.7)
         runtime/claude_analyst.py
         Role: Strategic analysis, weight recommendations, pattern discovery
         Frequency: NIGHTLY (midnight UTC)
         Authority: ADVISORY — generates weight_adjustments applied by daemon
         Context: data/logs/trade_outcomes.jsonl + strategy_weights.json
         Output: data/optimization/analysis_<ts>.json

         ↓ weight_adjustments applied by WeightApplicationDaemon

LAYER 3: Operational Decision Gate (QUIN — qwen2.5:14b)
         runtime/quin_orchestrator.py
         Role: Per-scan TRADE/HOLD/SCALE_DOWN/HALT decision
         Frequency: EVERY SCAN TICK (30-60s)
         Authority: ADVISORY — output flows into IntentPipeline
         Context: Full SkillContext (10 skill outputs)
         Output: QuinDecision {action, confidence, reasoning}

         ↓ QuinDecision → RuntimeOrchestrator.process_signal()

LAYER 2: Structured Cognition Pipeline (SkillClock — deterministic)
         runtime/skill_clock.py
         Role: 10 sequential skills per tick, shared context building
         Frequency: EVERY SCAN TICK (30-60s)
         Authority: STRUCTURAL — shapes what QUIN sees
         Context: Market data, strategy signals, capital state, positions
         Output: SkillContext with all 10 skill outputs

         ↓ SkillContext → QUIN

LAYER 1: Memory Compression (qwen3 local)
         runtime/qwen_compressor.py
         Role: Per-trade lesson extraction
         Frequency: EVERY TRADE CLOSE
         Authority: INFORMATIONAL — writes to Obsidian only
         Context: Trade record + outcome
         Output: 2-sentence lesson appended to trade journal
```

---

## Claude Opus as Executive Layer

### What it does

Claude Opus reads the entire recent trade history and current strategy weights, then returns a structured JSON with:
- Per-strategy health assessment (win rate, expectancy, regime performance)
- Weight adjustments to apply at midnight
- Strategies to block in specific regimes
- Stop-loss recommendations
- Concrete code-implementable improvements

### Prompt structure (runtime/claude_analyst.py)

```python
_ANALYSIS_PROMPT = """
You are analyzing a crypto trading bot's recent trade history (Crypto.com futures).
Find patterns and generate CONCRETE, code-implementable improvements.

## Trade Outcomes — last {n} closed trades
{outcomes_json}

## Current Strategy Weights (1.0 = normal, 2.0 = double, 0.2 = 20%)
{weights_json}

## Aggregated Stats by Strategy
{strategy_summary}

## Task
Return ONLY a valid JSON object:
{
  "weight_adjustments": {"STRATEGY": new_weight_float, ...},
  "blocked_strategies": {"STRATEGY": ["REGIME1", ...], ...},
  "sl_recommendations": {"STRATEGY": max_sl_pct_float, ...},
  "immediate_actions": ["...", ...],
  "health_summary": {"strategy": {"win_rate": 0.52, "expectancy": ...}, ...},
  "reflection": "..."
}
"""
```

### Midnight cadence

```
23:59:30 UTC  WeightApplicationDaemon wakes (30-second sleep chunks)
00:00:00 UTC  Daemon detects new day
              → Reads newest data/optimization/analysis_*.json
              → If mtime newer than last applied → apply weight_adjustments
              → Clamp to [0.1, 2.0]
              → Snapshot prior weights to data/weight_snapshots/
              → Append to data/weight_adjustments_audit.jsonl
              → Log completion
00:00:05 UTC  CryptoComBot._auto_apply_opus_weights() (redundant safety call)
```

The daemon uses file-lock (`fcntl.flock`) and atomic temp-file rename for safe writes.

### Gap: No Obsidian context injection

Currently, Claude Opus sees only the raw trade outcomes JSONL and strategy weights JSON. It does NOT see:
- Previous analysis reports (no chaining)
- Vault trade journal notes (no pattern context)
- Strategy evolution history from Obsidian
- QUIN decision patterns
- Regime distribution history

Adding context injection would significantly improve analysis quality. See `docs/OBSIDIAN_MEMORY_SYSTEM.md` for the proposed retriever design.

---

## QUIN as Operational Gate

### Decision context

QUIN receives the full `SkillContext` after all 10 skills run. Relevant fields:

```python
ctx.signals          # [StrategySignal, ...] — all signals this tick
ctx.regimes          # {symbol: regime_label}
ctx.risk_state       # {scalar: 0.8, state: "DEFENSIVE"}
ctx.execution_plan   # best signal chosen by S5
ctx.market_data      # prices, candles
ctx.health           # survivability score
```

### LLM prompt structure (when Ollama available)

QUIN constructs a compact prompt with:
1. Current regime per symbol
2. Top 3 strategy signals with confidence scores
3. Capital state and risk scalar
4. Open positions count
5. Recent QUIN decisions (last 3, from _DECISIONS_PATH)

The prompt asks for a JSON response: `{"action": "TRADE", "confidence": 0.75, "reasoning": "..."}`.

### Rule-based fallback (always available)

When Ollama is down or times out (10s):

```python
# QUIN rule-based decision:
1. EMERGENCY_HALT  → if capital state is EMERGENCY_HALT
2. HOLD            → if no signals in ctx
3. HOLD            → if strategy blocked in current regime
4. HOLD            → if best signal confidence < per-strategy floor
5. SCALE_DOWN      → if capital state is CRITICAL
6. TRADE           → otherwise
```

This fallback is **always used on Railway** (no Ollama available).

### QUIN learns from context but not from feedback

QUIN's rule-based fallback is static — it does not update from trade outcomes. The LLM model (qwen2.5:14b) also does not fine-tune. Learning happens at Layer 4 (Opus) and is applied via weight adjustments, not QUIN logic changes.

---

## SkillClock as Structured Cognition

### Why deterministic?

The SkillClock is not an LLM. It's a sequential pipeline of 10 deterministic functions that structure the cognitive state before any AI model is consulted. This ensures:
- Market data is always fresh before strategy evaluation
- Capital state is always checked before signal selection
- Positions are always reconciled before execution decisions

### Information flow

```
Skill 1 output (market_data) → used by Skills 2, 3, 5, 6, 8
Skill 2 output (regimes)     → used by Skills 3, 4, 5, QUIN
Skill 3 output (signals)     → used by Skills 4, 5, QUIN
Skill 4 output (risk_state)  → used by Skills 5, 6, QUIN
Skill 5 output (exec_plan)   → used by QUIN, executor
Skill 6 output (positions)   → used by Skills 7, 8
Skill 7 output (recon)       → used by Skill 8
Skill 8 output (health)      → used by Skill 9, 10
Skill 9 output (learning)    → used by Skill 10
Skill 10 output (audit)      → persisted to JSONL
```

### Failure isolation

Each skill is wrapped in a try/except. A failure in Skill 3 (signal generation) does not stop Skill 4-10. The error is appended to `ctx.errors` and the tick continues. At Skill 10, all errors are written to the audit log.

This means the system degrades gracefully — a broken signal generator causes zero signals this tick (HOLD decision), but does not halt the bot.

---

## Memory Retrieval Pipeline

### Current state: write-heavy, read-poor

```
Trade close → qwen3 compresses lesson → Obsidian vault (write)
Strategy change → strategy_writer → Obsidian vault (write)
Trade close → HNSW store → Ruflo memory (write, local only)
Midnight → Opus analysis → vault + data/optimization/ (write)

No read pipeline exists. All context for AI calls is assembled from:
  - data/logs/trade_outcomes.jsonl (last N trades, flat JSON)
  - data/strategy_weights.json (current weights)
  - data/capital_state.json (current capital state)
```

### Proposed read pipeline (priority order)

**Phase 1: JSONL index retrieval** (no new infrastructure needed)
```python
# Read from Obsidian index files:
05_Trading/_index.jsonl  → recent_trades(symbol, strategy, outcome, limit)
06_Strategies/weight_history.jsonl → strategy_history(strategy, last_n)
```

**Phase 2: Claude Analyst context injection**
```python
# Before Opus analysis prompt:
recent_vault_trades = retriever.get_recent_trades(limit=20)
ema_weight_history  = retriever.get_strategy_history("EMA_CROSS")
# Inject as additional context section in _ANALYSIS_PROMPT
```

**Phase 3: QUIN context injection**
```python
# Before QUIN decision:
similar_setups = retriever.get_recent_trades(
    symbol=signal.symbol, strategy=signal.strategy, limit=5
)
# Include in QUIN prompt as "Historical similar setups"
```

**Phase 4: Ruflo HNSW in cloud** (harder — requires alternative to Node.js subprocess)
```python
# Option: run Ruflo as separate Railway service with HTTP transport
# RUFLO_MCP_TRANSPORT=http
# RUFLO_MCP_URL=https://ruflo-service.railway.app
```

---

## Context Compression

### Current compression (qwen_compressor.py)

After every trade close, the bot generates a compressed lesson:

```python
class QwenCompressor:
    def compress_trade(self, trade_record: dict) -> str:
        """Generate 2-sentence lesson from trade outcome."""
        prompt = f"""
Trade: {trade_record['strategy']} on {trade_record['symbol']}
Result: {trade_record['outcome']} | PnL: {trade_record['pnl']:+.2f}
Regime: {trade_record.get('regime', 'UNKNOWN')}
Reason: {trade_record.get('signal_reason', '')}

Generate a 2-sentence trading lesson. Be specific. Start with the pattern, end with the rule.
"""
        return ask_llm(prompt, task="compression")  # qwen3 primary
```

### History compression (core/brain.py)

Before any Claude call, conversation history is trimmed to the last 6 turns:

```python
def _compress_history(history: List[dict], max_turns: int = 6) -> List[dict]:
    return history[-max_turns:] if len(history) > max_turns else history
```

Prompt compression strips filler words before sending to Claude:
- "please", "kindly", "could you", "would you", "i would like you to"
- "can you", "just", "simply", "basically", "literally", "actually"
- "very", "really", "i want you to", "make sure to", "feel free to"

---

## Reflection Loops

### Existing loop: Nightly Opus → Midnight weight application

```
Trades close → outcome appended to data/logs/trade_outcomes.jsonl
Midnight → ClaudeAnalyst reads outcomes → generates analysis JSON
Midnight+5s → WeightApplicationDaemon reads JSON → applies to strategy_weights.json
Next scan → StrategyWeightEngine loads updated weights → signals scaled accordingly
```

This is a 24-hour feedback loop. Improvements from Opus analysis take effect the next day.

### Existing loop: Per-trade weight adjustment

Each trade close triggers immediate weight update in `StrategyWeightEngine`:

```python
def record_outcome(self, strategy: str, won: bool) -> None:
    stats = self.stats[strategy]
    stats.trades += 1
    if won:
        stats.wins += 1
    else:
        stats.losses += 1
    stats.recent_outcomes.append(won)
    if len(stats.recent_outcomes) > 20:
        stats.recent_outcomes.pop(0)

    # Recency-weighted win rate (most recent = 1.0, each older = 0.85x decay)
    new_weight = self._compute_weight(stats)
    stats.weight = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, new_weight))
```

This is a real-time feedback loop. Weight changes take effect on the next scan.

### Missing loop: QUIN self-improvement

QUIN's rule-based fallback does not update from trade outcomes. If QUIN's HOLD decision on a specific pattern consistently precedes missed winning trades, there is no mechanism to update the hold threshold. This would require:
1. Logging QUIN decisions with context
2. Backtesting QUIN decisions against actual outcomes
3. Updating rule thresholds based on analysis

---

## Cognition Evolution Over Time

### How the system learns

```
Week 1:   Baseline — all strategies at 1.0x weight
          SkillClock generates signals, QUIN approves/holds
          Trades execute, outcomes recorded

Week 2:   First Opus analysis
          EMA_CROSS already at 0.4x (recency-weighted down)
          Opus confirms: "EMA_CROSS win rate 52% — marginal, consider block in RANGING"
          Weight application: EMA_CROSS → 0.35x

Month 1:  Strategy differentiation
          DCA at 1.7x (no losses yet — new strategy)
          TREND_FOLLOW at 0.3x (blocked in TRENDING_BULL)
          Ruflo HNSW accumulates 100+ trade embeddings

Month 3:  Regime-aware weights
          Opus begins identifying regime-strategy correlations
          BREAKOUT weight varies by regime (via blocked_strategies)
          Qwen compressor lessons accumulate in Obsidian vault

Month 6:  Stable performance layer
          Strategy weights stabilize around their "true" values
          QUIN rule-based thresholds well-tested against outcomes
          Obsidian vault has 500+ trade notes, weekly patterns

Month 12: Knowledge compounding
          Obsidian context injection improves Opus analysis quality
          Historical patterns from vault inform QUIN decisions
          Self-healing weight system maintains above-market returns
```

### Goal milestone cognition

The GoalTracker ($98 → $50K) influences position sizing indirectly:
- As balance grows, CapitalPreservationEngine alltime_peak increases
- Drawdown thresholds become more sensitive at higher balances
- Risk scalar decreases earlier, protecting gains

At milestone hits ($200, $500, $1000, etc.), Telegram alerts notify Ronnie. No automatic strategy change on milestone — cognitive adjustment is purely via Opus analysis.
