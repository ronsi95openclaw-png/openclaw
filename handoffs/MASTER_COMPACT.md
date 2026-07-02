# Master Compact — Updated 2026-07-02

## Decisions Made (keep forever)
- 2026-07-02 OpenAlice/CryptoBot: Declared dead pillars. No longer active — do not surface as live projects in future briefings.
- 2026-07-02 vibe-trading: This is now the live trading pillar (replaces OpenAlice/CryptoBot in that role). TJR/ICT Kill Zone strategy for a Lucid 25K prop eval, built on the HKUDS/Vibe-Trading framework. Repo already exists locally at `vibe-trading/` (bot, backtest, strategies, hermes skill docs).
- 2026-07-02 vibe-trading local bot (`bot/runner.py`) is paper/DRY_RUN by default via a dual-lock go-live gate (`HERMES_BOT_LIVE=1` env AND `config.go_live=true`, both required). Confirmed safe design — do not remove or weaken this gate without an explicit go-live review against the Go-Live Checklist in `bot/README.md`.
- 2026-07-02 Daily scheduled task `vibe-trade-tjr-premarket` created (weekdays 8:00 AM CT, 30 min pre-NY-open): does NOT run the local bot (see blocker below) — instead Claude performs the TJR/ICT read directly using the Liquid MCP connector (analyze_market, get_news, get_portfolio) and renders a `suggest_trade` confirm-required card. Never auto-executes; Ronnie must press Confirm.
- 2026-07-02 Liquid account sizing: paper trading is DISABLED on the Liquid account (live, real money). Balance is small (~$67-92, not the $25K Lucid mandate assumes). Scheduled task is instructed to size conservatively (~$15-20 collateral, 2-3x leverage) and never exceed `available_balance` — explicitly NOT using the Lucid mandate's $150-300/trade risk numbers for this account.

## Patterns & Learnings (keep forever)
- The Cowork sandbox's network egress blocks Yahoo Finance (`query1/query2.finance.yahoo.com` → 403 from proxy), confirmed via direct curl test (general internet works fine, e.g. anthropic.com 200). So `vibe-trading/bot/runner.py`'s yfinance-based live data fetch cannot run from this sandbox — it needs to run on Ronnie's actual Windows machine (same pattern as CryptoBot's local `.bat` wrapper relaunch).
- Liquid MCP's `analyze_market` returns price/funding/OI/positioning-by-size-segment data — good for bias/crowding reads, but does NOT return raw OHLC candle history, so it can't replicate the local bot's exact mechanical FVG/Order-Block/Market-Structure-Break detection. Any Liquid-MCP-based TJR read is a qualitative/judgment approximation of the strategy, not the mechanically precise version.
- `suggest_trade` (Liquid MCP) renders an interactive card requiring Ronnie to press Confirm — it is not an execution. This is the safe mechanism for "give me the setup, I'll decide" workflows; never call any other order/execution tool on an unattended schedule.

## Completed Work (last 30 days)
- 2026-07-02: Investigated vibe-trading local bot (README, ARCHITECTURE, risk_guard, runner, strategy spec, kill switch) — confirmed paper-safe by design. Ran a live test cycle (`python runner.py --once`) — pipeline executed correctly, logged a clean "skip" decision; failure was network-only (yfinance blocked), not a logic bug.
- 2026-07-02: Tested Liquid MCP path live — `get_portfolio` ($92.37 equity / $67.17 available, no open positions), `paper_trading_status` (disabled/live), `get_news` (no major scheduled catalyst today, chip-stock weakness on Meta AI cloud news), `analyze_market` S&P500 (mixed/no strongly crowded positioning across size segments — did not meet the bar for a trade suggestion, correctly resulted in "no trade" rather than forcing a setup).
- 2026-07-02: Created and tuned scheduled task `vibe-trade-tjr-premarket` (weekdays 8:00 AM CT).

- 2026-07-02: Ran the existing TJR backtests (no code changes) — full results in `vibe-trading/backtest/TJR_BACKTEST_SUMMARY.md`. Precise 5M backtest (real data, 7-week window): only 3 trades, +$586.50, too small to trust. Longer 4yr approximate backtest: ES 1D −$13,436.50 (94.7% DD), ES 1H −$5,814, NQ 1D −$10,235 (168.8% DD), NQ 1H +$5,369 (only profitable combo). **Strategy does not currently show a reliably profitable edge across instruments/timeframes — treat daily `vibe-trade-tjr-premarket` suggestions as one input, not a validated system, until this is revisited.**

## Superseded / Stale (archive)
- OpenAlice/TraderAlice and CryptoBot/ClawBot sections of the original pillar tracking are stale as of 2026-07-02 — superseded by vibe-trading. Keep historical detail in old handoffs but stop carrying them forward in new briefings unless Ronnie revives them.
