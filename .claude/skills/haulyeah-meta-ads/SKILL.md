---
name: haulyeah-meta-ads
description: Produce HaulYeah Meta (Facebook/Instagram) ad assets — single-image ad copy variants and a 5-card carousel — for the DFW trash-hauling business, and explain how to push them live via the Meta ads tools once the owner approves. Use when the hermes bot is asked to create FB ads, a carousel, or ad copy. Generating copy is automatic; creating LIVE campaigns requires explicit owner approval plus an ad account + Page.
---

# HaulYeah Meta Ads

## When to use
- "Make me a Facebook ad / carousel / ad copy."
- Planning a paid campaign for the DFW hauling service.

## Step 1 — Generate the copy (always safe)
From the repo root:

```
python .claude/skills/haulyeah-meta-ads/scripts/ads.py          # human-readable
python .claude/skills/haulyeah-meta-ads/scripts/ads.py --json    # machine-readable
```

This prints 3 ad variants (primary text / headline / description / CTA) and 5 carousel
cards (headline / body / image hint). Source of truth: `trash_hauling_bot/agents/marketing.py`
(`meta_ad_copy`, `carousel_cards`). The same content is in `trash_hauling_bot/MARKETING_PLAYBOOK.md`
with Ads Manager targeting notes (DFW geo, audience, objective).

## Step 2 — Create LIVE ads (owner-gated)
**Do not create live ads without explicit owner approval.** Live ads spend real money and
need the owner's Meta ad account and Page.

### Preconditions (check first — these have blocked it before)
- `ads_get_ad_accounts` → the chosen account must have `is_ads_mcp_enabled: true`. If false
  ("Ads MCP is gradually being rolled out"), the API will reject object creation — stop and
  tell the owner the account isn't enabled yet.
- `ads_get_user_pages` / `ads_get_ad_account_pages` → there must be at least one Page. A
  carousel creative requires a `page_id`; with no Page you cannot build the ad.

### Build the payloads (one command)
```
python .claude/skills/haulyeah-meta-ads/scripts/ads.py --campaign-spec \
  --ad-account-id <ID> --page-id <PAGE_ID> --link-url <QUOTE_URL>
```
This prints ready-to-submit `campaign` / `ad_set` / `creative` / `ad` bodies (all `PAUSED`),
with one `child_attachment` per carousel card. Source: `marketing.meta_carousel_campaign_spec`.

### Submit
1. Generate 5 card images (use each card's `image_hint`; Higgsfield `generate_image`), then
   upload them and collect the `image_hash` for each — pass them as `image_hashes` to the
   spec builder so the `IMAGE_HASH_n` placeholders are replaced.
2. `ads_create_campaign` → `ads_create_ad_set` → `ads_create_creative` → `ads_create_ad`,
   feeding the IDs forward (campaign_id into the ad set, etc.).
3. Preview with `ads_get_ad_preview` and show the owner. Activate only on explicit go.

## Rules
- Copy generation: fine to run anytime.
- Campaign creation / spend: **only after the owner says go and provides the account + Page.**
- Keep headlines <= 40 chars (the generator already enforces this).
