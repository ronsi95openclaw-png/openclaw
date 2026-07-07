#!/usr/bin/env python3
"""
revenue_ledger.py — HaulYeah job revenue tracker.

Usage:
  python revenue_ledger.py log [amount] [description...]  — log a completed job
  python revenue_ledger.py today                          — today's revenue
  python revenue_ledger.py weekly                         — this week's summary
  python revenue_ledger.py stats                          — all-time stats

Stores to: trash_hauling_bot/data/revenue.json
"""
import datetime
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")
REVENUE_FILE = os.path.join(DATA_DIR, "revenue.json")


def _load() -> list:
    if not os.path.exists(REVENUE_FILE):
        return []
    try:
        with open(REVENUE_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(jobs: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REVENUE_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)


def log_job(args: list) -> None:
    if not args:
        print("usage: log [amount] [description...]")
        print("  e.g. log 175 'single room cleanout, Irving TX'")
        return
    try:
        amount = float(args[0])
    except ValueError:
        print(f"ERROR: amount must be a number, got '{args[0]}'")
        return
    desc = " ".join(args[1:]) if len(args) > 1 else ""
    now = datetime.datetime.now().astimezone()
    rec = {
        "ts": now.isoformat(),
        "date": now.date().isoformat(),
        "week": now.strftime("%Y-W%V"),
        "amount": round(amount, 2),
        "description": desc,
    }
    jobs = _load()
    jobs.append(rec)
    _save(jobs)
    print(f"Logged: ${amount:.2f} — {desc or '(no description)'} [{rec['date']}]")
    # cumulative today
    today_total = sum(j["amount"] for j in jobs if j["date"] == rec["date"])
    print(f"Today's total: ${today_total:.2f}")


def today() -> None:
    d = datetime.date.today().isoformat()
    jobs = [j for j in _load() if j.get("date") == d]
    total = sum(j["amount"] for j in jobs)
    print(f"=== HaulYeah Revenue — {d} ===")
    if not jobs:
        print("No jobs logged today.")
        return
    for j in jobs:
        print(f"  ${j['amount']:.2f}  {j.get('description', '')}")
    print(f"  TOTAL: ${total:.2f}")


def weekly() -> None:
    week = datetime.date.today().strftime("%Y-W%V")
    jobs = [j for j in _load() if j.get("week") == week]
    total = sum(j["amount"] for j in jobs)
    by_day: dict = {}
    for j in jobs:
        by_day.setdefault(j["date"], []).append(j["amount"])
    print(f"=== HaulYeah Revenue — {week} ===")
    if not jobs:
        print("No jobs logged this week.")
        return
    for day in sorted(by_day):
        day_total = sum(by_day[day])
        print(f"  {day}: ${day_total:.2f} ({len(by_day[day])} jobs)")
    print(f"  WEEK TOTAL: ${total:.2f} across {len(jobs)} jobs")
    avg = total / len(jobs) if jobs else 0
    print(f"  Avg job: ${avg:.2f}")


def stats() -> None:
    jobs = _load()
    if not jobs:
        print("No revenue recorded yet. Log a job: revenue_ledger.py log 150 'junk pickup'")
        return
    total = sum(j["amount"] for j in jobs)
    by_week: dict = {}
    for j in jobs:
        by_week.setdefault(j.get("week", "?"), []).append(j["amount"])
    best_week = max(by_week, key=lambda w: sum(by_week[w]))
    best_week_total = sum(by_week[best_week])
    avg_job = total / len(jobs)
    print(f"=== HaulYeah All-Time Stats ===")
    print(f"  Total jobs: {len(jobs)}")
    print(f"  Total revenue: ${total:.2f}")
    print(f"  Avg per job: ${avg_job:.2f}")
    print(f"  Weeks active: {len(by_week)}")
    print(f"  Best week: {best_week} (${best_week_total:.2f})")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "today"
    rest = sys.argv[2:]
    cmds = {
        "log": lambda: log_job(rest),
        "today": lambda: today(),
        "weekly": lambda: weekly(),
        "week": lambda: weekly(),
        "stats": lambda: stats(),
    }
    cmds.get(cmd, lambda: print(f"Unknown command: {cmd}\nUsage: log|today|weekly|stats"))()


if __name__ == "__main__":
    main()
