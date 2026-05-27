# ClawBot — Prompt Patterns & Session Checklist

---

## Session startup checklist

Run these at the start of every session before touching any code:

```bash
cd /home/user/openclaw

# 1. Syntax check critical files
python -m py_compile trading/cryptocom_bot.py runtime/telegram_bot.py trading/strategies.py

# 2. Check strategy weights — flag anything below 0.3
cat data/strategy_weights.json | python3 -m json.tool

# 3. Confirm capital state is SAFE
cat data/capital_state.json

# 4. Confirm current balance
python3 -c "
import json
s = json.load(open('data/cryptocom_state.json'))
bal = s.get('balance') or (s.get('starting_balance',98) + s.get('total_pnl',0))
print(f'Balance: \${bal:.2f}  PnL: \${s.get(\"total_pnl\",0):.2f}')
"

# 5. Count closed trades and win rate
python3 -c "
import json
trades = [json.loads(l) for l in open('data/logs/trade_outcomes.jsonl') if l.strip()]
wins = sum(1 for t in trades if t.get('outcome')=='win')
print(f'Trades: {len(trades)}/30  WR: {wins/len(trades)*100:.0f}%' if trades else 'No trades')
"

# 6. Full import chain test
python -c "
mods=['settings','infra.state_store','risk.capital_preservation',
      'trading.cryptocom_bot','runtime.telegram_bot','runtime.telegram_relay',
      'runtime.morning_briefing','runtime.live_mode_gate','dashboard.api.server']
[(__import__(m), print('OK', m)) for m in mods]
"
```

---

## Bug fix prompt template

Use this when reporting a bug to start a new session:

```
I have a bug in OpenClaw (crypto trading bot, ~/openclaw).

Symptom: [what you see — include exact log line or error message]

When it happens: [scan loop / trade close / startup / Telegram command / etc.]

Files likely involved: [list suspected files]

What I've already tried: [any attempted fixes]

Hard constraints:
- DEMO_MODE must stay true
- Never bypass IntentPipeline
- Develop on: claude/blofin-trading-bot-dashboard-TUJBC
- Push after fix
```

---

## Feature request prompt template

```
Add a new feature to OpenClaw (crypto trading bot, ~/openclaw).

Feature: [name and 1-sentence description]

Why: [what problem it solves / what metric it improves]

Integration point: [which file and function should call it]

Output: [what the user/bot should see when it works]

Constraints:
- DEMO_MODE must stay true
- Must not bypass IntentPipeline or CapitalPreservationEngine
- Non-blocking (daemon thread or fire-and-forget if touching external APIs)
- Develop on: claude/blofin-trading-bot-dashboard-TUJBC
- Push after complete
```

---

## Full audit prompt template

```
Run a full audit of OpenClaw (crypto trading bot, ~/openclaw).

Audit scope:
1. Import chain — all 9 core modules import cleanly
2. State files — balance, capital state, strategy weights are consistent
3. Bot state — balance = starting_balance + total_pnl
4. Telegram — relay daemon running, no 409 conflicts
5. Intent Pipeline — all 5 gates active
6. TREND_FOLLOW — confirmed forbidden in TRENDING_BULL
7. Google Sheets — credentials missing handled gracefully (DEBUG, not ERROR)
8. Windows shim — fcntl shim present in main.py
9. MidnightReport + Heartbeat daemons — registered in CryptoComBot

For each area: PASS / FAIL / WARNING + fix if FAIL.
Push any fixes to branch: claude/blofin-trading-bot-dashboard-TUJBC
```

---

## Telegram debug prompt template

```
Debug Telegram in OpenClaw (~/openclaw).

Symptom: [commands not responding / duplicate replies / 403 errors / 409 conflicts]

Environment: [local machine / Railway]

Check:
1. RAILWAY_PUBLIC_URL set? → webhook mode (no polling)
2. TELEGRAM_OUTBOX_MODE=supabase? → replies go through Supabase outbox
3. telegram_outbox table — any rows with sent_at=NULL and old created_at?
4. relay daemon running? (runtime/telegram_relay.py)
5. Webhook registered? deleteWebhook and re-test polling

Log lines to look for:
- "409 webhook conflict — auto-deleting webhook"
- "TelegramRelayDaemon started"
- "Webhook deleted"
- "Railway webhook mode — skipping getUpdates polling"
```

