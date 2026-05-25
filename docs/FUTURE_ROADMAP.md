# OpenClaw — Future Roadmap

**Last Updated**: 2026-05-25
**Current balance**: ~$295.30 | **Return**: +201% | **Goal**: $50,000

---

## Principles

1. **Fix broken things before adding new things** — Obsidian writes were broken for weeks; that was more damaging than any missing feature
2. **Local-first** — cloud should enhance, not replace, local intelligence
3. **Observe before you optimize** — measure the gap before choosing a model upgrade
4. **Capital preservation first** — at $295 the risk is ruin; at $5000 the risk is plateau
5. **Compounding memory** — every trade should make the next trade smarter

---

## Critical Fixes (Do These First)

### Fix 1: Data persistence on Railway

**Problem**: `data/` directory is ephemeral on Railway. Every restart loses QUIN decisions, replay journal, scan audit, and bot state.

**Solution**: Mount Railway volume at `/app/data/`

```toml
# railway.toml
[volumes]
data = "/app/data"
```

Or use Railway Postgres for structured state:
```python
# runtime/state_backend.py
class StateBackend:
    def __init__(self):
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            self._backend = PostgresBackend(db_url)
        else:
            self._backend = FilesystemBackend("data/")
```

**Priority**: HIGH — data loss on restart undermines audit trail integrity
**Effort**: 1-2 hours

---

### Fix 2: JSONL rotation policy

**Problem**: `replay_journal.jsonl` grows ~70MB/day. After 30 days: ~2GB. Disk exhaustion is a real risk.

**Solution**: Add rotation daemon

```python
# runtime/log_rotator.py
class LogRotator:
    """Rotate JSONL logs daily, compress with gzip, keep 30 days."""

    ROTATE_FILES = [
        "data/replay_journal.jsonl",
        "data/quin_decisions.jsonl",
        "data/skill_clock_audit.jsonl",
        "data/execution_analytics.jsonl",
    ]
    KEEP_DAYS = 30

    def rotate_all(self):
        for path in self.ROTATE_FILES:
            self._rotate(Path(path))

    def _rotate(self, path: Path):
        if not path.exists():
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        archive = path.parent / f"{path.stem}_{ts}.jsonl.gz"
        with gzip.open(archive, "wb") as gz:
            gz.write(path.read_bytes())
        path.write_text("")  # truncate, not delete
        self._prune_old(path.parent, path.stem, self.KEEP_DAYS)
```

**Priority**: HIGH — disk exhaustion kills the bot
**Effort**: 2-3 hours

---

### Fix 3: Ruflo in cloud (HTTP transport)

**Problem**: Ruflo requires a Node.js subprocess. Not available in Railway Docker.

**Solution**: Run Ruflo as a separate Railway service using HTTP+SSE transport

```python
# runtime/ruflo_bridge.py — add HTTP transport option
RUFLO_MCP_TRANSPORT = os.getenv("RUFLO_MCP_TRANSPORT", "stdio")  # "stdio" | "http"
RUFLO_MCP_URL       = os.getenv("RUFLO_MCP_URL", "")

class RufloBridge:
    def start(self):
        if RUFLO_MCP_TRANSPORT == "http" and RUFLO_MCP_URL:
            self._transport = HttpSseTransport(RUFLO_MCP_URL)
        else:
            self._transport = StdioTransport()   # existing
```

Add a second Railway service (Node.js):
```json
// ruflo-service/package.json
{
  "scripts": {
    "start": "npx @ruvnet/ruflo --transport http --port 3001"
  }
}
```

**Priority**: MEDIUM — pre-trade memory advisory improves win rate
**Effort**: 4-8 hours (new Railway service + bridge update)

---

## Phase A: Memory Completion (1-2 weeks)

### A1: Obsidian Retrieval API

**File to create**: `obsidian/retriever.py`

```python
class ObsidianRetriever:
    """Read from Obsidian index files for context injection."""

    def recent_trades(self, symbol=None, strategy=None,
                      outcome=None, limit=10) -> list[dict]:
        """Read from 05_Trading/_index.jsonl"""

    def strategy_history(self, strategy: str, last_n=20) -> list[dict]:
        """Read from 06_Strategies/weight_history.jsonl"""

    def daily_note(self, date: str) -> str:
        """Read from 20_Daily_Notes/YYYY-MM-DD.md"""

    def latest_analysis(self) -> dict:
        """Read latest data/optimization/analysis_*.json"""
```

### A2: Context injection into Claude Analyst

Add to `runtime/claude_analyst.py` before building the analysis prompt:

```python
from obsidian.retriever import ObsidianRetriever
retriever = ObsidianRetriever()

vault_context = f"""
## Recent trade lessons from vault (last 10):
{format_lessons(retriever.recent_trades(limit=10))}

## EMA_CROSS weight history (last 5 changes):
{format_weight_history(retriever.strategy_history('EMA_CROSS', last_n=5))}
"""
# Prepend vault_context to _ANALYSIS_PROMPT
```

