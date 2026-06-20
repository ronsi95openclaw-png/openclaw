#!/usr/bin/env python3
"""Print HaulYeah Meta ad copy + carousel cards (text or JSON)."""
import argparse
import json
import os
import sys

_BOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "trash_hauling_bot")
sys.path.insert(0, os.path.abspath(_BOT))

from agents.marketing import carousel_cards, meta_ad_copy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    ads = meta_ad_copy()
    cards = carousel_cards()

    if args.json:
        print(json.dumps({"ads": ads, "carousel": cards}, indent=2))
        return 0

    print("=== Meta ad copy ===")
    for ad in ads:
        print(f"\n[{ad['name']}]")
        print(ad["primary_text"])
        print(f"Headline: {ad['headline']}")
        print(f"Description: {ad['description']}  |  CTA: {ad['cta']}")

    print("\n=== Carousel cards ===")
    for i, card in enumerate(cards, 1):
        print(f"{i}. {card['headline']} — {card['body']}")
        print(f"   image: {card['image_hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
