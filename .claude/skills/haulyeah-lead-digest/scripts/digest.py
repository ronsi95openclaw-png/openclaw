#!/usr/bin/env python3
"""Print the compact HaulYeah new-leads digest.

Pulls `new` leads from the project Google Sheet by default, or from a JSON file
(--leads-json) when Sheets is unavailable. Output is length-bounded by
agents.lead_alert.build_digest so the lead alert can never truncate.
"""
import argparse
import json
import os
import sys

# Make the trash_hauling_bot package importable regardless of cwd.
_BOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "trash_hauling_bot")
sys.path.insert(0, os.path.abspath(_BOT))

from agents.lead_alert import DEFAULT_MAX_CHARS, DEFAULT_MAX_LEADS, build_digest


def _load_leads(path: str | None) -> list:
    if path:
        with open(path) as f:
            return json.load(f)
    # No file given — pull live `new` leads from Sheets.
    from integrations.sheets import SheetsClient
    return SheetsClient().get_leads_by_status("new")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads-json", default=None, help="JSON list of leads to summarize")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    ap.add_argument("--max-leads", type=int, default=DEFAULT_MAX_LEADS)
    args = ap.parse_args()

    try:
        leads = _load_leads(args.leads_json)
    except Exception as exc:  # Sheets/creds unavailable — fail loud but useful.
        print(f"Could not load leads ({exc}). "
              f"Pass --leads-json with a list of leads to build a digest offline.",
              file=sys.stderr)
        return 1

    print(build_digest(leads, max_chars=args.max_chars, max_leads=args.max_leads))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