**Expected impact**: Opus analysis gains institutional memory. Instead of analyzing each day independently, it can reference patterns across weeks/months.

### A3: Context injection into QUIN

Add to `runtime/quin_orchestrator.py` before building the decision prompt:

```python
from obsidian.retriever import ObsidianRetriever
retriever = ObsidianRetriever()

signal = ctx.execution_plan.get("signal", {})
if signal:
    similar = retriever.recent_trades(
        symbol=signal.get("symbol"),
        strategy=signal.get("strategy"),
        limit=5
    )
    history_ctx = "\n".join(
        f"- {t['date']}: {t['outcome']} PnL={t['pnl']:+.2f} regime={t['regime']}"
        for t in similar
    )
```

---

## Phase B: Claude Sonnet Routing Tier (1 week)

### B1: Add Sonnet to core/brain.py

```python
SONNET_MODEL = "claude-sonnet-4-6"

def ask_sonnet(prompt: str, system=None, history=None) -> str:
    """Mid-tier reasoning: regime synthesis, weight analysis, conflict resolution."""
    # Implementation mirrors ask_claude() but with SONNET_MODEL
    # Falls back to ask_llm() if API unavailable
```

### B2: Update complexity classifier

```python
def classify_depth(prompt: str) -> str:
    """Three-tier: simple / complex / deep"""
    if classify_complexity(prompt) == "simple":
        return "simple"

    _DEEP_KEYWORDS = {
        "why did", "explain the loss", "portfolio", "correlation",
        "regime shift", "multi-symbol", "drawdown analysis",
        "strategy conflict", "rebalance", "risk-adjusted"
    }
    if any(kw in prompt.lower() for kw in _DEEP_KEYWORDS):
        return "deep"   # → Sonnet

    return "complex"    # → Haiku (existing)
```

### B3: QUIN escalation to Sonnet

For high-stakes decisions (large position, capital state DEFENSIVE, multi-symbol conflict), QUIN should escalate from qwen2.5:14b to Claude Sonnet:

```python
# In QuinOrchestrator.decide():
if self._should_escalate(ctx):
    decision = self._ask_sonnet(ctx)  # NEW
    decision.source = "sonnet"
else:
    decision = self._ask_ollama(ctx)  # existing
```

Escalation triggers:
- Capital state is DEFENSIVE
- Confidence gap between top 2 signals < 0.05 (conflict)
- Proposed position size > $50 (large relative to balance)
- TREND_FOLLOW signal in ambiguous regime

---

## Phase C: Self-Healing Services (2-3 weeks)

### C1: Structured error alerting

```python
# runtime/alert_manager.py
class AlertManager:
    """Routes silent failures to Telegram."""

    ALERT_PATTERNS = [
        ("Obsidian write failed", "VAULT_WRITE_FAIL", AlertSeverity.MEDIUM),
        ("Ruflo bridge not running", "RUFLO_DOWN", AlertSeverity.LOW),
        ("Opus analysis failed", "ANALYSIS_FAIL", AlertSeverity.HIGH),
        ("Weight application skipped", "WEIGHT_SKIP", AlertSeverity.MEDIUM),
        ("QUIN timeout", "QUIN_TIMEOUT", AlertSeverity.LOW),
    ]

    def monitor_logs(self):
        """Tail server logs and alert on pattern matches."""
```

### C2: Circuit breaker for Crypto.com API

```python
# trading/exchange.py
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self._failures = 0
        self._state = "closed"  # closed = normal, open = blocking

    def call(self, fn, *args, **kwargs):
        if self._state == "open":
            raise CircuitOpenError("Exchange API circuit open")
        try:
            result = fn(*args, **kwargs)
            self._failures = 0
            return result
        except Exception:
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._state = "open"
            raise
```

### C3: Automatic restart on scan loop death

```python
# main.py
while True:
    time.sleep(60)
    if not bot.is_running():
        logger.warning("Bot stopped unexpectedly — restarting")
        try:
            bot.stop()
        except Exception:
            pass
        bot = CryptoComBot()  # fresh instance
        bot.start()
        alerts.send_telegram("Bot auto-restarted")  # notify Ronnie
```

This already exists in `main.py` (line 62-64) but only restarts the scan loop, not the daemons. Full restart should reinitialize all daemons too.

---

## Phase D: Distributed Worker Pools (1-2 months)

For when the bot scales beyond single-machine capacity:

### D1: Parallel symbol scanning

Current: BTC → ETH → SOL scanned sequentially each tick.
Proposed: Thread pool for parallel symbol scans.

```python
# trading/cryptocom_bot.py
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {
        executor.submit(self._scan_symbol, sym): sym
        for sym in SYMBOLS
    }
    results = {sym: f.result() for f, sym in futures.items()}
```

Expected speedup: ~3x for market data fetch (I/O bound).

### D2: Async FastAPI event handling

Replace the synchronous event bus with an async version using `asyncio.Queue`:

```python
# dashboard/api/event_bus.py
class AsyncEventBus:
    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    async def publish(self, event: dict):
        for q in self._queues:
            await q.put(event)
```

### D3: Remote Ollama pool

