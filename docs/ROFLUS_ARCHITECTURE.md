# OpenClaw — Orchestration Layer Architecture

**Covers**: RuntimeOrchestrator, QUIN, RufloBridge, SkillClock, IntentPipeline
**Last Updated**: 2026-05-25

---

## Overview

OpenClaw's orchestration layer is a multi-tier system that routes cognition, enforces authority hierarchy, and coordinates all subsystems. It is NOT a single component — it is five interlocking layers working in sequence.

```
USER INTENT / MARKET SIGNAL
         ↓
  SkillClock (10-skill pipeline)        runtime/skill_clock.py
         ↓
  QUIN Orchestrator (LLM gate)          runtime/quin_orchestrator.py
         ↓
  RuntimeOrchestrator (authority hub)  runtime/orchestrator.py
         ↓
  IntentPipeline (5-gate validation)   runtime/intent_pipeline.py
         ↓
  CapitalPreservationEngine (risk)      risk/capital_preservation.py
         ↓
  Executor (live orders only)           trading/executor.py
```

---

## Authority Hierarchy (strictly enforced)

| Priority | Component | Authority | Can block trades? |
|----------|-----------|-----------|-------------------|
| 1 | Kill Switch / Emergency Halt | SUPREME | Yes — absolute veto |
| 2 | CapitalPreservationEngine | AUTHORITATIVE | Yes — risk scalar = 0 |
| 3 | IntentPipeline | AUTHORITATIVE | Yes — 5 gates |
| 4 | QUIN Orchestrator | OPERATIONAL | Yes — HOLD decision |
| 5 | SkillClock signals | OPERATIONAL | No — generates intents |
| 6 | Ruflo HNSW advisory | ADVISORY | No — nudges confidence only |
| 7 | RegimeClassifier | ADVISORY | No — labels regime |
| 8 | Claude Opus / Brain | ADVISORY | No — suggests weights |

**Rule**: AI systems never have execution authority. Every AI output flows through at least one AUTHORITATIVE gate before reaching the executor.

---

## Layer 1: SkillClock — Structured Cognition Pipeline

**File**: `runtime/skill_clock.py` (484 lines)

The SkillClock is the heartbeat of the system. It runs 10 skills sequentially on every scan tick, building a shared `SkillContext` that passes state forward.

### 10 Skills (in execution order)

```
S1  MarketDataIngest      Fetch prices, candles, funding rates per symbol
S2  RegimeDetection       Classify market regime (TRENDING_BULL, RANGING, etc.)
S3  SignalGeneration      Run all 6 strategies × 3 symbols → raw signals
S4  RiskCapitalCheck      Gate on capital state + position limits
S5  ExecutionDecisioning  Weight-filter signals, pick best, build execution plan
S6  OrderManagement       Check SL/TP triggers on all open positions
S7  Reconciliation        Pull latest reconciliation report
S8  TelemetryHealth       Emit Prometheus metrics, build health snapshot
S9  LearningDrift         Track weight scheduler + drift detector status
S10 GovernanceAudit       Append full tick to data/skill_clock_audit.jsonl
```

### SkillContext fields

```python
tick_id:          str      # UUID per tick
tick_ts:          str      # ISO-8601 UTC
tick_number:      int      # monotonically increasing
market_data:      dict     # {symbol: {candles, ticker, funding}}  (S1 output)
regimes:          dict     # {symbol: regime_label}                 (S2 output)
signals:          list     # [StrategySignal, ...]                  (S3 output)
risk_state:       dict     # capital state + scalar                  (S4 output)
execution_plan:   list     # [{signal, size, approved}]             (S5 output)
position_updates: list     # SL/TP triggers + closes                (S6 output)
recon_result:     dict     # balance reconciliation snapshot        (S7 output)
health:           dict     # subsystem health dict                  (S8 output)
learning_updates: dict     # weight scheduler + drift status        (S9 output)
audit_result:     dict     # governance log entry                   (S10 output)
quin_decision:    dict     # QUIN verdict (set between S5 and exec)
errors:           list     # accumulated non-fatal errors
```

### Execution flow

```python
ctx = SkillContext(tick_id=..., tick_ts=..., tick_number=N)
for skill in [S1, S2, S3, S4, S5, S6, S7, S8, S9, S10]:
    skill.run(ctx, bot)
quin_decision = quin.decide(ctx)   # between S5 output and execution
# execution layer reads ctx.execution_plan + quin_decision
```

**Persistence**: Every tick → `data/skill_clock_audit.jsonl`

---

## Layer 2: QUIN Orchestrator — Local LLM Gate

**File**: `runtime/quin_orchestrator.py` (360 lines)

