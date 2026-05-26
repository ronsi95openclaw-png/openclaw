"""Live mode activation gate — enforces all pre-flight requirements before
allowing DEMO_MODE=false. Called by /livecheck and /golive Telegram commands.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Tuple, List

logger = logging.getLogger("openclaw.runtime.live_mode_gate")


@dataclass
class LiveModeRequirements:
    min_paper_trades:     int   = 30
    min_win_rate:         float = 0.54
    capital_state_req:    str   = "SAFE"
    slippage_sim_req:     bool  = True


def check_live_mode_eligibility() -> Tuple[bool, List[str]]:
    """Returns (eligible: bool, failed_checks: list[str])."""
    reqs     = LiveModeRequirements()
    failures = []

    # Check 1: minimum paper trades
    paper_trades = _load_paper_trades()
    if len(paper_trades) < reqs.min_paper_trades:
        failures.append(
            f"❌ Paper trades: {len(paper_trades)}/{reqs.min_paper_trades} required"
        )

    # Check 2: win rate
    if paper_trades:
        wins     = sum(1 for t in paper_trades if t.get("outcome") == "win")
        win_rate = wins / len(paper_trades)
        if win_rate < reqs.min_win_rate:
            failures.append(
                f"❌ Win rate: {win_rate:.1%} (need {reqs.min_win_rate:.1%})"
            )
    else:
        failures.append(f"❌ Win rate: N/A (no paper trades)")

    # Check 3: capital state must be SAFE
    try:
        from infra.state_store import load_capital_state
        cap = load_capital_state() or {}
        cap_state = cap.get("state", "UNKNOWN")
    except Exception:
        cap_state = "UNKNOWN"
    if cap_state != reqs.capital_state_req:
        failures.append(
            f"❌ Capital state: {cap_state} (need {reqs.capital_state_req})"
        )

    # Check 4: slippage simulation must be active
    try:
        from settings import DEMO_SLIPPAGE_PCT
        if DEMO_SLIPPAGE_PCT <= 0:
            failures.append(
                "❌ Slippage simulation not enabled (DEMO_SLIPPAGE_PCT must be > 0)"
            )
    except Exception:
        failures.append("❌ settings.py unavailable — cannot verify slippage config")

    return len(failures) == 0, failures


def _pbar(current: float, total: float, width: int = 8) -> str:
    ratio  = min(current / total, 1.0) if total > 0 else 0.0
    filled = int(width * ratio)
    done   = "✅" if ratio >= 1.0 else f"{int(ratio*100)}%"
    return f"[{'█' * filled}{'░' * (width - filled)}] {done}"


def _wr_bar(win_rate: float, target: float, width: int = 8) -> str:
    """Progress bar for win rate — label shows actual WR, not % toward goal."""
    ratio  = min(win_rate / target, 1.0) if target > 0 else 0.0
    filled = int(width * ratio)
    label  = "✅" if win_rate >= target else f"{win_rate:.0%}"
    return f"[{'█' * filled}{'░' * (width - filled)}] {label}"


def format_eligibility_report() -> str:
    """Returns a Telegram-formatted eligibility report with progress bars."""
    from settings import LIVE_ACTIVATION_PASSPHRASE
    reqs     = LiveModeRequirements()
    eligible, failures = check_live_mode_eligibility()

    trades      = _load_paper_trades()
    paper_count = len(trades)
    wins        = sum(1 for t in trades if t.get("outcome") == "win")
    win_rate    = wins / paper_count if paper_count else 0.0

    try:
        from infra.state_store import load_capital_state
        cap_state = (load_capital_state() or {}).get("state", "UNKNOWN")
    except Exception:
        cap_state = "UNKNOWN"

    try:
        from settings import DEMO_SLIPPAGE_PCT
        slip_ok = DEMO_SLIPPAGE_PCT > 0
    except Exception:
        slip_ok = False

    header = "✅ <b>LIVE MODE READY</b>" if eligible else "🚫 <b>LIVE MODE NOT READY</b>"
    lines = [
        header,
        "──────────────────────",
        f"Trades: {_pbar(paper_count, reqs.min_paper_trades)} {paper_count}/{reqs.min_paper_trades}",
        f"WR:     {_wr_bar(win_rate, reqs.min_win_rate)} (need {reqs.min_win_rate:.0%})",
        f"Capital:{_pbar(1 if cap_state == 'SAFE' else 0, 1)} {cap_state}",
        f"Slip:   {_pbar(1 if slip_ok else 0, 1)} {'Active' if slip_ok else 'Off'}",
        "──────────────────────",
    ]
    if eligible:
        lines.append("To go live:\n"
                     f"<code>/golive {LIVE_ACTIVATION_PASSPHRASE}</code>\n"
                     "⚠️ Real money at risk — verify API keys first.")
    else:
        needed = reqs.min_paper_trades - paper_count
        if needed > 0:
            lines.append(f"Need {needed} more paper trades to qualify.")
        lines.append("Continue paper trading until all bars are green.")
    return "\n".join(lines)


def _load_paper_trades() -> list:
    """Load paper (demo=True) trades from local JSONL file."""
    try:
        from infra.state_store import load_trade_outcomes
        all_trades = load_trade_outcomes()
        return [t for t in all_trades if t.get("demo", True)]
    except Exception:
        return []
