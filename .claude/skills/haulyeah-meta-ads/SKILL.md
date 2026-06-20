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
need the owner's Meta ad account and Page. When approved:

1. Confirm the ad account id and Page with the owner (`ads_get_ad_accounts`, `ads_get_user_pages`).
2. Build images for the carousel cards (use the `image_hint` on each card; the Higgsfield
   `generate_image` tool can produce them), then upload via `ads_get_ad_images` / image upload.
3. Create the campaign → ad set → creative → ad with the Meta ads tools
   (`ads_create_campaign`, `ads_create_ad_set`, `ads_create_creative`, `ads_create_ad`).
   Map each ad variant's `primary_text`/`headline`/`description`/`cta` onto the creative's
   `object_story_spec`. For the carousel, one card per `child_attachment`.
4. Preview with `ads_get_ad_preview` and show the owner before activating.

## Rules
- Copy generation: fine to run anytime.
- Campaign creation / spend: **only after the owner says go and provides the account + Page.**
- Keep headlines <= 40 chars (the generator already enforces this).
