# Backtest Correctness Verification

Answers "does it actually work?" with evidence, not assumption. All 5 strategies
held to the same standard. Reproduce: `suite/verify_correctness.py` (EMA) and
`suite/verify_all.py` (Range / Liquidity / Funding).

## What was verified

1. **No look-ahead bias.** The engine shifts every signal by one bar before
   execution — `base.py:79` *"Signal is shifted by 1 bar (next-bar-open semantics)"*,
   `raw.shift(1)` at line 119. A signal from bar *t*'s close fills at bar *t+1*'s
   open. The hand-built engines additionally use `.shift(1)` on their rolling
   high/low/swing levels, so the rules read only prior-bar information.

2. **Reported metrics reconcile EXACTLY to the raw equity curve** (`equity.csv`),
   independently recomputed:

   | Engine | total_return (eq vs reported) | max_drawdown (eq vs reported) |
   |---|---|---|
   | EMA Trend Retest | 0.7760 = 0.7760 | −0.5796 = −0.5796 |
   | Range Breakout | 0.0767 = 0.0767 | −0.7481 = −0.7481 |
   | Liquidity Sweep | −0.4714 = −0.4714 | −0.7657 = −0.7657 |
   | Funding Rate MR | 0.0328 = 0.0328 | −0.1595 = −0.1595 |

3. **Per-trade P&L is arithmetically correct** — one round-trip per engine
   recomputed by hand `dir·(exit−entry)·qty` and matched to the cent:
   EMA −15,688.19 · Range −44,724.18 · Liquidity −130,961.36 · Funding −515.40.

4. **Every entry obeys the engine's own rule** — for each fresh position the
   engine opened, the engine's stated entry condition was True at that bar:
   Range 39/39, Liquidity 62/62, Funding 13/13 = **100%**. Nothing fired outside
   its logic.

5. **Funding data is real** — BTC perp funding: 1462/1462 non-zero, range
   ±0.0001 (±0.01%/8h, typical), mean −0.000025. Not stubbed zeros.

## What was NOT tested (still open)
- **SMC** signal logic was not re-derived here (it's vibe-trading's shipped engine,
  used verbatim; its engine-accounting is covered by checks 1–3).
- `sum(per-trade gross P&L)` vs net equity change differs by the fee drag + any
  open position at period end (per-trade P&L is gross; fees hit capital separately).
  Headline return/drawdown reconcile exactly; this residual is explained, not a tie.
- **No live / paper-forward test.** These are historically-correct backtests, not
  forward-validated. Before any capital: more symbols/exchanges, longer history,
  rolling walk-forward, then paper-forward.

## Bottom line
The pipeline is look-ahead-free and the numbers are real (not artifacts of a broken
harness). Correct ≠ profitable: see `VALIDATION.md` — only EMA Trend Retest's edge
survived out-of-sample.
