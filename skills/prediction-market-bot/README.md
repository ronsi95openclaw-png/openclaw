# Prediction Market Bot

This folder contains a Claude skill scaffold for building a prediction market trading bot.

## What it includes

- `SKILL.md` — the Claude skill definition and workflow guidance.
- `scripts/` — Python skeletons for each pipeline stage.
- `scripts/formulas.md` — key trading and probability formulas.
- `scripts/platforms.md` — platform notes for Polymarket and Kalshi.
- `scripts/failure_log.md` — a structured file for lessons and post-mortem summaries.

## Getting started

1. Review `SKILL.md` and adapt the workflow to your domain.
2. Use demo API keys for Polymarket and Kalshi until the strategy is validated.
3. Run the Python scripts in `scripts/` as a reference for integration and risk checks.
4. Add new source data to the research pipeline and test the prediction logic with simulated trades.

## Recommended workflow

1. `python -m skills.prediction_market_bot.scripts.scan` — discover candidate markets.
2. `python -m skills.prediction_market_bot.scripts.research` — gather intelligence for each candidate.
3. `python -m skills.prediction_market_bot.scripts.predict` — estimate probabilities and edge.
4. `python -m skills.prediction_market_bot.scripts.risk` — validate the trade.
5. `python -m skills.prediction_market_bot.scripts.execute` — place orders in demo mode.
6. `python -m skills.prediction_market_bot.scripts.compound` — log outcomes and learn.

## Important notes

- This repo is not a finished trading bot. It is a framework and educational starting point.
- Do not run live trades without manual review and strong risk controls.
- Use the `failure_log.md` file to collect lessons for future improvement.

## Paper trading simulator

- `scripts/paper_trade.py` simulates trades and logs results to `scripts/paper_trades.md`.
- Use paper trading to test your edge, win rate, and position sizing before connecting to live markets.
- Track simulated trade performance as part of your compound learning process.
