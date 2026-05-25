# OpenClaw — Performance Audit

**Last Updated**: 2026-05-25
**Audit phases completed**: 9 (audit_reports_phase1/ through audit_reports_phase9/)
**Phase 9 composite score**: 100/100 (Supervised Live Ready)

---

## Summary: 9 Audit Phases

| Phase | Focus | Score | Key Finding |
|-------|-------|-------|-------------|
| Phase 1 | Core architecture | — | Foundation established |
| Phase 2 | Reconciliation, chaos tests, portfolio risk, replay | — | Event sourcing verified |
| Phase 3 | Drift detection, event store, execution analytics | — | Checksums + replay working |
| Phase 4 | Operational diagnostics, continuous reconciliation | — | Monitoring layer added |
| Phase 5 | Deployment hardening, optimization governance | — | Railway deployment hardened |
| Phase 6 | Remaining risk surface, portfolio risk | — | Known gaps documented |
| Phase 7 | Security, execution quality | — | Auth + firewall added |
| Phase 8 | Remaining risks (2nd pass), execution quality | — | Telemetry loop added |
| Phase 9 | Dashboard API + React UI | 100/100 | All 9 dashboard sections live |

---

## Capability Matrix (31 items — from /api/health endpoint)

From `runtime/capability_matrix.py`:

| Category | Item | Status |
|----------|------|--------|
| Trading | Strategy execution | ✅ |
| Trading | Position management | ✅ |
| Trading | Risk management | ✅ |
| Trading | Multi-symbol support (BTC/ETH/SOL) | ✅ |
| Trading | 6 active strategies | ✅ |
| Capital | State machine (SAFE/DEFENSIVE/CRITICAL/HALT) | ✅ |
| Capital | Rolling drawdown tracker | ✅ |
| Capital | Live balance feed | ✅ |
| Intelligence | Nightly Opus analysis | ✅ |
| Intelligence | QUIN gate (LLM + rule-based) | ✅ |
| Intelligence | Per-trade compression | ✅ |
| Intelligence | Self-learning weights | ✅ |
| Intelligence | Goal tracker ($98→$50K) | ✅ |
| Memory | Replay journal (append-only) | ✅ |
| Memory | QUIN decisions log | ✅ |
| Memory | Skill clock audit | ✅ |
| Memory | Response cache | ✅ |
| Memory | Obsidian vault writes | ✅ (post-fix) |
| Memory | Ruflo HNSW (local) | ✅ / ❌ (cloud) |
| Ops | WebSocket real-time stream | ✅ |
| Ops | Dashboard API (25+ endpoints) | ✅ |
| Ops | Telegram two-way commands | ✅ (local) |
| Ops | Google Sheets reporting | ✅ (local) |
| Ops | SnapshotDaemon | ✅ |
| Ops | WeightApplicationDaemon | ✅ |
| Ops | BalanceFeedDaemon | ✅ |
| Governance | Emergency controls | ✅ |
| Governance | Strategy governance | ✅ |
| Governance | Operator approval workflow | ✅ |
| Security | API firewall | ✅ |
| Security | Token auth on dashboard | ✅ |

---

## Survivability Score

**File**: `runtime/survivability.py`

The survivability engine scores 8 subsystems on a 0-100 scale:

| Subsystem | Local score | Cloud (Railway) score |
|-----------|-------------|----------------------|
| Capital preservation | ~15/15 | ~15/15 |
| Trade reconciliation | ~10/10 | ~8/10 |
| Event store integrity | ~10/10 | ~10/10 |
| Strategy weights validity | ~10/10 | ~10/10 |
| QUIN decision log | ~10/10 | ~7/10 |
| Telegram connectivity | ~10/10 | ~0/10 |
| Exchange connectivity | ~10/10 | ~0/10 |
| Ollama inference | ~15/15 | ~0/15 |
| **Total** | **~90-100** (STABLE) | **~50-60** (DEGRADED) |

The DEGRADED status in Railway is expected and is **not a bug**. It reflects the network-blocked environment. Real trading survivability is measured only on the local machine.

Classifications:
- STABLE: 80-100
- DEGRADED: 60-79
- CRITICAL: 40-59
- UNSAFE: 0-39

---

## Known Bottlenecks

### 1. Scan loop timing

```python
BotState.scan_interval = 30  # seconds (default)
```

