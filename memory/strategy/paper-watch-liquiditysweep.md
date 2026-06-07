---
title: LiquiditySweep Paper-Watch — Started 2026-05-31
strategy: LiquiditySweep
mode: paper-watch (signal-logging only, NO wiring)
start_date: 2026-05-31
day7_check: 2026-06-07
day14_decision: 2026-06-14
cadence: daily (matches 1d candle refresh)
backtest_baseline: 1/4 positive quarters, +$0.777 PnL across 4 symbols × 1d candles × 4 quarters
---

# LiquiditySweep Paper-Watch

## Purpose
Observe LiquiditySweep's HIGH-confidence signals against live Crypto.com 1d data
for ~14 days **without** wiring it into the executor. Goal: see whether live
signal cadence + direction matches the backtest, or diverges.

## What we are NOT doing
- NOT wiring `LiquiditySweepStrategy` into `trading/executor.py`
- NOT trading any signals it generates
- NOT adding capital
- NOT switching off DEMO mode

## What we ARE doing
- Daily run of `infra/paper_watch_liquiditysweep.py` via Windows scheduled task `ClawBot-LiquiditySweep-Watch`
- Appends one JSONL line per (symbol, run) to `data/paper_watch/liquidity_sweep.jsonl`
- Each line captures: timestamp, symbol, last close, number of warmup candles, full Signal dict (action/confidence/rsi/reason)

## Success criteria for considering wiring (after 14 days)
- Live HIGH-confidence signal count for LiquiditySweep within roughly ±30% of backtest's expected cadence (~17 trades/year across 4 symbols ≈ 1 every 3 weeks → so 0–1 over 14 days; if we see 5+ that's a divergence worth understanding)
- No catastrophic regime shift visible in raw signal pattern
- Crypto.com public endpoint stays reachable (zero `fetch failed` entries)

## Review schedule
- **2026-06-07 (Day 7)** — mid-point check: count signals, glance at error rate, classify market regime (trending vs ranging by EMA slope of BTC closes), document in CHANGES.md
- **2026-06-14 (Day 14)** — full review against criteria above, decision on wiring (Category B proposal if positive)

## Decision tree at Day 14
- Criteria met → propose `LiquiditySweepStrategy` wire-in as Category B in next session
- Criteria partially met → extend paper-watch 14 more days
- Criteria failed → strategy retired; revisit ensemble approach (Phase 5D option 2 from `next_session.md`)

## Operational notes
- **Cadence:** daily (start time configured at scheduled-task install, see CHANGES.md). 1d candles only refresh once per day; running more often is wasted API calls.
- **Disable:** `schtasks /change /tn "ClawBot-LiquiditySweep-Watch" /disable` (re-enable with `/enable`); permanent removal `schtasks /delete /tn "ClawBot-LiquiditySweep-Watch" /f`.
- **Manual run:** `python -m infra.paper_watch_liquiditysweep` from the Claude-openclaw root.
- **Output location:** `data/paper_watch/liquidity_sweep.jsonl` (data/ is gitignored — JSONL is local-only).

## Cross-references
- [[backtest-2026-05-30]] — the comparison that produced the deferred-wiring decision
- [[DECISIONS]] — see entry "Defer strategy-wiring; paper-watch LiquiditySweep"
