#!/usr/bin/env python3
"""
paper_ledger.py — Persistent paper trading position tracker.

Bridges --once runs: saves approved signals to disk, resolves TP1/TP2/SL fills
on subsequent bar scans. Each vibe_paper_scan.py run calls auto_resolve() with
the latest bar before scanning for new signals.

File layout:
  bot/logs/paper_positions.json  — open positions (list of dicts)
  bot/logs/trade_journal.jsonl   — closed trade log (shared with trade_journal.py)
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
POSITIONS_FILE = os.path.join(LOGS, "paper_positions.json")
JOURNAL_FILE = os.path.join(LOGS, "trade_journal.jsonl")

_POINT_VALUES = {"ES": 50.0, "MES": 5.0, "NQ": 20.0, "MNQ": 2.0}
_TICK_SIZE = {"ES": 0.25, "MES": 0.25, "NQ": 0.25, "MNQ": 0.25}


# ── I/O helpers ────────────────────────────────────────────────────────────────

def _ensure_logs():
    os.makedirs(LOGS, exist_ok=True)


def load_positions() -> list:
    _ensure_logs()
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_positions(positions: list) -> None:
    _ensure_logs()
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2)


def _append_journal(record: dict) -> None:
    _ensure_logs()
    with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ── Core logic ─────────────────────────────────────────────────────────────────

def open_position(signal: dict) -> None:
    """Save an approved signal as a new open paper position.

    signal must have: side, instrument, entry, stop, tp1, tp2, size, ts
    """
    positions = load_positions()
    pos = {
        "side": signal["side"],
        "instrument": signal.get("instrument", "ES"),
        "size": int(signal.get("size", 1)),
        "entry": float(signal["entry"]),
        "stop": float(signal["stop"]),
        "tp1": float(signal.get("tp1", math.nan)),
        "tp2": float(signal.get("tp2", math.nan)),
        "tp1_filled": False,
        "size_remaining": int(signal.get("size", 1)),
        "opened_at": signal.get("ts", datetime.now(timezone.utc).isoformat()),
        "strategy_reason": signal.get("reason", ""),
    }
    positions.append(pos)
    save_positions(positions)


def _pnl(pos: dict, fill_price: float, contracts: int) -> float:
    pv = _POINT_VALUES.get(pos["instrument"], 1.0)
    direction = 1 if pos["side"] == "long" else -1
    return direction * (fill_price - pos["entry"]) * pv * contracts


def resolve_bar(pos: dict, bar_high: float, bar_low: float,
                bar_close: float, bar_time: str) -> tuple:
    """Resolve a single position against one OHLC bar.

    Returns (updated_pos_or_None, messages).
    updated_pos=None means position is fully closed.
    Conservative priority: stop fills before TP (fail-safe when bar straddles both).
    """
    msgs = []
    side = pos["side"]
    instrument = pos["instrument"]
    size_rem = pos.get("size_remaining", pos.get("size", 1))

    if side == "long":
        stop_hit = bar_low <= pos["stop"]
        tp1_hit = (not pos.get("tp1_filled", False)
                   and math.isfinite(pos.get("tp1", math.nan))
                   and bar_high >= pos["tp1"])
        tp2_hit = (pos.get("tp1_filled", False)
                   and math.isfinite(pos.get("tp2", math.nan))
                   and bar_high >= pos["tp2"])
    else:  # short
        stop_hit = bar_high >= pos["stop"]
        tp1_hit = (not pos.get("tp1_filled", False)
                   and math.isfinite(pos.get("tp1", math.nan))
                   and bar_low <= pos["tp1"])
        tp2_hit = (pos.get("tp1_filled", False)
                   and math.isfinite(pos.get("tp2", math.nan))
                   and bar_low <= pos["tp2"])

    # SL takes priority (fail-safe)
    if stop_hit:
        fill_px = pos["stop"]
        pnl = _pnl(pos, fill_px, size_rem)
        msgs.append(
            f"STOP HIT [{instrument} {side.upper()}] entry {pos['entry']} -> stop {fill_px:.2f} "
            f"({size_rem}ct) P&L: ${pnl:+.2f} | {bar_time}"
        )
        _append_journal({
            "ts": bar_time, "date": bar_time[:10],
            "source": "paper_ledger",
            "side": side, "instrument": instrument,
            "entry": pos["entry"], "exit": fill_px,
            "contracts": size_rem, "pnl": round(pnl, 2),
            "outcome": "SL",
            "opened_at": pos.get("opened_at", ""),
        })
        return None, msgs  # fully closed

    if tp2_hit:
        fill_px = pos["tp2"]
        pnl = _pnl(pos, fill_px, size_rem)
        msgs.append(
            f"TP2 HIT [{instrument} {side.upper()}] entry {pos['entry']} -> TP2 {fill_px:.2f} "
            f"({size_rem}ct) P&L: ${pnl:+.2f} | {bar_time}"
        )
        _append_journal({
            "ts": bar_time, "date": bar_time[:10],
            "source": "paper_ledger",
            "side": side, "instrument": instrument,
            "entry": pos["entry"], "exit": fill_px,
            "contracts": size_rem, "pnl": round(pnl, 2),
            "outcome": "TP2",
            "opened_at": pos.get("opened_at", ""),
        })
        return None, msgs  # fully closed

    if tp1_hit:
        # Half out at TP1, remainder runs to TP2
        half = max(1, size_rem // 2)
        fill_px = pos["tp1"]
        pnl = _pnl(pos, fill_px, half)
        msgs.append(
            f"TP1 HIT [{instrument} {side.upper()}] entry {pos['entry']} -> TP1 {fill_px:.2f} "
            f"({half}ct of {size_rem}) P&L: ${pnl:+.2f} | {bar_time} — runner to TP2"
        )
        _append_journal({
            "ts": bar_time, "date": bar_time[:10],
            "source": "paper_ledger",
            "side": side, "instrument": instrument,
            "entry": pos["entry"], "exit": fill_px,
            "contracts": half, "pnl": round(pnl, 2),
            "outcome": "TP1",
            "opened_at": pos.get("opened_at", ""),
        })
        pos = dict(pos)  # copy
        pos["tp1_filled"] = True
        pos["size_remaining"] = size_rem - half
        return pos, msgs  # position still open (remainder)

    return pos, msgs  # no fill this bar


def auto_resolve(bar_ohlc: dict, instrument: str = "ES") -> list:
    """Check all open positions against bar_ohlc = {high, low, close, ts}.

    Returns list of fill message strings. Call this at the top of each paper scan
    BEFORE looking for new signals.
    """
    positions = load_positions()
    if not positions:
        return []

    bar_high = float(bar_ohlc["high"])
    bar_low = float(bar_ohlc["low"])
    bar_close = float(bar_ohlc.get("close", bar_ohlc["high"]))
    bar_time = str(bar_ohlc.get("ts", ""))

    remaining = []
    all_msgs = []
    for pos in positions:
        updated, msgs = resolve_bar(pos, bar_high, bar_low, bar_close, bar_time)
        all_msgs.extend(msgs)
        if updated is not None:
            remaining.append(updated)

    save_positions(remaining)
    return all_msgs


def status() -> None:
    """Print open positions to stdout."""
    positions = load_positions()
    if not positions:
        print("No open paper positions.")
        return
    print(f"Open paper positions: {len(positions)}")
    for p in positions:
        print(f"  {p['instrument']} {p['side'].upper()} "
              f"entry={p['entry']} stop={p['stop']} "
              f"tp1={p.get('tp1','?')} tp2={p.get('tp2','?')} "
              f"size={p.get('size_remaining', p.get('size',1))}ct "
              f"opened={p.get('opened_at','?')[:16]}")


def flatten_all(reason: str = "manual_flatten") -> None:
    """EOD flatten — clear all open positions without P&L (no fill recorded)."""
    positions = load_positions()
    if positions:
        print(f"Flattening {len(positions)} open position(s) — {reason}.")
    save_positions([])


def resolve_from_csv(csv_path: str, instrument: str = "ES") -> list:
    """Read last bar from csv_path and auto-resolve all open positions.
    Returns fill messages. Handles NinjaTrader and yfinance CSV formats.
    """
    import csv as _csv
    bars = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = _csv.reader(f)
            rows = list(reader)
        # detect yfinance (header starts with Datetime)
        is_yf = rows and rows[0][0].strip().lower().startswith("datetime")
        for row in rows:
            if not row or row[0].strip().lower() in ("date", "datetime", ""):
                continue
            try:
                if is_yf:
                    from datetime import datetime as _dt
                    ts = str(_dt.fromisoformat(row[0].strip()))
                    h, l, c = float(row[2]), float(row[3]), float(row[4])
                else:
                    ts = f"{row[0].strip()} {row[1].strip()}"
                    h, l, c = float(row[3]), float(row[4]), float(row[5])
                bars.append({"ts": ts, "high": h, "low": l, "close": c})
            except (ValueError, IndexError):
                continue
    except Exception as exc:
        return [f"paper_ledger: csv read error: {exc}"]

    if not bars:
        return []
    last = bars[-1]
    return auto_resolve(last, instrument)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("flatten")
    r = sub.add_parser("resolve")
    r.add_argument("--csv", required=True)
    r.add_argument("--instrument", default="ES")
    args = parser.parse_args()

    if args.cmd == "resolve":
        msgs = resolve_from_csv(args.csv, args.instrument)
        if msgs:
            for m in msgs:
                print(m)
        else:
            print("No fills on latest bar.")
    elif args.cmd == "flatten":
        flatten_all("manual_flatten")
    else:
        status()