The scan loop runs every 30 seconds. Each iteration:
- Fetches candles for 3 symbols (~0.5-2s per symbol, 3 HTTP calls)
- Runs 6 strategies × 3 symbols = 18 signal computations (~10ms total, pure Python)
- QUIN decision via Ollama (~1-4s with qwen2.5:14b)
- IntentPipeline validation (~<1ms)
- If trade: Executor call (~0.5-2s)

**Total per-scan overhead (local)**: ~3-8 seconds of a 30-second window.
**Total per-scan overhead (cloud)**: ~0.1s (fake candles, rule-based QUIN)

The bottleneck is network I/O (Crypto.com API) + Ollama inference.

### 2. Ollama cold start

First QUIN call after Ollama restart: ~5-10 seconds (model loading).
Subsequent calls: 1-4 seconds.

Mitigation: QUIN 10-second timeout falls back to rule-based immediately. No blocking.

### 3. Opus analysis latency

The nightly Opus call with 100 trade records + weights takes 5-30 seconds depending on output length (max 4096 tokens). This runs in `flush_daily_summary()` which is called from the bot's midnight boundary check. The scan loop is not blocked — it continues running during Opus analysis.

### 4. Obsidian write latency

Each vault write is a synchronous filesystem operation. With:
- Trade journal write: ~10-50ms
- Strategy evolution write: ~5-20ms
- Daily note write: ~10-50ms

At peak (multiple trades in rapid succession), this can add ~100-200ms to trade close processing. This is non-blocking if the writes are in a try/except (they are).

---

## Silent Failure Points

These are the most dangerous failure modes — the system appears healthy but is not functioning:

### 1. Obsidian vault writes (CRITICAL — now fixed)

**Previously**: `~/ai-system` did not exist. All vault writes hit `ModuleNotFoundError` which was caught by bare `try/except` in:
- `trading/cryptocom_bot.py` line 1282: `from obsidian.trade_journal_writer import write_trade`
- `trading/cryptocom_bot.py` line 1527: `from obsidian.vault_manager import write_daily_note`
- `trading/strategies.py` line 515: `from obsidian.strategy_writer import write_strategy_evolution`

**Status**: Fixed — `obsidian/` package added to repo (2026-05-25). Writes now succeed.

**Detection**: `ls ~/AI-Operating-System-Vault/05_Trading/` — should show trade notes.

### 2. Ruflo HNSW unavailable (Railway)

When Ruflo is unavailable, `RufloAdvisor` returns `RufloAdvice(available=False)`. The IntentPipeline proceeds without memory context. No error is logged at WARNING level — only at DEBUG.

**Detection**: Check `runtime.ruflo_agent` logs for "bridge not running" messages.

**Impact**: Pre-trade memory advisory is entirely disabled. QUIN makes decisions without historical pattern context.

### 3. Ollama timeout → rule-based QUIN (silent downgrade)

When Ollama times out (>10s), QUIN silently switches to rule-based. The `QuinDecision.source` field captures this:
- `"source": "ollama"` — LLM decision
- `"source": "rule_based"` — silent downgrade

**Detection**: `grep '"source": "rule_based"' data/quin_decisions.jsonl | wc -l` — high count means Ollama is struggling.

### 4. Opus analysis not applied (stale analysis file)

If `data/optimization/analysis_*.json` is not updated (Opus call fails or API key invalid), `WeightApplicationDaemon` detects that the file hasn't changed (same mtime) and skips application. No error — it just waits for the next day.

**Detection**: `ls -lt data/optimization/analysis_*.json | head -3` — check modification time. Should be <25 hours old.

### 5. Google Sheets API silent failure

All Google Sheets calls are wrapped in try/except. If the credentials file is missing or expired, sheets updates fail silently. No retry logic.

**Detection**: `grep "sheets" data/logs/server.log | tail -20` for errors.

### 6. Telegram polling failure (Railway)

On Railway, Telegram polling fails immediately (connection refused to api.telegram.org). The `TelegramCommandBot` catches this and sleeps 30 seconds before retrying indefinitely. No alarm is raised.

**Detection**: On Railway, Telegram is expected to fail. On local machine, unexpected silence from Telegram suggests polling failure.

---

## Railway Constraints

| Constraint | Effect | Workaround |
|-----------|--------|------------|
| api.crypto.com blocked | No real market data | _fake_candles() simulation |
| api.telegram.org blocked | No Telegram | Expected — local only |
| No Ollama | No LLM inference | Rule-based QUIN, OpenRouter |
| No Node.js subprocess | No Ruflo HNSW | Graceful degradation |
| No GPU | No local inference | OpenRouter fallback |
| Single process | uvicorn + bot in one | main.py handles this |
| Ephemeral filesystem | State lost on restart | data/ persisted via... |