QUIN (Qwen Unified Intelligence Node) sits between SkillClock's `execution_plan` and the actual trade executor. It makes the final TRADE/HOLD decision using a local LLM.

### Model routing

```
Primary:   qwen2.5:14b via Ollama (local, free, private)
Fallback1: OpenRouter cloud (when OPENROUTER_API_KEY set + Ollama down)
Fallback2: Deterministic rule-based resolver (always available)
```

### Decision types

| Action | Meaning |
|--------|---------|
| TRADE | Proceed with execution at planned size |
| HOLD | Skip this signal this tick |
| SCALE_DOWN | Execute at reduced size (50%) |
| EMERGENCY_HALT | Write halt marker, alert Telegram |

### Rule-based fallback logic

When Ollama AND OpenRouter are unavailable:

```python
# Block known bad combos
if strategy == "TREND_FOLLOW" and regime in ["UNKNOWN", "LIQUIDITY_DROUGHT"]:
    return HOLD

# Require minimum weighted score
if signal_score < 0.5:
    return HOLD

# Capital state gate
if capital_state == "CRITICAL":
    return HOLD

# Per-strategy confidence floors
floors = {"EMA_CROSS": 0.65, "BREAKOUT": 0.70, "RSI_MEAN_REVERT": 0.60}
if confidence < floors.get(strategy, 0.55):
    return HOLD

return TRADE
```

### Audit trail

Every decision → `data/quin_decisions.jsonl`:
```json
{
  "decision_id": "uuid",
  "ts": "2026-05-25T14:30:00Z",
  "action": "TRADE",
  "confidence": 0.78,
  "reasoning": "...",
  "source": "ollama",
  "tick_id": "...",
  "signal": {...}
}
```

---

## Layer 3: RuntimeOrchestrator — Authority Hub

**File**: `runtime/orchestrator.py` (452 lines)

Central coordinator that enforces the authority hierarchy. All signals must flow through `process_signal()`.

### Signal processing flow

```python
def process_signal(symbol, strategy, action, confidence, ...):
    # 1. Check kill switch / halt marker
    if self._is_halted():
        return IntentVerdict(approved=False, reason="EMERGENCY_HALT")

    # 2. Record in replay journal
    self._journal.record("signal_generated", ...)

    # 3. Ruflo advisory (non-blocking, purely informational)
    advice = self._ruflo.pre_trade_advice(...)  # nudges confidence ±10%

    # 4. Build TradingIntent
    intent = TradingIntent(symbol, strategy, action, confidence, ...)

    # 5. IntentPipeline validation (AUTHORITATIVE)
    verdict = self._pipeline.validate(intent)

    # 6. Record verdict in EventStore
    self._event_store.append(EventType.INTENT_APPROVED/REJECTED, ...)

    return verdict
```

### Capital state updates

```python
def update_capital_state(equity: float):
    # Feeds live balance into CapitalPreservationEngine state machine
    old_state = self._capital.get_state()
    self._capital.update(equity)
    new_state = self._capital.get_state()
    if old_state != new_state:
        # Alert Telegram, log to EventStore, send Telegram notification
```

---

## Layer 4: IntentPipeline — 5-Gate Validation

**File**: `runtime/intent_pipeline.py` (186 lines)

AUTHORITATIVE safety layer. All 5 gates must pass for a trade to be approved.

### Gates (in order)

```
Gate 1: Schema validation
    action ∈ {long, short, close}
    confidence ∈ [0.0, 1.0]
    leverage ≤ 5×
    size_pct ≤ 4%
    sl_pct > 0, tp_pct > 0
    symbol in ALLOWED_SYMBOLS

Gate 2: Staleness check
    intent.expired_at > datetime.now(UTC)

Gate 3: Deduplication (thread-safe, atomic)
    (symbol, strategy, action) unique within 90-second TTL
    prevents duplicate signals in fast market conditions

Gate 4: Regime compatibility (advisory, fail-safe)
    is_strategy_compatible(strategy, regime)
    fails-CLOSED: if compatibility check crashes → deny

Gate 5: Capital preservation scalar
    scalar = CapitalPreservationEngine.get_risk_scalar()
    adjusted_size = size_pct × scalar
    if scalar == 0.0 (EMERGENCY_HALT state) → deny
```

### Verdict output

```python
@dataclass
class IntentVerdict:
    approved:          bool
    reason:            str    # "" if approved
    risk_scalar:       float  # 0.0 – 1.0
    adjusted_size_pct: float  # size × scalar, capped at 4%
```

---

## Layer 5: Ruflo — HNSW Memory Advisory

