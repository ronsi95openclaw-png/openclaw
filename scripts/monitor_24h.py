#!/usr/bin/env python3
"""24-hour bot monitor — polls /api/status every 60s, writes structured snapshots."""
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE   = Path("/tmp/monitor_24h.jsonl")
REPORT     = Path("/tmp/perf_24h.txt")
INTERVAL   = 60          # poll every 60s
RUN_SECS   = 24 * 3600  # 24 hours

BASE = "http://localhost:8000"

snapshots: list[dict] = []
trade_ids_seen: set[str] = set()
events: list[str] = []
errors: list[str] = []

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def poll() -> dict | None:
    try:
        r = requests.get(f"{BASE}/api/status", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        errors.append(f"{ts()} poll_error: {e}")
        return None

def write_report(snap_list: list[dict]):
    if not snap_list:
        return
    first  = snap_list[0]
    last   = snap_list[-1]
    closed = [s for s in snap_list if s.get("trade_log")]

    # Collect all unique closed trades from trade_log snapshots
    all_closed: dict[str, dict] = {}
    for s in snap_list:
        for t in s.get("trade_log", []):
            tid = t.get("id", "")
            if tid and tid not in all_closed:
                all_closed[tid] = t

    wins   = [t for t in all_closed.values() if t.get("outcome") == "win"]
    losses = [t for t in all_closed.values() if t.get("outcome") == "loss"]
    total  = len(all_closed)
    wr     = round(len(wins) / total * 100, 1) if total else 0.0
    gross  = sum(t.get("pnl", 0) for t in all_closed.values())

    strat_stats: dict[str, dict] = {}
    for t in all_closed.values():
        s = t.get("strategy", "?")
        if s not in strat_stats:
            strat_stats[s] = {"wins": 0, "losses": 0, "pnl": 0.0}
        strat_stats[s]["wins"   if t.get("outcome") == "win" else "losses"] += 1
        strat_stats[s]["pnl"] += t.get("pnl", 0)

    lines = [
        "=" * 60,
        f"  OpenClaw 24h Performance Report",
        f"  Generated: {ts()}",
        "=" * 60,
        f"  Start balance : ${first.get('balance', 0):.2f}",
        f"  Current PnL   : ${last.get('total_pnl', 0):+.2f}",
        f"  Unrealized    : ${last.get('unrealized_pnl', 0):+.2f}",
        f"  Capital state : {last.get('capital_state', '?')}",
        f"  Open positions: {len(last.get('open_positions', []))}",
        "",
        f"  Closed trades : {total}  (W:{len(wins)} / L:{len(losses)})  WR: {wr}%",
        f"  Gross PnL     : ${gross:+.2f}",
        "",
        "  Per-strategy breakdown:",
    ]
    for strat, st in sorted(strat_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        t2 = st["wins"] + st["losses"]
        wr2 = round(st["wins"] / t2 * 100, 1) if t2 else 0
        lines.append(f"    {strat:<20} W:{st['wins']} L:{st['losses']}  WR:{wr2}%  PnL:${st['pnl']:+.2f}")

    lines += [
        "",
        f"  Strategy weights (live):",
    ]
    for strat, w in last.get("strategy_weights", {}).items():
        lines.append(f"    {strat:<20} weight={w.get('weight',0):.3f}  trades={w.get('trades',0)}")

    if events:
        lines += ["", "  Notable events (latest 20):"]
        for e in events[-20:]:
            lines.append(f"    {e}")

    if errors:
        lines += ["", f"  Errors ({len(errors)} total, latest 5):"]
        for e in errors[-5:]:
            lines.append(f"    {e}")

    lines.append("=" * 60)
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text)
    print(text)

def detect_events(prev: dict | None, curr: dict):
    if not prev:
        return
    prev_ids = {p["id"] for p in prev.get("open_positions", [])}
    curr_ids = {p["id"] for p in curr.get("open_positions", [])}
    # New opens
    for pos in curr.get("open_positions", []):
        if pos["id"] not in prev_ids:
            events.append(f"{ts()} OPEN {pos['side'].upper()} {pos['symbol']} [{pos['strategy']}] "
                          f"@ {pos['entry_price']:.4f}")
    # Closes (compare with trade log)
    curr_log_ids = {t["id"] for t in curr.get("trade_log", [])}
    prev_log_ids = {t["id"] for t in prev.get("trade_log", [])}
    for t in curr.get("trade_log", []):
        if t["id"] not in prev_log_ids:
            events.append(f"{ts()} CLOSE {t['side'].upper()} {t['symbol']} [{t['strategy']}] "
                          f"@ {t.get('exit_price',0):.4f}  PnL:{t.get('pnl',0):+.4f}  [{t['outcome'].upper()}]")
    # Capital state changes
    if prev.get("capital_state") != curr.get("capital_state"):
        events.append(f"{ts()} CAPITAL_STATE {prev.get('capital_state')} → {curr.get('capital_state')}")
    # Partial TP
    for pos in curr.get("open_positions", []):
        for prev_pos in prev.get("open_positions", []):
            if pos["id"] == prev_pos["id"]:
                if pos.get("partial_tp_taken") and not prev_pos.get("partial_tp_taken"):
                    events.append(f"{ts()} PARTIAL_TP {pos['symbol']} [{pos['strategy']}] "
                                  f"SL→{pos['sl_price']:.4f}")

def main():
    print(f"[monitor_24h] Starting — will run for 24 hours. Report at {REPORT}")
    print(f"[monitor_24h] Log: {LOG_FILE}")
    start = time.time()
    prev  = None
    last_report_h = -1
    i = 0

    while True:
        elapsed = time.time() - start
        if elapsed > RUN_SECS:
            print(f"\n[monitor_24h] 24-hour run complete.")
            break

        status = poll()
        if status:
            snap = {"elapsed_s": int(elapsed), "ts": ts(), **status}
            snapshots.append(snap)
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(snap) + "\n")
            detect_events(prev, status)
            prev = status

        # Print live update every 10 polls (~10 min)
        if i % 10 == 0:
            if status:
                pnl     = status.get("total_pnl", 0)
                u_pnl   = status.get("unrealized_pnl", 0)
                n_pos   = len(status.get("open_positions", []))
                capital = status.get("capital_state", "?")
                elapsed_h = elapsed / 3600
                print(f"[{elapsed_h:5.2f}h] PnL={pnl:+.2f}  uPnL={u_pnl:+.2f}  "
                      f"pos={n_pos}  capital={capital}  "
                      f"events={len(events)}  errors={len(errors)}")

        # Full report every hour
        current_h = int(elapsed / 3600)
        if current_h > last_report_h:
            write_report(snapshots)
            last_report_h = current_h

        i += 1
        time.sleep(INTERVAL)

    write_report(snapshots)
    print(f"[monitor_24h] Final report written to {REPORT}")

if __name__ == "__main__":
    main()
