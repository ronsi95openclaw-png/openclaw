"""Entry point for the OpenClaw bots.

Usage:
    python main.py dca    # run a single DCA cycle
    python main.py futures  # run a single futures cycle

Extend this file to support scheduling or background services.
"""
from __future__ import annotations

import argparse
import sys

from config.settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openclaw")
    parser.add_argument("mode", choices=["dca", "futures"], help="Which bot to run")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args(argv)

    settings = load_settings()

    if args.mode == "dca":
        from bots.dca.dca_bot import run_dca_once

        run_dca_once()
    elif args.mode == "futures":
        from bots.futures.futures_bot import run_futures_once

        run_futures_once()
    else:
        parser.print_help()
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
