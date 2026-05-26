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


def format_eligibility_report() -> str:
    """Returns a Telegram-formatted eligibility report."""
    from settings import LIVE_ACTIVATION_PASSPHRASE
    eligible, failures = check_live_mode_eligibility()

    paper_count = len(_load_paper_trades())

    if eligible:
        return (
            "✅ <b>ALL LIVE MODE REQUIREMENTS MET</b>\n\n"
            f"Paper trades: {paper_count}\n\n"
            "To activate live trading, send:\n"
            f"<code>/golive {LIVE_ACTIVATION_PASSPHRASE}</code>\n\n"
            "⚠️ <b>This will use REAL money.</b> "
            "Double-check your API keys and risk settings first."
        )
    else:
        return (
            "🚫 <b>LIVE MODE NOT READY</b>\n\n"
            f"Paper trades so far: {paper_count}\n\n"
            "Failed requirements:\n"
            + "\n".join(failures)
            + "\n\nContinue paper trading until all requirements are met."
        )


def _load_paper_trades() -> list:
    """Load paper (demo=True) trades from local JSONL file."""
    try:
        from infra.state_store import load_trade_outcomes
        all_trades = load_trade_outcomes()
        return [t for t in all_trades if t.get("demo", True)]
    except Exception:
        return []
