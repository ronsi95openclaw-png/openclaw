#!/usr/bin/env python3
"""
CryptoBot Read-Only Bridge for Hermes
Reads signals from logs ONLY -- never imports CryptoBot modules
Strangler-fig pattern
"""
import json
import os
from pathlib import Path
from datetime import datetime

CRYPTOBOT_LOG_PATH = Path("data/logs")
SIGNAL_OUTPUT = Path("sub-agents/crypto-agent/latest_signals.json")
PAPER_WATCH = Path("data/paper_watch/liquidity_sweep.jsonl")

def read_latest_signals() -> dict:
    signals = {
        "timestamp": datetime.now().isoformat(),
        "source": "CryptoBot/ClawBot",
        "mode": "read_only",
        "signals": [],
        "paper_watch_count": 0
    }

    # Read paper watch signals
    if PAPER_WATCH.exists():
        with open(PAPER_WATCH, 'r') as f:
            lines = [l.strip() for l in f if l.strip()]
            signals["paper_watch_count"] = len(lines)
            signals["paper_watch_latest"] = lines[-1] if lines else None

    # Read log files
    if CRYPTOBOT_LOG_PATH.exists():
        log_files = list(CRYPTOBOT_LOG_PATH.glob("*.log"))
        if log_files:
            latest_log = max(log_files, key=os.path.getmtime)
            with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()[-100:]
                for line in lines:
                    if any(kw in line.upper() for kw in ['BUY', 'SELL', 'SIGNAL', 'ALERT']):
                        signals['signals'].append(line.strip())

    SIGNAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNAL_OUTPUT, 'w') as f:
        json.dump(signals, f, indent=2)

    print(f"{len(signals['signals'])} signals | {signals['paper_watch_count']} paper-watch entries")
    return signals

if __name__ == "__main__":
    data = read_latest_signals()
    print(json.dumps(data, indent=2))
