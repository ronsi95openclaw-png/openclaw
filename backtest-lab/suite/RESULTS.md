# Vibe-Trading Backtest Suite — Results

**5 strategies × {BTC, ETH, SOL} × {daily, 4H}**, run through vibe-trading's own
`CryptoEngine`. Generated 2026-06-14.

> **⚠️ Read [VALIDATION.md](./VALIDATION.md) before trusting this ranking.** Out-of-sample
> testing shows **EMA Trend Retest's edge survives** the unseen holdout, but **Range
> Breakout's does NOT** — its strong full-period Sharpe (incl. #1 on 4H below) is a
> 2023–2024-bull artifact that turns negative out-of-sample. Full-period rank ≠ tradable edge.

## Methodology
- **Data:** ccxt → **OKX** spot (and OKX perps + funding history for Funding MR).
  `binance.com` is geo-blocked (HTTP 451) on this box, so OKX is the source.
- **Period:** 2022-06-13 → 2026-06-13 (**4 years**). Daily = 1462 bars; 4H = 8767 bars. Full coverage on both timeframes.
- **Engine:** vibe-trading `backtest.engines.crypto.CryptoEngine`. Initial capital **$1,000,000**, long + short.
- **Costs modeled:** taker 5 bps, maker 2 bps, slippage 5 bps (~17 bps round-trip) + liquidation logic.
- **Aggregation:** one backtest per strategy across all 3 symbols (portfolio); ranked by **Sharpe** within each timeframe.
- **Engines:** `SMC` is vibe-trading's **shipped** signal engine (verbatim). The other four are **hand-built** from the skill specs (`technical-basic`/`smc`) with a uniform 12% per-trade stop. Default (unoptimized) parameters.

## Daily (1D)

| Rank | Strategy | Sharpe | Total Return | Max DD | Win Rate | Trades | Annual | Sortino |
|---|---|---|---|---|---|---|---|---|
| 1 | **EMA Trend Retest** | **0.54** | +77.6% | −58.0% | 30% | 70 | +15.4% | 0.77 |
| 2 | Range Breakout | 0.30 | +7.7% | −74.8% | 35% | 107 | +2.0% | 0.39 |
| 3 | Funding Rate MR | 0.14 | +3.3% | **−15.9%** | 46% | 24 | +0.8% | 0.04 |
| 4 | Liquidity Sweep | −0.07 | −47.1% | −76.6% | 52% | 188 | −15% | −0.09 |
| 5 | SMC *(shipped)* | −2.27 | −74.3% | −74.3% | 27% | 90 | −29% | −0.80 |

## 4H

| Rank | Strategy | Sharpe | Total Return | Max DD | Win Rate | Trades | Annual | Sortino |
|---|---|---|---|---|---|---|---|---|
| 1 | **Range Breakout** | **0.44** | +44.8% | −59.4% | 38% | 545 | +10% | 0.58 |
| 2 | EMA Trend Retest | 0.18 | −11.0% | −51.2% | 27% | 430 | −3% | 0.23 |
| 3 | Liquidity Sweep | −0.30 | −66.6% | −82.2% | 63% | 818 | −24% | −0.37 |
| 4 | Funding Rate MR | −0.41 | −15.0% | −29.6% | 43% | 100 | −4% | −0.12 |
| 5 | SMC *(shipped)* | −5.79 | −98.2% | −98.2% | 19% | 531 | −64% | −1.93 |

## Takeaways
- **EMA Trend Retest** is the most robust — the only strategy with a positive Sharpe on **both** timeframes (#1 daily, #2 4H).
- **Range Breakout** is the best 4H performer and a solid #2 daily. Trend-following beat mean-reversion over this (bull-dominated) 4-year window.
- **Funding Rate MR** is the capital-preserver: by far the **lowest drawdown** (−16% daily) but near-flat returns and few trades.
- **Liquidity Sweep** has the **highest win rate** (52–63%) but **negative expectancy** — many small wins, fewer large losses. Classic mean-reversion payoff that nets negative here.
- **SMC — the one *official* shipped engine — is the worst on both timeframes**, near-total loss on 4H. The naive default over-trades and bleeds on chop + fees.

## Caveats (read before trusting)
- **Unoptimized, in-sample, single backtest.** No walk-forward / out-of-sample / parameter robustness. **Not investment advice.**
- The 4 hand-built engines are faithful-to-spec reimplementations, not vibe-trading's own (only SMC is the tool's). A 12% stop was added to all four (real strategies use stops; it also prevents shorts from driving equity negative).
- Aggregate across 3 symbols; per-symbol breakdowns are in each `runs/suite/<strategy>_<interval>/run_card.json` (`by_symbol`).
- Funding MR trades OKX perps using funding z-score (window 30, entry |z|>1.5); the others trade spot.

## Reproduce
From `backtest-lab/` with the sidecar/venv:
```
PYTHONUTF8=1 VIBE_TRADING_ALLOWED_RUN_ROOTS=$PWD/runs \
  .venv-backtest/Scripts/python.exe suite/run_suite.py 1D 4H
```
Engines: `suite/strategies.py`. Orchestrator: `suite/run_suite.py`. Artifacts per run in `runs/suite/`.
