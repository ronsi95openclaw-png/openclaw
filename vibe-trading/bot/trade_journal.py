"""trade_journal.py - log trades + review with Lucid win-rate & 50% consistency check.

Usage (pandas venv not required; stdlib only):
  log <long|short> <instrument> <entry> <exit> <pnl> [note...]   - append a trade
  today                                                          - today's trades + P&L vs limits
  review [days]                                                  - win rate, P&L, consistency (default 30d)

Stores JSONL at bot/logs/trade_journal.jsonl. PAPER journal — informational only, no orders.
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))         # ...\vibe-trading\bot
VT = os.path.dirname(HERE)
LOGS = os.path.join(HERE, "logs")
JOURNAL = os.path.join(LOGS, "trade_journal.jsonl")
MANDATE = os.path.join(VT, "lucid_mandate.json")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _mandate():
    try:
        with open(MANDATE, encoding="utf-8") as f:
            return json.load(f).get("rules", {})
    except Exception:
        return {}


def _load():
    out = []
    if os.path.exists(JOURNAL):
        for line in open(JOURNAL, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def log(args):
    if len(args) < 5:
        print("usage: log <long|short> <instrument> <entry> <exit> <pnl> [note]")
        return
    rec = {
        "ts": datetime.datetime.now().astimezone().isoformat(),
        "date": datetime.date.today().isoformat(),
        "side": args[0].lower(),
        "instrument": args[1].upper(),
        "entry": float(args[2]),
        "exit": float(args[3]),
        "pnl": float(args[4]),
        "note": " ".join(args[5:]) if len(args) > 5 else "",
    }
    os.makedirs(LOGS, exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"logged: {rec['side']} {rec['instrument']} entry {rec['entry']} "
          f"exit {rec['exit']} P&L ${rec['pnl']:.2f}")


def today():
    d = datetime.date.today().isoformat()
    rows = [t for t in _load() if t.get("date") == d]
    pnl = sum(t["pnl"] for t in rows)
    rules = _mandate()
    cap = rules.get("daily_trade_cap")
    mll = rules.get("max_loss_limit")
    print(f"=== Today ({d}) ===")
    print(f"trades: {len(rows)}" + (f" / {cap} cap" if cap else ""))
    print(f"P&L today: ${pnl:.2f}")
    if mll:
        print(f"  vs max daily loss -${mll}: {'OK' if pnl > -float(mll) else 'BREACH'}")
    for t in rows:
        print(f"  - {t['side']} {t['instrument']} {t['entry']}->{t['exit']} "
              f"${t['pnl']:.2f} {t.get('note', '')}")


def review(args):
    days = int(args[0]) if args and str(args[0]).isdigit() else 30
    rows = _load()
    if not rows:
        print("no trades logged yet. Log one with: trade_journal.py log long ES 4750 4762 200")
        return
    wins = [t for t in rows if t["pnl"] > 0]
    losses = [t for t in rows if t["pnl"] <= 0]
    total = sum(t["pnl"] for t in rows)
    wr = (len(wins) / len(rows) * 100) if rows else 0
    byday = {}
    for t in rows:
        byday[t["date"]] = byday.get(t["date"], 0) + t["pnl"]
    best_day = max(byday.values()) if byday else 0
    rules = _mandate()
    cons = float(rules.get("consistency_rule_eval") or 0.5)
    print(f"=== Trade Review (last {days}d window, {len(rows)} trades) ===")
    print(f"wins {len(wins)} / losses {len(losses)}   ·   win rate {wr:.0f}%")
    print(f"total P&L: ${total:.2f}   ·   best day: ${best_day:.2f}   ·   days traded: {len(byday)}")
    if total > 0:
        limit = total * cons
        flag = "BREACH" if best_day > limit else "OK"
        print(f"consistency ({int(cons*100)}% rule): best day ${best_day:.2f} "
              f"vs limit ${limit:.2f}  ->  {flag}")
    print("(paper journal — informational only)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "review"
    rest = sys.argv[2:]
    {"log": lambda: log(rest),
     "today": lambda: today(),
     "review": lambda: review(rest)}.get(cmd, lambda: review(rest))()


if __name__ == "__main__":
    main()
