"""Paper trading simulator for prediction market strategies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

LOG_FILE = Path(__file__).parent / "paper_trades.md"


@dataclass
class SimulatedTrade:
    timestamp: str
    market_id: str
    side: str
    stake: float
    entry_price: float
    exit_price: float
    outcome: str
    profit_loss: float
    note: str


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def simulate_trade(
    market_id: str,
    side: str,
    stake: float,
    entry_price: float,
    exit_price: float,
    note: str = "",
) -> SimulatedTrade:
    outcome = "win" if (side.lower() == "yes" and exit_price > entry_price) or (side.lower() == "no" and exit_price < entry_price) else "loss"
    profit_loss = stake * ((exit_price - entry_price) if side.lower() == "yes" else (entry_price - exit_price))

    return SimulatedTrade(
        timestamp=_timestamp(),
        market_id=market_id,
        side=side,
        stake=stake,
        entry_price=entry_price,
        exit_price=exit_price,
        outcome=outcome,
        profit_loss=profit_loss,
        note=note,
    )


def log_simulated_trade(trade: SimulatedTrade) -> None:
    entry = [
        f"## {trade.timestamp} | {trade.market_id} | {trade.outcome}",
        f"- Side: {trade.side}",
        f"- Stake: {trade.stake:.2f}",
        f"- Entry price: {trade.entry_price:.4f}",
        f"- Exit price: {trade.exit_price:.4f}",
        f"- P/L: {trade.profit_loss:.2f}",
        f"- Note: {trade.note}",
        "",
    ]
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Paper Trading Log\n\n", encoding="utf-8")
    LOG_FILE.write_text(LOG_FILE.read_text(encoding="utf-8") + "\n".join(entry), encoding="utf-8")


def summarize_trades(trades: List[SimulatedTrade]) -> Dict[str, Any]:
    total_trades = len(trades)
    total_pl = sum(trade.profit_loss for trade in trades)
    wins = sum(1 for trade in trades if trade.outcome == "win")
    losses = total_trades - wins
    win_rate = wins / total_trades if total_trades else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_profit_loss": total_pl,
    }


if __name__ == "__main__":
    print("Prediction market paper trading module loaded.")