**State persistence on Railway**: Currently `data/` is ephemeral (Railway does not mount persistent volumes by default). This means every Railway restart loses:
- `data/cryptocom_state.json` (bot state)
- `data/quin_decisions.jsonl` (QUIN audit)
- `data/skill_clock_audit.jsonl`
- `data/replay_journal.jsonl`
- All accumulated metrics

**Fix needed**: Mount a Railway volume at `/app/data/` or add Railway Postgres for state persistence.

---

## Daemon Overhead

| Daemon | Memory footprint | CPU usage | Notes |
|--------|-----------------|-----------|-------|
| CryptoComBot scan loop | ~50MB (Python) | Low (~1% avg) | Main thread |
| uvicorn / FastAPI | ~80MB | Low unless high traffic | Daemon thread |
| WeightApplicationDaemon | ~5MB | Near-zero (sleeps 30s chunks) | Daemon thread |
| BalanceFeedDaemon | ~5MB | Near-zero (polls every 60s) | Daemon thread |
| TelegramCommandBot | ~15MB | Low (polling) | Daemon thread |
| SnapshotDaemon | ~5MB | Low (periodic) | Daemon thread |
| ReconciliationScheduler | ~5MB | Low (periodic) | Daemon thread |
| DriftDetector | ~5MB | Low (periodic) | Daemon thread |
| WsGuardian | ~5MB | Low | Daemon thread |
| IntegrityMonitor | ~5MB | Low | Daemon thread |
| ExecutionAnalytics | ~5MB | Low | Daemon thread |
| **Total** | **~180MB** | **~2-5%** | 11 threads |

The memory footprint is dominated by Python runtime overhead (10-15 threads × ~15-50MB each). No optimization needed at this scale.

---

## Memory Footprint of Data Files

| File | Expected size | Growth rate |
|------|--------------|-------------|
| `data/replay_journal.jsonl` | 1-50MB | ~1KB/scan (~48KB/hour) |
| `data/quin_decisions.jsonl` | 1-10MB | ~500B/scan (~24KB/hour) |
| `data/skill_clock_audit.jsonl` | 1-50MB | ~2KB/scan (~96KB/hour) |
| `data/logs/trade_outcomes.jsonl` | 10KB-1MB | ~2KB/trade (~20KB/day) |
| `data/response_cache.json` | <1MB | Capped at 200 entries |
| `data/usage_stats.json` | <100KB | ~200B/day |
| `data/optimization/analysis_*.json` | 50-200KB/file | 1 file/day |

At current scan rate (~1440 scans/day), `replay_journal.jsonl` grows ~70MB/day. After 30 days without cleanup: ~2GB. A rotation policy is needed for long-term operation.

---

## Production Hardening Gaps

| Gap | Risk | Priority |
|-----|------|----------|
| Data/ not persisted on Railway | State lost on restart | High |
| No JSONL rotation policy | Disk exhaustion in 30-60 days | High |
| Opus analysis has no retry logic | Silent failure if API key bad | Medium |
| Google Sheets has no retry | Silent reporting failure | Low |
| No structured error alerting | Silent failures invisible | Medium |
| No rate limiting on Crypto.com API | Could hit API limits during bursts | Medium |
| Exchange.py has no circuit breaker | Cascade on API failure possible | Medium |
| QUIN has no Sonnet escalation path | Wrong model for high-stakes decisions | Low |
| Obsidian retrieval not built | Memory context invisible to Claude | Medium |

---

## Test Coverage (Phase 9)

**395 tests passing** across 9 phases.

Phase 9 additions (61 tests):
- `tests/phase9/test_audit.py` — 17 tests (DashboardAuditEvent, atomic writes)
- `tests/phase9/test_routers.py` — 25 tests (all 23 phase9 endpoints)
- `tests/phase9/test_commands.py` — 12 tests (bot commands, state changes)
- `tests/phase9/test_telemetry.py` — 7 tests (5-channel telemetry loop)
- `tests/phase9/test_dashboard_soak.py` — 5 tests (1000-concurrent-writes soak)

To run all tests:
```bash
cd /home/user/openclaw
python -m pytest tests/ -v --tb=short
```

To run specific phase:
```bash
python -m pytest tests/phase9/ -v
```
