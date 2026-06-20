#!/usr/bin/env python3
"""Print a HaulYeah outreach message + container pitch for a DFW lead."""
import argparse
import os
import sys

_BOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "trash_hauling_bot")
sys.path.insert(0, os.path.abspath(_BOT))

from agents.marketing import container_pitch, outreach_message


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-type", default="junk removal")
    ap.add_argument("--city", default=None)
    ap.add_argument("--no-container", action="store_true",
                    help="omit the drop-off-container line")
    args = ap.parse_args()

    msg = outreach_message(
        job_type=args.job_type,
        city=args.city,
        include_container=not args.no_container,
    )
    print("Outreach (copy/send):\n" + msg)
    print("\nContainer pitch:\n" + container_pitch(city=args.city))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
