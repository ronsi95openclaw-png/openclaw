"""Out-of-sample / robustness validation for the top-2 strategies
(EMA Trend Retest, Range Breakout) on full-4yr OKX data via the tool's engine.

Three checks:
  1. Parameter robustness  - all grid combos over the full period -> % positive,
     median Sharpe (broad plateau = robust; single peak = overfit risk).
  2. Chronological holdout  - select best params on first 60% (in-sample), then
     evaluate ONLY on the unseen last 40% (out-of-sample). Compare vs defaults.
  3. Per-year breakdown     - default-param Sharpe/return each year (regime check).

Large windows are used on purpose (4yr only) so indicator warmup is a small
fraction of each window; fine-grained rolling WF would distort with 200-bar EMAs.

Usage: python suite/validate.py [1D] [4H]   (default both)
"""
import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engines.crypto import CryptoEngine
from backtest.metrics import calc_bars_per_year

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import EMATrendRetestEngine, RangeBreakoutEngine  # noqa: E402
from run_suite import fetch_spot, PreloadedLoader, START, END  # noqa: E402

GRIDS = {
    "EMA_Trend_Retest": (EMATrendRetestEngine,
                         [{"fast": f, "slow": s} for f, s in
                          [(10, 50), (20, 50), (10, 100), (20, 100), (30, 100)]]),
    "Range_Breakout": (RangeBreakoutEngine,
                       [{"lookback": n} for n in [10, 20, 30, 40, 55]]),
}
DEFAULTS = {"EMA_Trend_Retest": {}, "Range_Breakout": {}}
_TMP = Path("runs/validate/_tmp")


def run(data_map, interval, EngineCls, params):
    if not data_map or any(len(d) < 60 for d in data_map.values()):
        return {}
    config = {"codes": list(data_map), "start_date": START, "end_date": END,
              "source": "okx", "interval": interval, "engine": "daily"}
    bpy = calc_bars_per_year(interval, "okx")
    _TMP.mkdir(parents=True, exist_ok=True)
    eng = CryptoEngine(config)
    with contextlib.redirect_stdout(io.StringIO()):
        m = eng.run_backtest(config, PreloadedLoader(data_map), EngineCls(**params), _TMP, bars_per_year=bpy)
    return m or {}


def _sh(m):
    s = m.get("sharpe")
    return s if s is not None else float("-inf")


def islice(dm, a, b):
    return {c: df.iloc[a:b] for c, df in dm.items()}


def yslice(dm, year):
    return {c: df[df.index.year == year] for c, df in dm.items()}


def validate_interval(interval):
    print(f"\n{'='*72}\nVALIDATION  interval={interval}\n{'='*72}")
    dm = fetch_spot(interval)
    n = min(len(d) for d in dm.values())
    print(f"  bars/symbol={n}  {list(dm.values())[0].index[0].date()} -> {list(dm.values())[0].index[-1].date()}")
    report = {"interval": interval, "robustness": {}, "holdout": {}, "yearly": {}}

    for name, (Cls, grid) in GRIDS.items():
        # 1) robustness over full period
        sharpes, rets = [], []
        for p in grid:
            m = run(dm, interval, Cls, p)
            sharpes.append(_sh(m))
            rets.append(m.get("total_return"))
        valid = [s for s in sharpes if np.isfinite(s)]
        pos = sum(1 for s in valid if s > 0)
        report["robustness"][name] = {
            "combos": len(grid), "pct_positive_sharpe": round(pos / len(grid), 2),
            "median_sharpe": round(float(np.median(valid)), 2) if valid else None,
            "best_sharpe": round(max(valid), 2) if valid else None,
            "worst_sharpe": round(min(valid), 2) if valid else None,
        }

        # 2) chronological holdout: select on first 60%, test on last 40%
        cut = int(n * 0.6)
        is_dm, oos_dm = islice(dm, 0, cut), islice(dm, cut, n)
        best_p, best_s = grid[0], float("-inf")
        for p in grid:
            s = _sh(run(is_dm, interval, Cls, p))
            if s > best_s:
                best_s, best_p = s, p
        m_oos_sel = run(oos_dm, interval, Cls, best_p)
        m_oos_def = run(oos_dm, interval, Cls, DEFAULTS[name])
        report["holdout"][name] = {
            "is_selected_params": best_p, "is_sharpe": round(best_s, 2),
            "oos_sharpe_selected": round(_sh(m_oos_sel), 2),
            "oos_return_selected": m_oos_sel.get("total_return"),
            "oos_maxdd_selected": m_oos_sel.get("max_drawdown"),
            "oos_sharpe_default": round(_sh(m_oos_def), 2),
            "oos_return_default": m_oos_def.get("total_return"),
        }

        # 3) per-year (default params)
        yr = {}
        for y in sorted(set(list(dm.values())[0].index.year)):
            m = run(yslice(dm, y), interval, Cls, DEFAULTS[name])
            if m:
                yr[str(y)] = {"sharpe": round(_sh(m), 2), "return": m.get("total_return"),
                              "maxdd": m.get("max_drawdown"), "trades": m.get("trade_count")}
        report["yearly"][name] = yr

    return report


def fmt(reports):
    out = ["# Out-of-Sample / Robustness Validation", "",
           "Top-2 strategies on full-4yr OKX data, through vibe-trading's CryptoEngine.", ""]
    for rep in reports:
        iv = rep["interval"]
        out.append(f"## {iv}\n")
        out.append("### 1. Parameter robustness (full period)")
        out.append("| Strategy | Combos | % +Sharpe | Median | Best | Worst |")
        out.append("|---|---|---|---|---|---|")
        for s, r in rep["robustness"].items():
            out.append(f"| {s} | {r['combos']} | {int(r['pct_positive_sharpe']*100)}% | "
                       f"{r['median_sharpe']} | {r['best_sharpe']} | {r['worst_sharpe']} |")
        out.append("\n### 2. Chronological holdout (select on first 60%, test on last 40%)")
        out.append("| Strategy | IS-picked params | IS Sharpe | OOS Sharpe (sel) | OOS Ret (sel) | OOS DD | OOS Sharpe (default) |")
        out.append("|---|---|---|---|---|---|---|")
        for s, h in rep["holdout"].items():
            def pc(x):
                return "n/a" if x is None else f"{x*100:.0f}%"
            out.append(f"| {s} | {h['is_selected_params']} | {h['is_sharpe']} | "
                       f"{h['oos_sharpe_selected']} | {pc(h['oos_return_selected'])} | "
                       f"{pc(h['oos_maxdd_selected'])} | {h['oos_sharpe_default']} |")
        out.append("\n### 3. Per-year (default params)")
        for s, yr in rep["yearly"].items():
            out.append(f"\n**{s}**")
            out.append("| Year | Sharpe | Return | MaxDD | Trades |")
            out.append("|---|---|---|---|---|")
            for y, m in yr.items():
                def pc(x):
                    return "n/a" if x is None else f"{x*100:.0f}%"
                out.append(f"| {y} | {m['sharpe']} | {pc(m['return'])} | {pc(m['maxdd'])} | {m['trades']} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    intervals = sys.argv[1:] or ["1D", "4H"]
    reports = [validate_interval(iv) for iv in intervals]
    md = fmt(reports)
    Path("suite/VALIDATION.md").write_text(md, encoding="utf-8")
    Path("suite/validation.json").write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    print("\n" + md)
