# HaulYA'LL! — Meta Ads Skill System

Five slash commands for running paid social for **HaulYA'LL!**, a DFW junk-removal startup,
on top of the connected **Meta Ads MCP**.

Type the command name in Claude (e.g. `/spy`) to run it. Each command file in this folder is a
self-contained prompt.

## Brand kit
- **Voice:** friendly, no-nonsense, Texas-proud, working class
- **Core offer:** fast junk removal · same-day available · upfront pricing · DFW metro
- **Meta Ads account ID:** `795836823772411`
- **Colors:** Haul Gold `#F5A623` · Yeah Orange `#D84A1F` · Texas Night `#2A2D34` · Dust Cream `#F5F0E8`

## The five commands

| Command | What it does | Live data? | Meta Ads MCP tool(s) |
|---|---|---|---|
| `/spy` | Ranks active competitor ads by hook, CTA, offer, and run time | ✅ | `ads_library_search` |
| `/adsmeta` | Full account health audit → 0–100 score + priority fix list | ✅ | `ads_get_ad_accounts`, `ads_get_ad_entities`, `ads_get_field_context`, `ads_get_opportunity_score`, `ads_insights_auction_ranking_benchmarks`, `ads_insights_anomaly_signal`, `ads_insights_performance_trend` |
| `/bulk-creative` | 20 on-brand ad copy variations across 6 angles | ❌ (pure LLM) | none |
| `/ad-score` | Grades one creative on 6 dimensions, /100, GO/NO-GO | ❌ (pure LLM) | none |
| `/competitive-ad-extractor` | Ranks competitor hooks + finds 3 angle gaps to own | ✅ | `ads_library_search` |

## Before you run — prerequisites & permissions
- **`/spy` and `/competitive-ad-extractor`** call the public Ad Library. The MCP requires the token to
  have **at least one active ad account** or the search returns an error. They pull **US-only, ACTIVE** ads.
- **`/adsmeta`** reads live account data. It first checks `is_queryable` on the account; if Meta hasn't
  enabled MCP querying for `795836823772411`, it will report `not_queryable_reason` and stop. It's
  **read-only** — it never creates, pauses, or edits anything.
- **`/ad-score`** needs you to paste the creative (hook + body + CTA, plus any visual idea) as the argument.
- **`/bulk-creative`** needs nothing — run it as-is.

## Notes
- The Meta Ads MCP exposes the tools above under a session-specific server ID; the command prompts refer to
  them by their stable logical names (`ads_library_search`, `ads_get_ad_entities`, …) so they keep working
  across sessions.
- The Ad Library has **no public engagement metric**, so `/competitive-ad-extractor` ranks hooks by a stated
  copy-quality proxy (direct · specific · offer-forward), not real impressions/engagement.
