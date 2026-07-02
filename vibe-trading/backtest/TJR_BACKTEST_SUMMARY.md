# TJR ICT Kill Zone — Backtest Summary
**Generated:** 2026-07-02 (re-run of existing `tjr_backtest.py` and `backtest_4yr/tjr_backtest_4yr.py`, no code changes)

> Read both caveats sections before trusting either table. Neither run is a walk-forward /
> out-of-sample test — same discipline gap flagged in `backtest-lab/suite/VALIDATION.md`
> for the crypto suite applies here.

## Run 1 — Precise mechanics, real 5M data, short window
`python tjr_backtest.py --csv backtest/data/ES_5M.csv --instrument ES --contracts 1`

- **Data:** real ES 5-minute bars, 2026-04-10 → 2026-06-18 (4,410 bars). First valid setup wasn't until 2026-05-01, so effective window is ~7 weeks.
- **Result:** 3 trades, 100% win rate, PF ∞, avg 2.0R, net **+$586.50**, max DD $0.
- **Lucid compliance:** ❌ estimated FAIL — 1 consistency violation (2026-06-03 trade = 100% of the period's total profit; Lucid caps any single day at 50%).
- **Read:** This uses the *exact* mechanical logic (FVG/OB/OTE/MSB) but n=3 is not a statistically meaningful sample — 100% win rate on 3 trades tells you almost nothing about the true edge, good or bad.

## Run 2 — Approximate mechanics ("4yr v2"), longer window
`python backtest_4yr/tjr_backtest_4yr.py --csv backtest_4yr/data/<INST>_<TF>.csv --instrument <INST> --timeframe <tf> --contracts 1`

| Instrument | Timeframe | Data range | Trades | Win % | Profit Factor | Avg R | Net P&L | Max DD | Lucid 25K |
|---|---|---|---|---|---|---|---|---|---|
| ES | 1D | 2022-05-18 → 2026-06-15 | 22 | 27.3% | 0.66 | −0.61R | **−$13,436.50** | $23,682.50 (94.7%) | ❌ FAIL |
| ES | 1H | 2024-06-17 → 2026-06-15 | 17 | 58.8% | 0.39 | −3.38R | **−$5,814.00** | $6,605.00 (26.4%) | ❌ FAIL |
| NQ | 1D | 2022-05-18 → 2026-06-15 | 18 | 44.4% | 0.83 | −0.35R | **−$10,235.00** | $42,203.50 (168.8%) | ❌ FAIL |
| NQ | 1H | 2024-06-17 → 2026-06-15 | 18 | 83.3% | 2.21 | +1.89R | **+$5,369.00** | $3,808.50 (15.2%) | ❌ FAIL (flagged, though profitable — likely consistency/target rule) |

- **Read:** 3 of 4 instrument/timeframe combos lose money — ES on both timeframes and NQ daily are meaningfully negative, with ES 1D and NQ 1D showing drawdowns that blow well past the $1,500 Lucid hard-loss limit (94.7% and 168.8% of the $25K account) if unprotected by `risk_guard`'s circuit breakers. Only NQ 1H is a clear standout (PF 2.21, +1.89R avg), but even that's flagged as an eval FAIL by the script.
- This script is a coarser proxy of the strategy (1D/1H bars) — not the same bar-by-bar FVG/OB/MSB precision as `tjr_backtest.py`. Treat it as directional signal on regime/instrument fit, not a precise P&L forecast.

## Bottom line
Neither run currently supports "this strategy reliably passes a Lucid 25K eval." The precise backtest has too few trades to trust either way. The longer approximate backtest shows a real edge only on NQ 1H, and loses meaningfully everywhere else — including drawdowns that would blow the account multiple times over without the bot's live risk_guard circuit breakers in place. This directly informs how much confidence to place in daily suggestions from `vibe-trade-tjr-premarket` in the meantime — treat them as one input, not a proven system.

## Caveats
- No walk-forward / out-of-sample split, no parameter robustness check, no slippage/commission modeling in either script (confirm against `strategy.py` / `tjr_backtest.py` source before assuming costs are included).
- Small sample sizes throughout (3 to 22 trades) — wide confidence intervals on every stat above.
- `backtest_4yr` results reproduced from existing data files already in the repo (`backtest_4yr/data/*.csv`); not re-pulled from a live source.
- Not investment advice.
