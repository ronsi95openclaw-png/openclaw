#!/usr/bin/env python3
"""track_record.py - read-only paper-trading track record report for Lucid 25K eval.

Prints a human-readable summary of the bot's paper trade journal: win rate,
P&L, profitable-day count vs mandate minimum, the 50% consistency check, and
the CURRENT eval_gate.py verdict (called directly, never re-implemented, so
this report can never drift out of sync with the real live-gate).

Read-only. Never writes to trade_journal.jsonl, decisions.jsonl,
strategy.py, risk_guard.py, or config.py.

Usage:
  python track_record.py                 - full report against the real journal
  python track_record.py <journal.jsonl> [mandate.json]   - point at other files
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BOT_DIR = Path(__file__).resolve().parent
VIBE_DIR = BOT_DIR.parent

# Reuse eval_gate.py's own loaders/verdict so the numbers here can never
# diverge from what actually gates the live switch.
import eval_gate  # noqa: E402

DEFAULT_JOURNAL = BOT_DIR / "logs" / "trade_journal.jsonl"
DEFAULT_MANDATE = VIBE_DIR / "lucid_mandate.json"


def _fmt_money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _by_day(trades: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        d = t.get("date")
        pnl = t.get("pnl")
        if d is None or not isinstance(pnl, (int, float)):
            continue
        out[d] = out.get(d, 0.0) + float(pnl)
    return out


def build_report(journal_path: Path, mandate_path: Path) -> str:
    lines: list[str] = []

    def p(s: str = "") -> None:
        lines.append(s)

    p("=" * 72)
    p("LUCID 25K PAPER TRACK RECORD")
    p("=" * 72)
    p(f"journal : {journal_path}")
    p(f"mandate : {mandate_path}")
    p()

    # ---- Load mandate (fail closed, but keep going so report never crashes) --
    try:
        mandate = eval_gate._load_mandate(mandate_path)
        rules = mandate.get("rules", {}) or {}
    except Exception as exc:
        p(f"[!] could not read mandate: {exc}")
        rules = {}

    min_profitable_days = rules.get("payout_min_profitable_days")
    consistency_frac = rules.get("consistency_rule_eval")
    max_loss_limit = rules.get("max_loss_limit")

    # ---- Load trades (best-effort, missing/empty file -> []) -----------------
    try:
        trades = eval_gate._load_trade_journal(Path(journal_path))
    except Exception as exc:
        p(f"[!] could not read trade journal: {exc}")
        trades = []

    n = len(trades)
    p("-" * 72)
    p("SAMPLE")
    p("-" * 72)
    if n == 0:
        p("total paper trades logged : 0")
        p("date range covered        : N/A (no trades yet)")
    else:
        dated = [t for t in trades if t.get("date")]
        dates = sorted(t["date"] for t in dated) if dated else []
        p(f"total paper trades logged : {n}")
        if dates:
            p(f"date range covered        : {dates[0]} -> {dates[-1]}")
        else:
            p("date range covered        : N/A (no dated rows)")

    # ---- Win rate / P&L --------------------------------------------------------
    p()
    p("-" * 72)
    p("PERFORMANCE")
    p("-" * 72)
    priced = [t for t in trades if isinstance(t.get("pnl"), (int, float))]
    if not priced:
        p("win rate      : N/A (no priced trades yet)")
        p("total P&L     : N/A")
        p("avg win       : N/A")
        p("avg loss      : N/A")
    else:
        wins = [t for t in priced if t["pnl"] > 0]
        losses = [t for t in priced if t["pnl"] <= 0]
        total = sum(t["pnl"] for t in priced)
        wr = len(wins) / len(priced) * 100
        avg_win = (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.0
        p(f"win rate      : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
        p(f"total P&L     : {_fmt_money(total)}")
        p(f"avg win       : {_fmt_money(avg_win) if wins else 'N/A'}")
        p(f"avg loss      : {_fmt_money(avg_loss) if losses else 'N/A'}")

    # ---- Profitable days vs mandate minimum -----------------------------------
    p()
    p("-" * 72)
    p("PROFITABLE DAYS")
    p("-" * 72)
    byday = _by_day(trades)
    if not byday:
        p("profitable days : N/A (no dated/priced trades yet)")
        if min_profitable_days is not None:
            p(f"mandate requires : >= {int(min_profitable_days)} profitable day(s)")
    else:
        profitable_dates = sorted(d for d, v in byday.items() if v > 0)
        losing_dates = sorted(d for d, v in byday.items() if v <= 0)
        req = f">= {int(min_profitable_days)}" if min_profitable_days is not None else "N/A (mandate has no rule)"
        p(f"profitable days : {len(profitable_dates)} / {len(byday)} traded day(s)  (mandate requires {req})")
        if profitable_dates:
            p(f"  profitable : {', '.join(f'{d} ({_fmt_money(byday[d])})' for d in profitable_dates)}")
        if losing_dates:
            p(f"  losing     : {', '.join(f'{d} ({_fmt_money(byday[d])})' for d in losing_dates)}")

    # ---- Consistency check (best day % of total profit) ----------------------
    p()
    p("-" * 72)
    p("CONSISTENCY (50% rule)")
    p("-" * 72)
    total_pnl = sum(byday.values()) if byday else 0.0
    if not byday or total_pnl <= 0:
        p("consistency check : N/A (no aggregate profit yet to evaluate)")
    else:
        best_date = max(byday, key=byday.get)
        best_val = byday[best_date]
        pct = (best_val / total_pnl * 100) if total_pnl else 0.0
        limit_frac = float(consistency_frac) if consistency_frac is not None else 0.5
        limit_val = total_pnl * limit_frac
        verdict = "BREACH" if best_val > limit_val else "OK"
        p(f"best day       : {best_date}  {_fmt_money(best_val)}  ({pct:.1f}% of total profit)")
        p(f"limit          : {limit_frac:.0%} of total profit = {_fmt_money(limit_val)}")
        p(f"verdict        : {verdict}")

    # ---- Max trailing drawdown (context, mirrors eval_gate's own check) ------
    if max_loss_limit is not None and priced:
        ordered = sorted(
            priced,
            key=lambda t: (eval_gate._parse_ts(t.get("ts")) or eval_gate.datetime.min),
        )
        cumulative = 0.0
        peak = 0.0
        worst_dd = 0.0
        for t in ordered:
            cumulative += float(t["pnl"])
            peak = max(peak, cumulative)
            worst_dd = max(worst_dd, peak - cumulative)
        p()
        p("-" * 72)
        p("MAX TRAILING DRAWDOWN")
        p("-" * 72)
        p(f"worst trailing drawdown : {_fmt_money(worst_dd)}  (mandate limit {_fmt_money(float(max_loss_limit))})")

    # ---- Trade-by-trade / day-by-day table ------------------------------------
    p()
    p("-" * 72)
    p("DETAIL")
    p("-" * 72)
    if n == 0:
        p("no trades yet, N/A")
    elif n <= 50:
        p(f"{'date':<12} {'side':<6} {'instrument':<10} {'entry':>10} {'exit':>10} {'pnl':>12}")
        for t in sorted(trades, key=lambda t: (t.get("date") or "", t.get("ts") or "")):
            entry = t.get("entry")
            exit_ = t.get("exit")
            pnl = t.get("pnl")
            p(
                f"{str(t.get('date') or 'N/A'):<12} "
                f"{str(t.get('side') or 'N/A'):<6} "
                f"{str(t.get('instrument') or 'N/A'):<10} "
                f"{(f'{entry:.2f}' if isinstance(entry, (int, float)) else 'N/A'):>10} "
                f"{(f'{exit_:.2f}' if isinstance(exit_, (int, float)) else 'N/A'):>10} "
                f"{(_fmt_money(pnl) if isinstance(pnl, (int, float)) else 'N/A'):>12}"
            )
    else:
        p(f"{n} trades logged (> 50) — showing day-by-day instead of trade-by-trade:")
        p(f"{'date':<12} {'pnl':>12}")
        for d in sorted(byday):
            p(f"{d:<12} {_fmt_money(byday[d]):>12}")

    # ---- eval_gate.py verdict (single source of truth) ------------------------
    p()
    p("=" * 72)
    p("EVAL_GATE.PY VERDICT (live-gate — this is the authoritative pass/fail)")
    p("=" * 72)
    try:
        ok, reasons = eval_gate.passed_evaluation(journal_path, mandate_path)
        p(f"passed_evaluation() -> {ok}")
        if reasons:
            for r in reasons:
                p(f"  - {r}")
        else:
            p("  (all checks passed)")
    except Exception as exc:
        p(f"[!] eval_gate.passed_evaluation() raised unexpectedly: {exc}")
        p("  (this itself is a bug — passed_evaluation() is documented to never raise)")

    p()
    p("(paper track record — informational only; mode is PAPER per lucid_mandate.json)")

    return "\n".join(lines)


def main() -> int:
    journal_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JOURNAL
    mandate_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MANDATE
    print(build_report(journal_path, mandate_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
