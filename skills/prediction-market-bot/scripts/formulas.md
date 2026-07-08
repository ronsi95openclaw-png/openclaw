# Prediction Market Formulas

## Edge

- `edge = p_model - p_market`

Trade only when `edge` is meaningfully positive.

## Expected Value

- `EV = p_model * b - (1 - p_model)`

Where `b` is the net decimal odds minus 1.

## Mispricing Score

- `delta = (p_model - p_market) / sigma`

Where `sigma` is the standard deviation or estimated volatility of the model ensemble.

## Kelly Criterion

- Full Kelly: `f* = (p * b - q) / b`
- Fractional Kelly: `f = fraction * f*`

Where:
- `p` is the win probability
- `q = 1 - p`
- `b` is the net odds
- `fraction` is typically 0.25 to 0.5 for safer sizing

## Brier Score

- `BS = (1 / n) * sum((p_i - o_i) ** 2)`

Where:
- `p_i` is the predicted probability
- `o_i` is the outcome (1 for yes, 0 for no)
- Lower is better; target < 0.25 for well-calibrated predictions.

## Value at Risk (VaR)

A simple VaR approximation:

- `VaR = portfolio_value * z * sigma`

Where:
- `z` is the z-score for the desired confidence level (e.g. 1.645 for 95%)
- `sigma` is the estimated standard deviation of portfolio returns

## Position sizing limits

Recommended maximums:
- `max_single_position = 0.05 * bankroll`
- `max_total_exposure = 0.15 * bankroll`
- `max_daily_loss = 0.15 * bankroll`
- `max_drawdown = 0.08 * bankroll`
