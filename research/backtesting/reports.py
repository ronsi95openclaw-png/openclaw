"""Report generation for backtesting results.

Supports four output formats:
    HTML     – self-contained dark-theme page with equity curve, drawdown,
               summary stats, and trade table
    Markdown – plain text tabular report
    CSV      – trade-by-trade data for spreadsheet analysis
    JSON     – machine-readable summary dict
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List

from research.types import BacktestResult, BacktestTrade, PerformanceMetrics


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(v: float, decimals: int = 2) -> str:
    return f"{v:,.{decimals}f}"


def _pct(v: float) -> str:
    return f"{v:.2f}%"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


# ── ASCII equity chart ────────────────────────────────────────────────────────

def _ascii_chart(equity: List[float], width: int = 60, height: int = 12) -> str:
    """Render a minimal ASCII equity curve for HTML <pre> blocks."""
    if len(equity) < 2:
        return "(insufficient data)"
    lo, hi = min(equity), max(equity)
    span = hi - lo or 1.0

    # Down-sample to `width` points
    step = max(1, len(equity) // width)
    samples = equity[::step]
    if len(samples) > width:
        samples = samples[:width]

    rows: List[List[str]] = [[" "] * len(samples) for _ in range(height)]
    for col, val in enumerate(samples):
        row_idx = int((1.0 - (val - lo) / span) * (height - 1))
        row_idx = max(0, min(height - 1, row_idx))
        rows[row_idx][col] = "█"

    lines = ["".join(row) for row in rows]
    # Add axis labels
    hi_label = f"${hi:,.0f}"
    lo_label = f"${lo:,.0f}"
    chart = f"{hi_label:>12} ┤{''.join(lines[0])}\n"
    for line in lines[1:-1]:
        chart += f"{'':>12} │{line}\n"
    chart += f"{lo_label:>12} ┤{''.join(lines[-1])}\n"
    return chart


# ── HTML drawdown chart ───────────────────────────────────────────────────────

def _html_bar_chart(values: List[float], color: str = "#00d4ff", height_px: int = 60) -> str:
    """Render a tiny inline bar chart as HTML spans."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    step = max(1, len(values) // 120)
    samples = values[::step]
    bars = []
    for v in samples:
        pct = int(((v - lo) / span) * 100)
        bars.append(
            f'<span style="display:inline-block;width:2px;height:{pct}%;'
            f'background:{color};vertical-align:bottom;margin:0 1px;"></span>'
        )
    return (
        f'<div style="display:flex;align-items:flex-end;height:{height_px}px;'
        f'overflow:hidden;background:#111;padding:4px;">'
        + "".join(bars)
        + "</div>"
    )


# ── Main class ────────────────────────────────────────────────────────────────

