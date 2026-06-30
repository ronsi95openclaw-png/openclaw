"""
send_setups.py — Run all ClawBot strategies on live 4h candles and
send a formatted trade-setup card to Telegram (Liquid-paste ready).

Usage (from Claude-openclaw root):
    python send_setups.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=False)

import requests

# ── Project imports ───────────────────────────────────────────────────────────
from trading.exchange import fetch_all_closes, fetch_ticker_price
from trading.strategies.liquidity_sweep import LiquiditySweepStrategy
from trading.strategies.ema_momentum import EmaMomentumStrategy

COINS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT"]
TIMEFRAME = "4h"
CANDLES   = 120   # enough warmup for both strategies


def pct(base: float, p: float) -> float:
    return round(base * (1 + p / 100), 6)


def build_setup_card(coin: str, closes: list[float]) -> str | None:
    """
    Run both strategies and return a Liquid-paste card for any BUY/SELL signal.
    Returns None if both strategies say HOLD.
    """
    liq = LiquiditySweepStrategy()
    ema = EmaMomentumStrategy()

    sig_liq = liq.evaluate(coin, closes)
    sig_ema = ema.evaluate(coin, closes)

    # Pick the higher-confidence actionable signal
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    best = max([sig_liq, sig_ema],
               key=lambda s: order[s.confidence] if s.action != "HOLD" else 0)

    if best.action == "HOLD":
        return None

    try:
        price = fetch_ticker_price(coin)
    except Exception:
        price = closes[-1]   # fallback to last close

    pair   = coin.replace("_", "/")       # BTC_USDT -> BTC/USDT
    strat  = "Liq.Sweep" if best is sig_liq else "EMA Mom"
    conf   = best.confidence
    action = best.action

    # SL / TP  (2 % / 4 % — adjust manually before entry)
    if action == "BUY":
        sl = pct(price, -2.0)
        tp = pct(price, +4.0)
        emoji = "🟢 LONG"
    else:
        sl = pct(price, +2.0)
        tp = pct(price, -4.0)
        emoji = "🔴 SHORT"

    rr = round(abs(tp - price) / abs(price - sl), 2) if price != sl else 0.0

    lines = [
        f"{emoji}  {pair}",
        f"Strategy  : {strat}  [{conf}]",
        f"Entry     : {price:,.4f}",
        f"SL        : {sl:,.4f}  (-2%)",
        f"TP        : {tp:,.4f}  (+4%)",
        f"R:R       : {rr:.1f} : 1",
        f"Setup     : {best.reason}",
    ]

    # Show both signals if they diverge
    if sig_liq.action != sig_ema.action and sig_ema.action != "HOLD" and sig_liq.action != "HOLD":
        lines.append(f"⚠️  Conflicting: Liq={sig_liq.action}[{sig_liq.confidence}]"
                     f" / EMA={sig_ema.action}[{sig_ema.confidence}]")

    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[WARN] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — printing only.")
        print(text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    r.raise_for_status()
    print(f"[OK] sent to Telegram (chat {chat_id})")


def main() -> None:
    print(f"Fetching {TIMEFRAME} candles for {COINS} ...")
    all_closes = fetch_all_closes(COINS, timeframe=TIMEFRAME, count=CANDLES)

    cards = []
    for coin in COINS:
        closes = all_closes.get(coin)
        if not closes:
            print(f"  {coin}: no data - skipped")
            continue
        card = build_setup_card(coin, closes)
        if card:
            cards.append(card)
            print(f"  {coin}: signal generated")
        else:
            print(f"  {coin}: HOLD - skipped")

    if not cards:
        msg = "📭 No active setups right now - all coins HOLD on 4h."
        print(msg)
        send_telegram(msg)
        return

    header = f"🤖 ClawBot Setups - {TIMEFRAME.upper()}  ({len(cards)} signal{'s' if len(cards)>1 else ''})\n" + "-"*32
    body   = ("\n\n" + "-"*32 + "\n\n").join(cards)
    footer = (
        "\n\n" + "-"*32 +
        "\n[MANUAL ONLY] bot does NOT place orders."
        "\nSize at 3% risk:  qty = (0.03 x equity) / |entry - SL|"
        "\nPaste entry/SL/TP into Liquid and confirm yourself."
    )

    full_msg = header + "\n\n" + body + footer
    print("\n" + full_msg + "\n")
    send_telegram(full_msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