---

## Railway deploy prompt template

```
Deploy OpenClaw to Railway.

Pre-deploy checklist:
1. All tests pass: python -m py_compile trading/cryptocom_bot.py runtime/telegram_bot.py
2. No .env or credentials.json staged
3. DEMO_MODE=true confirmed in settings.py
4. Branch is claude/blofin-trading-bot-dashboard-TUJBC

Deploy:
git push -u origin claude/blofin-trading-bot-dashboard-TUJBC

Post-deploy verify:
1. Check Railway logs for "OpenClaw bot started"
2. Send /status to @Ronsi95openclawbot
3. Confirm relay daemon running locally (python runtime/telegram_relay.py)
4. Confirm balance shows ~$295 (not $98)
```

---

## Strategy analysis prompt template

```
Analyze OpenClaw trading performance (~/openclaw).

Data: data/logs/trade_outcomes.jsonl

Report:
1. Total trades, win rate, expectancy per trade
2. Per-strategy breakdown: trades, WR, total PnL, avg PnL
3. Per-regime breakdown: which regimes produce wins/losses
4. Best strategy+regime combination
5. Worst strategy+regime combination (candidates for weight reduction)
6. TREND_FOLLOW in TRENDING_BULL: confirm 0 trades (should be forbidden)
7. Live gate status: X/30 trades, Y% WR (need 54%)

Output: weight_adjustments dict ready for data/strategy_weights.json
```

---

## Capital / risk debug prompt template

```
Debug capital preservation in OpenClaw (~/openclaw).

Check:
1. cat data/capital_state.json — state should be SAFE, alltime_peak matches balance
2. If DEFENSIVE/CRITICAL: check daily_drawdown field
3. If EMERGENCY_HALT: need /resume after balance recovers

Files:
- risk/capital_preservation.py — state machine definition
- runtime/intent_pipeline.py — Gate 5 applies capital scalar
- runtime/orchestrator.py — fires alert_capital_recovered on SAFE transition

Capital scalars:
- SAFE: 1.0 (full sizing)
- DEFENSIVE: 0.5
- CRITICAL: 0.25
- EMERGENCY_HALT: 0.0 (no trades)
```

---

## Live mode gate check prompt

```
Check live mode eligibility for OpenClaw (~/openclaw).

Run: python -c "
from runtime.live_mode_gate import LiveModeGate
gate = LiveModeGate()
print(gate.format_eligibility_report())
"

Requirements:
- Paper trades: ≥ 30 (data/logs/trade_outcomes.jsonl)
- Win rate: ≥ 54%
- Capital state: SAFE (data/capital_state.json)
- DEMO_SLIPPAGE_PCT > 0 (settings.py)

Current status: check trade count and WR against these thresholds.
NEVER set DEMO_MODE=false until all 4 gates are green.
```

---

## Quick reference: where things live

| What | Where |
|------|-------|
| Bot state (balance, trades, positions) | `data/cryptocom_state.json` |
| Capital engine state | `data/capital_state.json` |
| Strategy weights | `data/strategy_weights.json` |
| Goal tracker progress | `data/goal_tracker.json` |
| Closed trades log | `data/logs/trade_outcomes.jsonl` |
| DCA cost basis | `data/dca_state.json` |
| QUIN decisions | `data/quin_decisions.jsonl` |
| Skill clock audit | `data/skill_clock_audit.jsonl` |
| Claude Analyst reports | `data/optimization/analysis_*.json` |
| Ruflo memories | `data/ruflo/memories.pkl` |
| Replay journal | `data/replay_journal.jsonl` |

---

## Commit message style

```
fix: auto-deleteWebhook on 409 conflict in telegram_bot.py
feat: add MidnightReportDaemon and HeartbeatDaemon
chore: silence Google Sheets ERROR spam on missing credentials
fix: Windows fcntl shim in main.py
refactor: get_bot() reuses running CryptoComBot instance
```

Always push after completing a task:
```bash
git push -u origin claude/blofin-trading-bot-dashboard-TUJBC
```