class ReportGenerator:
    """Generate reports from BacktestResult and PerformanceMetrics."""

    # ── HTML ──────────────────────────────────────────────────────────────────

    def generate_html(
        self,
        result: BacktestResult,
        metrics: PerformanceMetrics,
        output_path: str = "data/reports/backtest.html",
    ) -> str:
        """Write a self-contained HTML report and return the file path."""
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

        html = self._build_html(result, metrics)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return output_path

    def _build_html(self, result: BacktestResult, metrics: PerformanceMetrics) -> str:
        # Drawdown series
        equity = result.equity_curve
        dd_series: List[float] = []
        peak = equity[0] if equity else 1.0
        for v in equity:
            if v > peak:
                peak = v
            dd_series.append(((v - peak) / peak * 100) if peak > 0 else 0.0)

        equity_chart = _html_bar_chart(equity, color="#00d4ff", height_px=80)
        dd_chart = _html_bar_chart(dd_series, color="#ff4d4d", height_px=60)

        trade_rows = ""
        for t in result.trades:
            color = "#00c97a" if t.net_pnl >= 0 else "#ff4d4d"
            trade_rows += (
                f"<tr>"
                f"<td>{t.trade_id}</td>"
                f"<td>{t.side.upper()}</td>"
                f"<td>{t.entry_price:,.4f}</td>"
                f"<td>{t.exit_price:,.4f}</td>"
                f"<td>{t.size:.4f}</td>"
                f"<td style='color:{color}'>${t.net_pnl:+,.2f}</td>"
                f"<td>{t.net_pnl_pct:+.2f}%</td>"
                f"<td>{t.holding_bars}</td>"
                f"<td>{t.exit_reason}</td>"
                f"</tr>\n"
            )

        params_html = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in result.params.items()
        )

        total_return_color = "#00c97a" if metrics.total_return_pct >= 0 else "#ff4d4d"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenClaw Backtest — {result.strategy} / {result.symbol}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0a0a0f;
    color: #c8d8e8;
    font-family: 'Courier New', monospace;
    font-size: 13px;
    padding: 24px;
  }}
  h1 {{ color: #00d4ff; font-size: 20px; margin-bottom: 4px; }}
  h2 {{ color: #80bfff; font-size: 14px; margin: 20px 0 8px; text-transform: uppercase;
        letter-spacing: 2px; border-bottom: 1px solid #1e2d3d; padding-bottom: 4px; }}
  .meta {{ color: #667799; font-size: 11px; margin-bottom: 20px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
  .kpi {{
    background: #111827;
    border: 1px solid #1e2d3d;
    border-radius: 6px;
    padding: 12px 16px;
  }}
  .kpi .label {{ color: #667799; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; }}
  .kpi .value {{ font-size: 18px; font-weight: bold; margin-top: 4px; color: #e8f0f8; }}
  .positive {{ color: #00c97a !important; }}
  .negative {{ color: #ff4d4d !important; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    margin-top: 8px;
  }}
  th {{
    background: #111827;
    color: #80bfff;
    text-align: left;
    padding: 6px 10px;
    font-weight: normal;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-size: 10px;
    border-bottom: 1px solid #1e2d3d;
  }}
  td {{
    padding: 5px 10px;
    border-bottom: 1px solid #0e1a26;
    color: #c8d8e8;
  }}
  tr:hover td {{ background: #0e1a26; }}
  .chart-label {{ color: #667799; font-size: 10px; margin: 4px 0 2px; }}
  pre {{
    background: #080d14;
    padding: 12px;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 10px;
    line-height: 1.3;
    color: #00d4ff;
    border: 1px solid #1e2d3d;
  }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: bold;
  }}
  footer {{ margin-top: 40px; color: #334455; font-size: 10px; text-align: center; }}
</style>
</head>
<body>

<h1>&#x1F43E; OpenClaw Backtesting Report</h1>
<div class="meta">
  Strategy: <b>{result.strategy}</b> &nbsp;|&nbsp;
  Symbol: <b>{result.symbol}</b> &nbsp;|&nbsp;
  {result.start_time.strftime('%Y-%m-%d')} → {result.end_time.strftime('%Y-%m-%d')} &nbsp;|&nbsp;
  Capital: ${result.initial_capital:,.2f} → <span style="color:{total_return_color}">${result.final_capital:,.2f}</span>
</div>

<h2>Performance Summary</h2>
<div class="grid">
  <div class="kpi">
    <div class="label">Total Return</div>
    <div class="value {'positive' if metrics.total_return_pct >= 0 else 'negative'}">{_pct(metrics.total_return_pct)}</div>
  </div>
  <div class="kpi">
    <div class="label">Ann. Return</div>
    <div class="value {'positive' if metrics.annualized_return_pct >= 0 else 'negative'}">{_pct(metrics.annualized_return_pct)}</div>
  </div>
  <div class="kpi">
    <div class="label">Sharpe Ratio</div>
    <div class="value {'positive' if metrics.sharpe_ratio >= 1 else ''}">{_fmt(metrics.sharpe_ratio)}</div>
  </div>
  <div class="kpi">
    <div class="label">Sortino Ratio</div>
    <div class="value">{_fmt(metrics.sortino_ratio)}</div>
  </div>
  <div class="kpi">
    <div class="label">Max Drawdown</div>
    <div class="value negative">{_pct(metrics.max_drawdown_pct)}</div>
  </div>
  <div class="kpi">
    <div class="label">Calmar Ratio</div>
    <div class="value">{_fmt(metrics.calmar_ratio)}</div>
  </div>
  <div class="kpi">
    <div class="label">Win Rate</div>
    <div class="value">{_pct(metrics.win_rate * 100)}</div>
  </div>
  <div class="kpi">
    <div class="label">Profit Factor</div>
    <div class="value {'positive' if metrics.profit_factor >= 1 else 'negative'}">{_fmt(metrics.profit_factor)}</div>
  </div>
  <div class="kpi">
    <div class="label">Expectancy</div>
    <div class="value {'positive' if metrics.expectancy >= 0 else 'negative'}">${_fmt(metrics.expectancy)}</div>
  </div>
  <div class="kpi">
    <div class="label">Total Trades</div>
    <div class="value">{metrics.total_trades}</div>
  </div>
  <div class="kpi">
    <div class="label">Total Fees</div>
    <div class="value negative">${_fmt(metrics.total_fees)}</div>
  </div>
  <div class="kpi">
    <div class="label">Omega Ratio</div>
    <div class="value">{_fmt(metrics.omega_ratio)}</div>
  </div>
</div>

<h2>Equity Curve</h2>
<div class="chart-label">Portfolio Value over Time</div>
{equity_chart}

<h2>Drawdown Curve</h2>
<div class="chart-label">Drawdown from Peak (%)</div>
{dd_chart}

<h2>Full Metrics</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>CAGR</td><td>{_pct(metrics.cagr)}</td></tr>
<tr><td>Recovery Factor</td><td>{_fmt(metrics.recovery_factor)}</td></tr>
<tr><td>Max DD Duration (bars)</td><td>{metrics.max_drawdown_duration_bars}</td></tr>
<tr><td>Winning Trades</td><td>{metrics.winning_trades}</td></tr>
<tr><td>Losing Trades</td><td>{metrics.losing_trades}</td></tr>
<tr><td>Payoff Ratio</td><td>{_fmt(metrics.payoff_ratio)}</td></tr>
<tr><td>Avg Win</td><td>${_fmt(metrics.avg_win)}</td></tr>
<tr><td>Avg Loss</td><td>${_fmt(metrics.avg_loss)}</td></tr>
<tr><td>Largest Win</td><td>${_fmt(metrics.largest_win)}</td></tr>
<tr><td>Largest Loss</td><td>${_fmt(metrics.largest_loss)}</td></tr>
<tr><td>Max Win Streak</td><td>{metrics.max_win_streak}</td></tr>
<tr><td>Max Loss Streak</td><td>{metrics.max_loss_streak}</td></tr>
<tr><td>Avg Holding Bars</td><td>{_fmt(metrics.avg_holding_bars)}</td></tr>
<tr><td>Total Slippage</td><td>${_fmt(metrics.total_slippage)}</td></tr>
</table>

<h2>Strategy Parameters</h2>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
{params_html if result.params else '<tr><td colspan="2">No parameters</td></tr>'}
</table>

<h2>Trade Log ({len(result.trades)} trades)</h2>
<table>
<tr>
  <th>ID</th><th>Side</th><th>Entry</th><th>Exit</th>
  <th>Size</th><th>Net PnL</th><th>PnL %</th>
  <th>Bars</th><th>Reason</th>
</tr>
{trade_rows if trade_rows else '<tr><td colspan="9">No trades</td></tr>'}
</table>

<footer>
  Generated by OpenClaw Research Engine &mdash;
  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
</footer>
</body>
</html>"""

    # ── Markdown ──────────────────────────────────────────────────────────────

    def generate_markdown(
        self, result: BacktestResult, metrics: PerformanceMetrics
    ) -> str:
        """Return a Markdown-formatted report string."""
        lines = [
            f"# OpenClaw Backtest Report",
            f"",
            f"**Strategy:** {result.strategy}  ",
            f"**Symbol:** {result.symbol}  ",
            f"**Period:** {result.start_time.strftime('%Y-%m-%d')} → "
            f"{result.end_time.strftime('%Y-%m-%d')}  ",
            f"**Capital:** ${result.initial_capital:,.2f} → ${result.final_capital:,.2f}",
            f"",
            f"## Performance Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Return | {_pct(metrics.total_return_pct)} |",
            f"| Ann. Return | {_pct(metrics.annualized_return_pct)} |",
            f"| CAGR | {_pct(metrics.cagr)} |",
            f"| Sharpe Ratio | {_fmt(metrics.sharpe_ratio)} |",
            f"| Sortino Ratio | {_fmt(metrics.sortino_ratio)} |",
            f"| Calmar Ratio | {_fmt(metrics.calmar_ratio)} |",
            f"| Omega Ratio | {_fmt(metrics.omega_ratio)} |",
            f"| Max Drawdown | {_pct(metrics.max_drawdown_pct)} |",
            f"| Max DD Duration | {metrics.max_drawdown_duration_bars} bars |",
            f"| Recovery Factor | {_fmt(metrics.recovery_factor)} |",
            f"| Total Trades | {metrics.total_trades} |",
            f"| Win Rate | {_pct(metrics.win_rate * 100)} |",
            f"| Profit Factor | {_fmt(metrics.profit_factor)} |",
            f"| Payoff Ratio | {_fmt(metrics.payoff_ratio)} |",
            f"| Expectancy | ${_fmt(metrics.expectancy)} |",
            f"| Avg Win | ${_fmt(metrics.avg_win)} |",
            f"| Avg Loss | ${_fmt(metrics.avg_loss)} |",
            f"| Max Win Streak | {metrics.max_win_streak} |",
            f"| Max Loss Streak | {metrics.max_loss_streak} |",
            f"| Total Fees | ${_fmt(metrics.total_fees)} |",
            f"| Total Slippage | ${_fmt(metrics.total_slippage)} |",
            f"",
        ]

        if result.params:
            lines += [
                "## Strategy Parameters",
                "",
                "| Parameter | Value |",
                "|-----------|-------|",
            ]
            for k, v in result.params.items():
                lines.append(f"| {k} | {v} |")
            lines.append("")

        lines += [
            "## Trade Log",
            "",
            "| ID | Side | Entry | Exit | Size | Net PnL | PnL % | Bars | Reason |",
            "|----|------|-------|------|------|---------|-------|------|--------|",
        ]
        for t in result.trades:
            lines.append(
                f"| {t.trade_id} | {t.side.upper()} | {t.entry_price:,.4f} | "
                f"{t.exit_price:,.4f} | {t.size:.4f} | ${t.net_pnl:+,.2f} | "
                f"{t.net_pnl_pct:+.2f}% | {t.holding_bars} | {t.exit_reason} |"
            )

        return "\n".join(lines)

    # ── CSV ───────────────────────────────────────────────────────────────────

    def generate_csv(
        self,
        result: BacktestResult,
        output_path: str = "data/reports/trades.csv",
    ) -> str:
        """Write trades to CSV and return the file path."""
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

        fieldnames = [
            "trade_id", "symbol", "strategy", "side",
            "entry_time", "exit_time",
            "entry_price", "exit_price", "size",
            "gross_pnl", "fees", "net_pnl", "net_pnl_pct",
            "entry_slippage", "exit_slippage",
            "max_adverse_excursion", "max_favorable_excursion",
            "holding_bars", "exit_reason", "funding_paid",
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for t in result.trades:
                writer.writerow({
                    "trade_id":              t.trade_id,
                    "symbol":                t.symbol,
                    "strategy":              t.strategy,
                    "side":                  t.side,
                    "entry_time":            t.entry_time.isoformat(),
                    "exit_time":             t.exit_time.isoformat(),
                    "entry_price":           t.entry_price,
                    "exit_price":            t.exit_price,
                    "size":                  t.size,
                    "gross_pnl":             t.gross_pnl,
                    "fees":                  t.fees,
                    "net_pnl":               t.net_pnl,
                    "net_pnl_pct":           t.net_pnl_pct,
                    "entry_slippage":        t.entry_slippage,
                    "exit_slippage":         t.exit_slippage,
                    "max_adverse_excursion": t.max_adverse_excursion,
                    "max_favorable_excursion": t.max_favorable_excursion,
                    "holding_bars":          t.holding_bars,
                    "exit_reason":           t.exit_reason,
                    "funding_paid":          t.funding_paid,
                })
        return output_path

    # ── JSON ──────────────────────────────────────────────────────────────────

    def generate_json_summary(
        self,
        result: BacktestResult,
        metrics: PerformanceMetrics,
    ) -> Dict[str, Any]:
        """Return a complete JSON-serialisable summary dictionary."""
        return {
            "backtest": {
                "strategy":       result.strategy,
                "symbol":         result.symbol,
                "params":         result.params,
                "start_time":     result.start_time.isoformat(),
                "end_time":       result.end_time.isoformat(),
                "initial_capital": result.initial_capital,
                "final_capital":  result.final_capital,
                "total_bars":     len(result.equity_curve),
                "metadata":       result.metadata,
            },
            "performance": {
                "total_return_pct":         metrics.total_return_pct,
                "annualized_return_pct":    metrics.annualized_return_pct,
                "cagr":                     metrics.cagr,
                "sharpe_ratio":             metrics.sharpe_ratio,
                "sortino_ratio":            metrics.sortino_ratio,
                "calmar_ratio":             metrics.calmar_ratio,
                "omega_ratio":              metrics.omega_ratio,
                "max_drawdown_pct":         metrics.max_drawdown_pct,
                "max_drawdown_duration_bars": metrics.max_drawdown_duration_bars,
                "recovery_factor":          metrics.recovery_factor,
            },
            "trades": {
                "total":          metrics.total_trades,
                "winning":        metrics.winning_trades,
                "losing":         metrics.losing_trades,
                "win_rate":       metrics.win_rate,
                "profit_factor":  metrics.profit_factor,
                "payoff_ratio":   metrics.payoff_ratio,
                "expectancy":     metrics.expectancy,
                "avg_win":        metrics.avg_win,
                "avg_loss":       metrics.avg_loss,
                "largest_win":    metrics.largest_win,
                "largest_loss":   metrics.largest_loss,
                "max_win_streak":  metrics.max_win_streak,
                "max_loss_streak": metrics.max_loss_streak,
                "avg_holding_bars": metrics.avg_holding_bars,
                "total_fees":     metrics.total_fees,
                "total_slippage": metrics.total_slippage,
            },
            "trade_log": [
                {
                    "trade_id":   t.trade_id,
                    "side":       t.side,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "net_pnl":    t.net_pnl,
                    "net_pnl_pct": t.net_pnl_pct,
                    "exit_reason": t.exit_reason,
                    "holding_bars": t.holding_bars,
                }
                for t in result.trades
            ],
        }