**File**: `runtime/ruflo_bridge.py` (413 lines), `runtime/ruflo_agent.py` (274 lines)

Ruflo connects to a Node.js MCP server (Ruflo: github.com/ruvnet/ruflo) that exposes ~210 tools including HNSW vector memory search.

### Architecture

```
OpenClaw (Python)
    ↓ RufloBridge (MCP JSON-RPC over stdio)
Ruflo subprocess (Node.js)
    ↓
HNSW memory + swarm agents
```

### Memory operations

```python
# Before a trade — look up similar setups
advice = advisor.pre_trade_advice(
    symbol="BTC-USDT", strategy="EMA_CROSS",
    action="LONG", confidence=0.72, regime="TRENDING_BULL"
)
# Returns: {similar_wins, similar_losses, win_rate, avg_pnl, confidence_adj}
# confidence_adj clamped to [-0.10, +0.10]

# After trade closes — store for future lookup
advisor.record_outcome(
    symbol, strategy, pnl=42.5, regime="TRENDING_BULL", win=True
)
```

### Cloud limitation

**Ruflo is unavailable in Railway** (requires Node.js subprocess + local filesystem). In cloud: `RufloAdvisor.available = False`, all advice calls return `RufloAdvice(available=False)` and are skipped. No impact on execution — purely advisory.

---

## Strategy Governance Layer

**File**: `runtime/strategy_governance.py` (592 lines)

Background process that monitors strategy health and applies governance actions.

### Governance actions (priority order)

| Action | Trigger | Effect |
|--------|---------|--------|
| QUARANTINE | expectancy < -$5 + ≥20 trades | weight → 0.10 |
| REDUCE_WEIGHT | decay severity > 0.70 | weight × 0.80, floor 0.20 |
| FREEZE_OPTIMIZATION | overfitting score > 0.60 | disable shadow optimization |
| DISABLE_IN_REGIME | regime blindness detected | advisory log only |
| CLAMP_CONFIDENCE | calibration < 0.30 | suggest 0.75 cap (advisory) |
| NO_ACTION | all metrics healthy | — |

All actions route through `ShadowOptimizationEngine` — no direct weight file edits. Audit trail: `data/governance_decisions.jsonl`.

---

## Event Sourcing + Audit Trail

**File**: `runtime/event_store.py` (837 lines)

25 event types, monotonically increasing sequence numbers, SHA-256 checksums per event.

### Event types

```
Signal:    SIGNAL_GENERATED, INTENT_CREATED, INTENT_REJECTED
Positions: POSITION_OPENED, POSITION_CLOSED, POSITION_PARTIALLY_FILLED
Capital:   CAPITAL_STATE_CHANGED, EMERGENCY_HALT, HALT_RELEASED
Orders:    ORDER_SUBMITTED, ORDER_ACKNOWLEDGED, ORDER_REJECTED, ORDER_CANCELLED
Risk:      SL_TRIGGERED, TP_TRIGGERED
Ops:       RECONCILIATION_COMPLETE, RECONCILIATION_INCIDENT, EXECUTION_FAILURE,
           DRIFT_DETECTED, WEBSOCKET_RECONNECT, WEBSOCKET_FAILURE, EXECUTION_TIMEOUT
Weights:   STRATEGY_WEIGHT_CHANGED
```

### Recovery

```python
engine = EventReplayEngine(event_store)
state  = engine.reconstruct_portfolio_state(up_to_seq=None)
# Returns: {capital_state, open_positions, realized_pnl, halt_reason, strategy_trade_counts}
```

---

## What's Working vs Broken

| Component | Status | Notes |
|-----------|--------|-------|
| SkillClock 10-skill pipeline | ✅ Working | Full audit trail |
| QUIN Orchestrator | ✅ Working | Rule-based always available |
| RuntimeOrchestrator | ✅ Working | Authority hierarchy enforced |
| IntentPipeline 5 gates | ✅ Working | Schema + capital + regime |
| CapitalPreservationEngine | ✅ Working | Persists across restarts |
| EventStore + checksums | ✅ Working | Append-only, recoverable |
| WeightApplicationDaemon | ✅ Working | Midnight atomic writes |
| StrategyGovernance | ✅ Working | Priority-ordered rules |
| Ruflo HNSW (local) | ✅ Working | Requires Node.js |
| Ruflo HNSW (Railway) | ❌ Unavailable | No Node.js in Docker |
| Claude Sonnet routing tier | ❌ Missing | Jumps Haiku → Opus |
| Obsidian vault writes | ✅ Fixed 2026-05-25 | obsidian/ pkg added |
| Context injection from vault | ❌ Not built | No retrieval pipeline |
