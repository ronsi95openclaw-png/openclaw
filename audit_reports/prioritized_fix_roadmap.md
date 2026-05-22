# OpenClaw — Prioritized Fix Roadmap
**Status key:** ✅ Done | 🔴 Critical | 🟠 High | 🟡 Medium | ⚪ Low

---

## WAVE 1 — Capital-Safety (COMPLETE)

| # | Issue | File | Status |
|---|-------|------|--------|
| 1 | Governance halt check fail-open | orchestrator.py:329 | ✅ |
| 2 | Unhedged position tracked in state | cryptocom_bot.py:583 | ✅ |
| 3 | DCA state mutation outside lock | cryptocom_bot.py:708 | ✅ |
| 4 | Trade ID collision (same second) | cryptocom_bot.py:557 | ✅ |
| 5 | Exchange nonce collision | exchange.py:40 | ✅ |
| 6 | MACD array inverted | strategies.py:137 | ✅ |
| 7 | Dashboard endpoints unauthenticated | server.py:all | ✅ |
| 8 | BotConfig no risk_pct bounds | server.py:177 | ✅ |
| 9 | EventBus QueueFull silent drop | event_bus.py:61 | ✅ |
| 10 | EventBus TOCTOU loop-None deref | event_bus.py:53 | ✅ |
| 11 | Strategy weights legacy int schema | strategy_weights.py:237 | ✅ |
| 12 | Unknown regime labels fail-open | strategy_compatibility.py:44 | ✅ |
| 13 | recent_outcomes off-by-one (21) | strategies.py:444 | ✅ |
| 14 | Auth default "changeme" token | auth.py:13 | ✅ |
| 15 | Telegram allowlist fail-open | auth.py:39 | ✅ |

---

## WAVE 2 — Stability & State (TODO — pre-live-deployment)

| # | Issue | File | Priority |
|---|-------|------|----------|
| 16 | Capital engine state not persisted across restarts | capital_preservation.py | 🔴 |
| 17 | `_alltime_peak` seeded via private field (not thread-safe at init) | capital_preservation.py:231 | 🔴 |
| 18 | Executor: SL fail should cancel entry and skip TP | executor.py:75 | 🔴 |
| 19 | Executor: entry order_id not validated | executor.py:69 | 🔴 |
| 20 | Exchange: `data[0]` index without length check | exchange.py:121,164 | 🟠 |
| 21 | Exchange: `or` operator masks zero-valued fields | exchange.py:105 | 🟠 |
| 22 | Exchange: quantity precision instrument-specific | exchange.py:297 | 🟠 |
| 23 | Position schema validation too lenient (no type/range check) | cryptocom_bot.py:95 | 🟠 |
| 24 | Silent size-zero rejection (sl_usd==0 no log) | cryptocom_bot.py:432 | 🟠 |
| 25 | Auto-disable strategy no Telegram/Sheets alert | cryptocom_bot.py:163 | 🟡 |
| 26 | Demo balance grows unbounded (no daily reset) | cryptocom_bot.py:520 | 🟡 |
| 27 | Date boundary flush race (two threads) | cryptocom_bot.py:283 | 🟡 |
| 28 | RSI jitter — no hysteresis on 55/50 threshold | strategies.py:165 | 🟡 |
| 29 | ATR zero → constant 1% SL regardless of volatility | strategies.py:106 | 🟡 |
| 30 | Bollinger Band requires 25 close minimum (silent) | strategies.py:118 | 🟡 |

---

## WAVE 3 — Governance & Security (TODO — before any live keys)

| # | Issue | File | Priority |
|---|-------|------|----------|
| 31 | secrets.py XOR encryption trivially breakable | secrets.py:48 | 🔴 |
| 32 | Emergency halt maker/checker bypass (concurrent halt) | emergency_controls.py:180 | 🟠 |
| 33 | Approvals log concurrent-write corruption | approvals.py:213 | 🟠 |
| 34 | governance._is_globally_halted fail-open | operator_controls.py:329 | ✅ fixed in orchestrator |
| 35 | permissions.py base64 "encryption" | permissions.py:126 | 🟠 |
| 36 | Orchestrator direct `_drawdown_tracker` private access | orchestrator.py:252 | 🟡 |
| 37 | review_queue TOCTOU in promote() | review_queue.py:128 | 🟡 |
| 38 | review_queue mutable dict return | review_queue.py:165 | 🟡 |
| 39 | Replay journal rotation not atomic | replay_journal.py:177 | 🟡 |

---

## WAVE 4 — AI System Safety (TODO)

| # | Issue | File | Priority |
|---|-------|------|----------|
| 40 | Ruflo RPC response mismatch (concurrent calls) | ruflo_bridge.py:309 | 🟠 |
| 41 | Ruflo memory key collision (same-second trades) | ruflo_agent.py:165 | 🟠 |
| 42 | Claude analyst trade data prompt injection | claude_analyst.py:183 | 🟡 |
| 43 | Qwen compressor empty lesson not flagged | qwen_compressor.py:65 | ⚪ |
| 44 | Ruflo confidence adjustment unbounded | orchestrator.py:190 | 🟡 |
| 45 | claude_analyst hardcoded model ID | claude_analyst.py:35 | ⚪ |

---

## WAVE 5 — Observability & Monitoring (TODO)

| # | Issue | File | Priority |
|---|-------|------|----------|
| 46 | Google Sheets "reconnecting" doesn't reconnect | google_sheets.py:311 | 🟠 |
| 47 | Google Sheets queue overflow no metric | google_sheets.py:294 | 🟡 |
| 48 | Regime label not validated in replay journal | replay_journal.py:67 | 🟡 |
| 49 | Soak test growth rate not tracked | soak_tests.py | 🟡 (partial) |
| 50 | Validation chaos tests missing 429/403 scenarios | chaos_tests.py | 🟡 |
| 51 | WebSocket max connections not limited | server.py | 🟡 |
| 52 | CORS origins hardcoded to localhost | server.py:48 | ⚪ |

---

## Readiness Gates

| Gate | Criteria | Status |
|------|----------|--------|
| Extended Paper Trading | No CRITICAL open | ✅ PASS |
| Limited Live (small size) | Waves 1+2 complete + secrets.py replaced | ❌ Wave 2 pending |
| Full Production | All waves complete + exchange reconciliation + monitoring | ❌ Far |
