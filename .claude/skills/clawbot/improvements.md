# ClawBot — Improvements Reference

Planned upgrades, implementation-ready. All respect DEMO_MODE=true.

---

## Improvement 1 — Dynamic scan interval (runtime/scan_interval_engine.py)

**Status**: File exists. Wire it into the main scan loop.

**Goal**: Shorten scan interval when volatility is high, lengthen when quiet.

**Integration point** in `trading/cryptocom_bot.py`, `_scan_loop()`:

```python
from runtime.scan_interval_engine import ScanIntervalEngine
_scan_engine = ScanIntervalEngine()

# In scan loop body, after each scan:
new_interval = _scan_engine.get_interval(atr_ratio=current_atr_ratio)
self.state.scan_interval = new_interval
```

**ScanIntervalEngine contract**:
- Returns int seconds (30–120)
- High ATR (>0.015) → 30s
- Normal ATR (0.005–0.015) → 60s
- Low ATR (<0.005) → 120s

---

## Improvement 2 — Ruflo pre-trade memory advisory (runtime/ruflo_agent.py)

**Status**: File exists. Not yet called before trades.

**Goal**: Before placing a trade, query HNSW memory for similar past trades. If top-3 similar trades have negative expectancy, reduce `size_pct`.

**Integration point** in `runtime/intent_pipeline.py`, after Gate 4 (regime):

```python
from runtime.ruflo_agent import get_ruflo
ruflo = get_ruflo()
if ruflo and ruflo.is_ready():
    advice = ruflo.advise(
        symbol=intent["symbol"],
        strategy=intent["strategy"],
        regime=intent["regime_label"],
        confidence=intent["confidence"],
    )
    if advice.get("reduce_size"):
        intent["size_pct"] *= advice["size_scalar"]  # e.g. 0.5
        intent["ruflo_note"] = advice.get("reason", "")
```

---

## Improvement 3 — Claude Analyst nightly report (runtime/claude_analyst.py)

**Status**: File exists. Not scheduled.

**Goal**: Every night at 23:30 UTC, Claude Opus reads `data/logs/trade_outcomes.jsonl` and produces an `AnalysisReport` with weight adjustments.

**Scheduling** in `trading/cryptocom_bot.py`:

```python
from runtime.claude_analyst import get_analyst_daemon
self._analyst_daemon = get_analyst_daemon(self)
# In start():
self._analyst_daemon.start()
```

**AnalysisReport schema** (written to `data/optimization/analysis_YYYY-MM-DD.json`):

```json
{
  "date": "2026-05-27",
  "overall_health": "GOOD",
  "win_rate_pct": 58.3,
  "expectancy_usd": 2.14,
  "top_failure_pattern": "TREND_FOLLOW in RANGING regime",
  "top_win_pattern": "BOLLINGER_BAND on BTC at volatility spike",
  "immediate_action": "Reduce TREND_FOLLOW weight to 0.5",
  "weight_adjustments": {"TREND_FOLLOW": 0.5, "BOLLINGER_BAND": 1.2},
  "ruflo_directive": "Avoid SOL longs when ATR > 0.02",
  "model": "claude-opus-4-7"
}
```

Weight adjustments are auto-applied at midnight by `runtime/weight_scheduler.py`.

---

## Improvement 4 — Qwen per-trade lesson compressor (runtime/qwen_compressor.py)

**Status**: File exists. Not called on trade close.

**Goal**: After each trade closes, Qwen 2.5:14b generates a 1-sentence lesson stored in Ruflo memory.

**Integration point** in `trading/executor.py`, `_close_position()`:

```python
from runtime.qwen_compressor import compress_lesson
lesson = compress_lesson(trade_record)  # non-blocking, background thread
if lesson:
    trade_record["lesson"] = lesson
    get_ruflo().store(trade_record)
```

**compress_lesson contract**:
- Calls Ollama: `POST http://localhost:11434/api/generate`
- Model: `qwen2.5:14b`
- Falls back to rule-based lesson if Ollama unreachable
- Returns string ≤ 120 chars

---

## Improvement 5 — /analyze Telegram command