If local machine GPU becomes the bottleneck:

```python
OLLAMA_ENDPOINTS = [
    "http://localhost:11434",     # local
    "http://gpu-server-2:11434",  # remote (if available)
]

def ask_llm_pool(prompt, task):
    """Round-robin across Ollama endpoints."""
    ep = OLLAMA_ENDPOINTS[self._next_ep % len(OLLAMA_ENDPOINTS)]
    self._next_ep += 1
    return call_ollama(ep, prompt, task)
```

---

## 90-Day Milestone Plan

### Days 1-7: Critical fixes

| Task | Owner | Effort |
|------|-------|--------|
| Railway volume mount for data/ | Claude | 2h |
| JSONL rotation daemon | Claude | 3h |
| Structured error alerting | Claude | 4h |
| Verify Obsidian vault writes end-to-end | Claude | 1h |

### Days 8-21: Memory completion

| Task | Owner | Effort |
|------|-------|--------|
| obsidian/retriever.py | Claude | 4h |
| Context injection → Claude Analyst | Claude | 3h |
| Context injection → QUIN | Claude | 3h |
| Weekly consolidation notes | Claude | 4h |
| Test: vault → analyst → weights cycle | Claude | 2h |

### Days 22-35: Sonnet tier

| Task | Owner | Effort |
|------|-------|--------|
| ask_sonnet() in core/brain.py | Claude | 2h |
| Three-tier complexity classifier | Claude | 2h |
| QUIN escalation triggers | Claude | 4h |
| Cost monitoring for Sonnet tier | Claude | 1h |

### Days 36-60: Ruflo cloud + self-healing

| Task | Owner | Effort |
|------|-------|--------|
| Ruflo HTTP transport | Claude | 6h |
| Second Railway service (Ruflo Node.js) | Claude | 4h |
| Circuit breaker for exchange API | Claude | 3h |
| Auto-restart all daemons on failure | Claude | 2h |
| Chaos test all self-healing paths | Claude | 4h |

### Days 61-90: Scaling towards $50K

| Task | Owner | Effort |
|------|-------|--------|
| Parallel symbol scanning | Claude | 4h |
| More strategies (scalping, arbitrage) | Claude/Ronnie | ongoing |
| DCA strategy tuning (currently 0 trades) | Claude | 2h |
| RSI thresholds A/B test | Claude | 4h |
| Regime-aware position sizing | Claude | 6h |
| Live trading preparation (DEMO_MODE=false) | Ronnie approval | — |

---

## Scaling Path to $50,000

### Milestone strategy

| Balance | Target | Multiplier needed | Risk approach |
|---------|--------|------------------|---------------|
| $295 | $500 | 1.7× | Current SAFE, maintain |
| $500 | $1,000 | 2× | Introduce DCA fully |
| $1,000 | $2,500 | 2.5× | Add strategy from backtest |
| $2,500 | $5,000 | 2× | Sonnet-gated position sizing |
| $5,000 | $10,000 | 2× | Consider live trading |
| $10,000 | $25,000 | 2.5× | Expand to more symbols |
| $25,000 | $50,000 | 2× | Conservative capital preservation |

### What needs to be true at $10,000 before going live

1. All 8 critical fixes from this roadmap completed
2. Obsidian vault has 6+ months of trade history
3. QUIN has proven Sonnet escalation reducing false positives
4. Ruflo HNSW has 500+ trade embeddings with positive advisory record
5. Claude Opus analysis has applied successful weight adjustments ≥10 times
6. All strategies have ≥30 trades (statistical significance)
7. Survivability score STABLE (>80) maintained for 30+ consecutive days
8. Ronnie has explicitly reviewed and approved DEMO_MODE=false

### Capital at risk

**Current DEMO_MODE**: No real money at risk. All trades are simulated.
**CRITICAL**: NEVER set DEMO_MODE=false without Ronnie's explicit approval.
**CRITICAL**: NEVER push to main branch.

---

## Autonomous Reflection Design

Long-term vision: the bot should improve its own trading strategy through structured reflection cycles, not just nightly analysis.

```
Daily (midnight):
  Claude Opus analyzes yesterday's trades
  → Adjusts weights (already working)
  → Blocks strategies in proven-bad regimes (working)

Weekly (Sunday midnight):
  Claude Sonnet reads the week's Opus reports
  → Identifies meta-patterns across daily analyses
  → Writes weekly strategy thesis to Obsidian 18_Roadmaps/
  → Proposes thresholds changes (requires Ronnie approval via Telegram)

Monthly (1st midnight):
  Claude Opus reads all weekly theses
  → Generates monthly performance postmortem
  → Identifies systematic improvements
  → Files formal proposal to governance approval queue
```

This creates a three-tier reflection loop:
1. **Daily** (tactical): weight adjustments, strategy blocks
2. **Weekly** (operational): pattern recognition across days
3. **Monthly** (strategic): systematic improvements, threshold changes

The bot evolves on a 24-hour cycle currently. Adding weekly and monthly cycles turns it into a genuinely adaptive system.
