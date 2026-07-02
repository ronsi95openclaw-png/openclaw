"""trade_ops.py - operate & monitor the vibe-trading bot from ONE command.

Usage (run with the pandas venv, e.g.
  C:\\Users\\ronsi95openclaw\\Claude-openclaw\\.venv\\Scripts\\python.exe trade_ops.py <cmd>):

  status            - latest backtest result + today's paper decisions + safety state
  backtest [csv]    - run the backtest (defaults to ES_5M.csv, else ES_5M_TEST.csv)
  paper [csv]       - single paper pass through the runner (DRY_RUN, never live)

PAPER-FIRST: this never sends a live order. A live order needs env HERMES_BOT_LIVE=1
AND config go_live=true (both default OFF). This tool does not change either.
"""
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # ...\vibe-trading\bot
VT = os.path.dirname(HERE)                            # ...\vibe-trading
LOGS = os.path.join(HERE, "logs")
DATA = os.path.join(VT, "backtest", "data")
PY = sys.executable


def _run(module_args):
    env = dict(os.environ)
    env["PYTHONPATH"] = VT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([PY, "-m"] + module_args, cwd=VT, env=env,
                          capture_output=True, text=True)


def _pick_csv(arg):
    if arg and os.path.exists(arg):
        return arg
    for name in ("ES_5M.csv", "ES_5M_TEST.csv"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            return p
    return arg or os.path.join(DATA, "ES_5M.csv")


def status():
    print("=== Vibe Trading Bot — status ===")
    bts = sorted(glob.glob(os.path.join(LOGS, "backtests", "*.json")))
    if bts:
        try:
            with open(bts[-1], encoding="utf-8") as f:
                r = json.load(f)
            perf = r.get("performance", r) if isinstance(r, dict) else {}
            print("latest backtest:", os.path.basename(bts[-1]))
            for k in ("trades", "win_rate", "total_pnl", "max_drawdown",
                      "sharpe", "avg_trades_per_day", "worst_day"):
                if isinstance(perf, dict) and k in perf:
                    print(f"  {k}: {perf[k]}")
            if isinstance(r, dict) and "decisions" in r:
                print("  decisions:", r["decisions"])
        except Exception as e:
            print("  (could not parse latest backtest:", e, ")")
    else:
        print("no backtest has been run yet — use: trade_ops.py backtest")

    dec = os.path.join(LOGS, "decisions.jsonl")
    if os.path.exists(dec):
        try:
            lines = [l for l in open(dec, encoding="utf-8") if l.strip()]
            print(f"paper decisions logged: {len(lines)} (last 1 below)")
            if lines:
                print("  ", lines[-1].strip()[:200])
        except Exception:
            pass

    print("MODE: PAPER (DRY_RUN). Live needs HERMES_BOT_LIVE=1 AND go_live=true — both OFF.")


def backtest(arg):
    csv = _pick_csv(arg)
    print(f"running backtest on {csv} ...")
    p = _run(["bot.backtest", csv, "--instrument", "ES"])
    out = (p.stdout or "")[-3500:]
    print(out if out.strip() else (p.stderr or "")[-2000:])


def paper(arg):
    print("paper run (DRY_RUN, single pass) ...")
    p = _run(["bot.runner", "--once"])
    out = (p.stdout or "")[-3500:]
    print(out if out.strip() else (p.stderr or "")[-2000:])


def ready():
    print("=== Lucid Challenge Readiness ===")
    has_data = os.path.exists(os.path.join(DATA, "ES_5M.csv"))
    bts = sorted(glob.glob(os.path.join(LOGS, "backtests", "*.json")))
    trades, profitable = 0, False
    if bts:
        try:
            r = json.load(open(bts[-1], encoding="utf-8"))
            perf = r.get("performance", r) if isinstance(r, dict) else {}
            trades = int(perf.get("trades", 0) or 0)
            pnl = perf.get("total_pnl")
            profitable = pnl is not None and float(pnl) > 0
        except Exception:
            pass
    m = lambda b: "PASS" if b else "----"
    print(f" [{m(has_data)}] real ES 5M history at backtest/data/ES_5M.csv")
    print(f" [{m(trades > 0)}] backtest produces trades (last run: {trades})")
    print(f" [{m(profitable)}] backtest is PROFITABLE within Lucid rules")
    print(" [manual] forward paper-tested 2+ weeks on live data")
    print(" [manual] you reviewed the results and chose to go live")
    gate = has_data and trades > 0 and profitable
    print()
    if not gate:
        print("VERDICT: NOT ready — validate on real data first. Do NOT start the paid Lucid eval yet.")
    else:
        print("VERDICT: backtest gate passed — forward-test on paper before risking the eval.")
    print("Live trading is OFF (HERMES_BOT_LIVE / go_live default false). Keep it off until every gate passes.")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    {"status": lambda: status(),
     "backtest": lambda: backtest(arg),
     "paper": lambda: paper(arg),
     "ready": lambda: ready()}.get(cmd, status)()


if __name__ == "__main__":
    main()
