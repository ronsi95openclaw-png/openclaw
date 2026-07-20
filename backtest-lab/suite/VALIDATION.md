# Out-of-Sample / Robustness Validation — EMA Trend Retest & Range Breakout

Full-4yr OKX data (2022-06-13 → 2026-06-13), through vibe-trading's `CryptoEngine`.
Three checks: (1) parameter robustness over the full period, (2) chronological
holdout — pick params on the first 60%, test ONLY on the unseen last 40%, and
(3) per-year breakdown (default params).

## Verdict (TL;DR)

- **EMA Trend Retest — edge SURVIVES out-of-sample.** Robust across parameters
  (80% of combos positive daily, 100% on 4H) **and** positive on the unseen
  last-40% holdout on both timeframes (daily OOS Sharpe **0.81**, 4H **0.41–0.62**).
  It's a trend strategy — it loses in chop/bear years (2022, 2024-daily, 2025) but
  its edge has genuinely persisted into unseen data. The most credible of the five.

- **Range Breakout — edge FAILS out-of-sample.** It *looks* robust across
  parameters, but the temporal holdout flips it **negative** on both timeframes
  (daily OOS **−0.30** with IS-picked params / ~flat with defaults; 4H OOS **−0.42**),
  and per-year shows the gains are concentrated in the 2023–2024 bull, bleeding in
  2025–2026. Its full-period Sharpe is largely a **past-regime artifact**, not a
  persistent edge. **Do not trade as-is.**

> Key lesson: robustness-across-parameters ≠ robustness-across-time. Range passed
> the first and failed the second — which is exactly why OOS testing matters and
> why the full-period ranking (where Range was #1 on 4H) was misleading.

## Daily (1D)

**1. Parameter robustness (full period)**

| Strategy | Combos | % +Sharpe | Median | Best | Worst |
|---|---|---|---|---|---|
| EMA Trend Retest | 5 | 80% | 0.27 | 0.54 | −0.30 |
| Range Breakout | 5 | 80% | 0.21 | 0.30 | −0.50 |

**2. Chronological holdout (select on first 60%, test on last 40%)**

| Strategy | IS-picked | IS Sharpe | OOS Sharpe (sel) | OOS Ret | OOS DD | OOS Sharpe (default) |
|---|---|---|---|---|---|---|
| EMA Trend Retest | fast20/slow50 | 0.46 | **0.81** | +47% | −26% | 0.81 |
| Range Breakout | lookback10 | 0.28 | **−0.30** | −32% | −49% | 0.03 |

**3. Per-year (default params)**

| Year | EMA Sharpe | EMA Ret | Range Sharpe | Range Ret |
|---|---|---|---|---|
| 2022 | −0.61 | −18% | −1.07 | −44% |
| 2023 | 2.01 | +150% | 0.95 | +42% |
| 2024 | −0.70 | −43% | 0.25 | +1% |
| 2025 | 0.81 | +26% | 0.45 | +11% |
| 2026 | −1.18 | −20% | 0.75 | +10% |

## 4H

**1. Parameter robustness (full period)**

| Strategy | Combos | % +Sharpe | Median | Best | Worst |
|---|---|---|---|---|---|
| EMA Trend Retest | 5 | 100% | 0.26 | 0.77 | 0.18 |
| Range Breakout | 5 | 100% | 0.46 | 0.58 | 0.19 |

**2. Chronological holdout (select on first 60%, test on last 40%)**

| Strategy | IS-picked | IS Sharpe | OOS Sharpe (sel) | OOS Ret | OOS DD | OOS Sharpe (default) |
|---|---|---|---|---|---|---|
| EMA Trend Retest | fast10/slow50 | 0.78 | **0.41** | +14% | −39% | 0.62 |
| Range Breakout | lookback20 | 0.92 | **−0.42** | −40% | −59% | −0.42 |

**3. Per-year (default params)**

| Year | EMA Sharpe | EMA Ret | Range Sharpe | Range Ret |
|---|---|---|---|---|
| 2022 | −0.98 | −32% | −0.23 | −16% |
| 2023 | 0.54 | +15% | 1.75 | +129% |
| 2024 | 0.79 | +33% | 0.89 | +37% |
| 2025 | −0.04 | −11% | −0.81 | −41% |
| 2026 | 1.47 | +25% | −0.12 | −7% |

## Caveats
- Still **single-dataset, default-cost** backtests (OKX, ~17 bps round-trip, $1M, 12% stop).
- Holdout is one 60/40 split; fine-grained rolling walk-forward was avoided because 4 years
  is too short to roll without indicator-warmup artifacts (200-bar EMAs on small windows).
- EMA "survives OOS" means the historical edge persisted into unseen data — **not** a
  guarantee of future performance. Next step before any capital: more symbols/exchanges,
  a longer history, and live paper-forward.
