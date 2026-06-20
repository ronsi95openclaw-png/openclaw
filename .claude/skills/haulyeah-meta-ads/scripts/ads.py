#!/usr/bin/env python3
"""Print HaulYeah Meta ad copy + carousel cards (text or JSON)."""
import argparse
import json
import os
import sys

_BOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "trash_hauling_bot")
sys.path.insert(0, os.path.abspath(_BOT))

from agents.marketing import carousel_cards, meta_ad_copy, meta_carousel_campaign_spec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--campaign-spec", action="store_true",
                    help="emit the Meta Graph API campaign/adset/creative/ad payloads (PAUSED)")
    ap.add_argument("--ad-account-id", default="AD_ACCOUNT_ID")
    ap.add_argument("--page-id", default="PAGE_ID")
    ap.add_argument("--link-url", default="https://example.com/quote")
    args = ap.parse_args()

    ads = meta_ad_copy()
    cards = carousel_cards()

    if args.campaign_spec:
        print(json.dumps(meta_carousel_campaign_spec(
            ad_account_id=args.ad_account_id,
            page_id=args.page_id,
            link_url=args.link_url,
        ), indent=2))
        return 0

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
        print(f"   prompt: {card['image_prompt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
