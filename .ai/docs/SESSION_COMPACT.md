# SESSION_COMPACT — AUTO-GENERATED
**Last updated:** 2026-05-30 00:07 UTC (by scripts/generate_session_compact.py)
**DO NOT edit manually — regenerated on session end.**

## SYSTEM STATE
| Key | Value |
|-----|-------|
| Balance | $295.30 |
| Total PnL | $+197.30 |
| Capital state | SAFE |
| All-time peak | $295.30 |
| Demo mode | True |
| Branch | `claude/blofin-trading-bot-dashboard-TUJBC` |
| Trades (total) | 10 |
| Win rate | 30% |
| Milestones hit | 0 / 0 |
| Next target | ALL HIT |

## RECENT COMMITS
```
442d06a chore: regenerate SESSION_COMPACT.md post-cleanup
d00428f chore: remove shims + archive strategy v1 (T1.4, T1.5)
ae91db5 feat: auto-generate SESSION_COMPACT.md from live state (T2.3)
82e6e05 docs: add deployment ACTIVE/ASPIRATIONAL labels + env example vars
61bcadf fix: self-contained fcntl shim in guardian + reconciliation; silence demo noise
```

## STRATEGY WEIGHTS
| Strategy | Weight | Win Rate | Trades | Status |
|----------|--------|----------|--------|--------|
| BOLLINGER_BAND | 1.00× | 0% | 0 | OK |
| BREAKOUT | 1.00× | 0% | 0 | OK |
| EMA_CROSS | 0.80× | 0% | 25 | OK |
| RSI_MEAN_REVERT | 1.00× | 0% | 0 | OK |
| TREND_FOLLOW | 1.00× | 0% | 0 | OK |
| VWAP | 1.00× | 0% | 0 | OK |

## ACTIVE POSITIONS
| Symbol | Side | Entry | Size |
|--------|------|-------|------|
| BTC_USDT | short | $106,404.37 | 0.001445 |
| SOL_USDT | long | $169.31 | 0.754645 |

## KEY FILES QUICK REFERENCE
| File | Purpose |
|------|---------|
| `trading/cryptocom_bot.py` | Main bot, 60s scan loop |
| `trading/strategies.py` | 6 active strategies + weight engine |
| `runtime/intent_pipeline.py` | 5-gate safety filter (never bypass) |
| `risk/capital_preservation.py` | SAFE/DEFENSIVE/CRITICAL/HALT state machine |
| `runtime/telegram_bot.py` | 14 Telegram commands |
| `dashboard/api/server.py` | FastAPI + WebSocket (port 8000) |
| `data/cryptocom_state.json` | Live bot state |
| `data/logs/trade_outcomes.jsonl` | Closed trades (Claude Analyst input) |

## HARD RULES
- NEVER commit `.env`, `credentials.json`, `setup.sh`
- NEVER set `DEMO_MODE=false` without explicit user instruction
- NEVER bypass IntentPipeline gate
- NEVER push to `main` branch
- Always develop on branch: `claude/blofin-trading-bot-dashboard-TUJBC`
