"""mark_routine_step.py - stamp a DAILY_ROUTINE.md checkpoint as done.

Fixes the recurring Ruflo gap where steps 0/2/4/6/7/8/9/10/11/12 get flagged
"silent" every night because nightly grading can only guess from narrative
text in COMPACT_ANALYSIS. This writes an explicit, machine-checkable marker
the moment a step actually completes.

Usage: python mark_routine_step.py <step_number> [note]
Writes to: data/routine_markers/<YYYY-MM-DD>.json (git-ignored, local only)
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKERS_DIR = os.path.join(ROOT, "data", "routine_markers")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mark_routine_step.py <step_number> [note]")
        return 2

    step = sys.argv[1]
    note = sys.argv[2] if len(sys.argv) > 2 else ""

    os.makedirs(MARKERS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(MARKERS_DIR, f"{today}.json")

    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    data[f"step_{step}"] = {
        "done_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"[MARKED] step {step} done at {data[f'step_{step}']['done_at']}" + (f" -- {note}" if note else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