**Goal**: On demand, trigger a Claude Analyst report immediately (not wait for 23:30).

**Add to** `runtime/telegram_bot.py`:

```python
def _cmd_analyze(chat_id, _text, bot_ref) -> None:
    _reply(chat_id, "🔍 <b>Running analysis...</b> (this takes ~30s)")
    try:
        from runtime.claude_analyst import run_analysis_now
        report = run_analysis_now()
        lines = [
            f"📊 <b>Claude Analysis — {report['date']}</b>",
            f"Health: {report['overall_health']}",
            f"Win rate: {report['win_rate_pct']:.1f}%",
            f"Expectancy: ${report['expectancy_usd']:.2f}",
            f"\n🔴 <b>Failure pattern</b>: {report['top_failure_pattern']}",
            f"🟢 <b>Win pattern</b>: {report['top_win_pattern']}",
            f"\n⚡ <b>Action</b>: {report['immediate_action']}",
        ]
        _reply(chat_id, "\n".join(lines))
    except Exception as exc:
        _reply(chat_id, f"❌ Analysis failed: {exc}")
```

Register in `_COMMANDS` dict: `"/analyze": _cmd_analyze`

---

## Improvement 6 — Portfolio risk aggregation (risk/portfolio_risk.py)

**Status**: File exists. Not surfaced in `/status`.

**Goal**: Show total notional exposure across all open positions in `/status` and `/trades`.

**Add to** `_cmd_status` in `runtime/telegram_bot.py`:

```python
from risk.portfolio_risk import PortfolioRisk
risk = PortfolioRisk()
exposure = risk.total_exposure(state.open_positions)
lines.append(f"Exposure: ${exposure['notional_usd']:.2f} ({exposure['pct_of_balance']:.1f}% of balance)")
```

---

## Improvement 7 — Live balance feed integration (runtime/balance_feed.py)

**Status**: File exists and runs. In demo mode, feeds advisory warnings only.

**Goal**: When `DEMO_MODE=False` (live), the balance feed should auto-update `BotState.balance` from the exchange every 60s.

**Already wired**: `balance_feed.py` → `live_balance_guardian.py` cross-validates exchange vs internal.

**When going live**: Set `DEMO_MODE=false` in `.env` (requires explicit user instruction). The guardian will then emit `CRITICAL` alerts if exchange balance deviates >5% from internal.

---

## Improvement 8 — Dashboard real-time PnL chart (dashboard/web/)

**Goal**: Add a live sparkline chart to the React dashboard showing unrealised PnL across open positions.

**WebSocket endpoint** already available: `ws://localhost:8000/ws`

**Data shape pushed every 5s**:

```json
{
  "type": "pnl_update",
  "open_positions": [
    {"symbol": "BTC_USDT", "side": "long", "unrealised_pnl": 4.23, "entry": 76512},
    {"symbol": "ETH_USDT", "side": "short", "unrealised_pnl": -1.10, "entry": 2814}
  ],
  "total_unrealised": 3.13
}
```

**Implementation**: Add `recharts` SparklineChart in `dashboard/web/components/PnlChart.tsx`, subscribe to `/ws`, filter for `type === "pnl_update"`.

---

## Weight scheduler (already live)

`runtime/weight_scheduler.py` — `WeightApplicationDaemon` fires at 00:05 UTC.

Reads `data/optimization/analysis_*.json` (latest file), applies `weight_adjustments` to `data/strategy_weights.json`. Floor: 0.1. Ceiling: 2.0.

Logs: `WeightApplicationDaemon: applied 3 adjustments from analysis_2026-05-27.json`

---

## Auto-disable weak strategies (already live)

In `trading/cryptocom_bot.py`, `_auto_disable_weak_strategies()`:

```python
# Disable if weight < 0.3 AND ≥ 20 trades
for strategy, stats in weights.items():
    if stats["weight"] < 0.3 and stats.get("trades", 0) >= 20:
        stats["weight"] = 0.0
        logger.warning("Auto-disabled %s (weight=%.2f, trades=%d)",
                       strategy, stats["weight"], stats["trades"])
```

Called after every midnight weight application.
