# HaulY'all AI Growth System — Design Spec (2026-06-20)

## Goal
Give the HaulY'all junk-removal business an AI growth system that: finds jobs to bid on,
posts ads (with approval), generates on-brand images, drafts custom outreach for quick send,
and stays **compliant with Facebook rules so the account never gets banned**.

## Non-negotiable compliance principles (enforce in every piece)
1. **No automated posting or DMing on the personal/logged-in Facebook account.** Auto-posting
   to Marketplace/groups and cold auto-DM via Playwright violates FB ToS and is the #1 ban cause.
2. **Human-in-the-loop:** AI only *drafts*. Ronnie approves in Telegram. Posting/sending happens
   via compliant channels (official Meta Ads for paid ads; Ronnie sends DMs manually / replies via
   Messenger to people who message first).
3. **Paid ads** go through the official Meta Ads API/Manager only — never browser automation.
4. **Audit** every draft/approval/action to `data/audit.log`.
5. Scraper stays read-only browsing with human-like pacing (jitter); never bulk-acts.

## Feasibility findings (drive the architecture)
- The bot (`trash_hauling_bot`) has **no image-gen and no Meta API** (local Ollama + OpenRouter text only).
- **Image generation** is available only in the **Hermes/Claude layer** via the Higgsfield MCP
  (currently 10 free credits) — so brand image gen is a **Hermes skill capability**, not bot Python.
  For volume, Ronnie should add a dedicated image API key later.
- **Meta Ads**: account `795836823772411` has `is_ads_mcp_enabled=false`, no payment method, no Page.
  Ads **cannot post yet**; build the drafter + paste-ready output; real posting unlocks when Meta enables the account.

## Architecture (split by layer)
**A. `trash_hauling_bot` (Python) — data + approval surface + compliant helpers:**
- Piece 2 Lead/job ranking, Piece 3 Quick-send, Piece 5 Compliance guards, shared `brand_kit.json`,
  and the Telegram commands that surface image/ad drafts.

**B. Hermes (`%LOCALAPPDATA%\hermes`) — generative capabilities via skill + MCP:**
- Piece 1 brand image generation (Higgsfield) and Piece 4 ad-campaign drafting, driven by the
  `haulyeah-agent` SKILL.md + the shared brand kit. (Hermes already serves Ronnie on Telegram.)

## Pieces
### Piece 1 — Brand image generator (Hermes-side)
- Shared `trash_hauling_bot/brand_kit.json`: name "Haul Y'all", phone (469) 618-7677,
  email junkgone@haulya-ll.com, colors Haul Gold #F5A623 / Yeah Orange #D84A1F / Texas Night #2A2D34 /
  Dust Cream #F5F0E8, hours Mon-Sat 7am-7pm, voice (friendly Texas, upfront flat-rate).
- Update Hermes `haulyeah-agent` SKILL.md with a "brand image" command: when asked, build a prompt
  embedding the brand kit and call the image MCP (Higgsfield), return image for approval. Note 10-credit limit.
- Do NOT call the image MCP during the build; only write the skill/config that enables it.

### Piece 2 — Lead/job finder upgrade (bot-side)
- New `agents/scoring.py` (or `utils/scoring.py`): pure functions to score a lead (urgency, size,
  recency, location proximity to DFW) → 0-100 + reason. Unit-testable, no I/O.
- A function to select daily top-N new leads and format a Telegram digest with each lead's id +
  score + a suggested next action. Wire as `/topleads` in the integration phase.
- Keep web-search "active job finding" as skill guidance (compliant, read-only); no new scraping of gated pages.

### Piece 3 — Quick-send messaging (bot-side)
- New `utils/quicksend.py`: given an approved outreach message + lead contact, produce
  (a) a clean copy-paste block and (b) a one-tap link — `sms:<number>?body=<urlencoded>` when a phone
  exists, else an `https://m.me/` / Marketplace thread hint. Pure function, unit-testable.
- Minimal `outreach.py` touch only if needed to expose contact/number. Wire as `/quicksend <id>`.
- Still 100% manual send — bot never sends.

### Piece 4 — Ad campaign drafter (Hermes-side + bot surface)
- Update SKILL.md / add a drafting helper to produce a full campaign from `brand_kit.json` +
  `HaulYeah_Meta_Ads_Draft.md`: objective, budget, targeting, 3 creative variations (primary text,
  headline, description), and CTA — formatted ready to paste into Meta Ads Manager.
- Posting via Meta Ads MCP is GATED (account not enabled). Add a clear "to actually post: enable Meta
  Ads + add payment + connect a Page" note. Do NOT attempt to post.

### Piece 5 — Compliance guardrails (bot-side, cross-cutting)
- `COMPLIANCE.md`: the rules above, what's allowed/forbidden, ban-risk notes.
- `utils/compliance.py`: helpers/asserts — e.g., `assert_human_approved(...)`, a scrape-pacing helper
  (jitter), and a guard that there is no auto-send path. Add a test that no module calls a "send DM"/"post"
  Facebook action automatically.
- Confirm scraper keeps human-like delays (it already sleeps 2-4s/keyword).

## Execution (sub-agent workflow)
- **Phase Build (parallel, no shared-file edits):** Agent A=Piece 2 modules, Agent B=Piece 3 modules,
  Agent C=Piece 5 (docs+guards), Agent D=brand_kit.json + Hermes SKILL.md (Pieces 1&4). Each creates
  NEW files + returns the exact Telegram commands/handlers to wire. NONE edit `integrations/telegram_bot.py`.
- **Phase Integrate (single agent):** wire all new commands into `telegram_bot.py` (+ minimal `outreach.py`),
  fix imports, `py_compile` every changed file.
- **Phase Verify (single agent):** `py_compile` + import-smoke the whole bot, run `pytest` if present,
  assert no auto-send/auto-post path, produce a report. Do NOT restart the live bot (main loop will, after review).

## External dependencies (Ronnie)
- Meta: add payment method + connect a Facebook **Page** + wait for/enable Ads MCP → before ads can post.
- Optional: a dedicated image-gen API key for unlimited brand images (Higgsfield free = 10 credits).
- Approve creating the `HaulYeahBot` logon scheduled task for reboot-persistent 24/7 scraper.

## Verification / done criteria
- All changed Python compiles + imports; existing tests pass; no auto-send path; new Telegram commands
  registered; brand kit + SKILL.md updates present. Live bot restarted only after main-loop review.
