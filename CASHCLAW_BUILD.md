# CASHCLAW BUILD — Claude Code Drop-In Prompt

> Drop this file into a Claude Code session and say: **"Build this."**
> All 5 modules are already written and wired. This document is for reference,
> verification, and guided extension.

---

## What Is CashClaw?

CashClaw is the income engine inside ClawBot.
It finds freelance gigs, writes humanized outreach, tracks income, and
self-reviews its own performance — all piped through Telegram so Ronnie
approves every action before anything fires.

**No trading. No financial automation. Pure freelance income.**

Target categories:
- Freelance dev: Telegram bots, Discord bots, Python scripts, API integrations, scrapers
- AI/ML tasks: prompt engineering, data labeling, chatbot builds, AI automation
- Content: crypto/tech newsletters, ghostwriting, tweet threads
- Notion templates and digital products
- AI automation consulting

---

## Architecture (5 Modules)

```
[Whop Marketplace]
      │
      ▼
┌─────────────────────┐
│  1. Job Scout        │  agents/job_scout.py
│  Scrapes + scores    │  Runs every 6h via APScheduler
│  Top 3 → Telegram   │  /scout  /approve_job N  /reject_job N
└──────────┬──────────┘
           │ approved jobs
           ▼
┌─────────────────────┐
│  2. HumanVoice       │  agents/human_voice.py
│  Pass 1: Ollama      │  Generates raw pitch draft
│  Pass 2: Haiku       │  Strips AI-isms → sounds like Ronnie
└──────────┬──────────┘
           │ humanized draft
           ▼
┌─────────────────────┐
│  3. CashClaw Applier │  agents/cashclaw_applier.py
│  Telegram gate       │  /apply_job N  → preview draft
│  Manual confirm      │  /send_apply N → mark as sent
└──────────┬──────────┘
           │ income logged
           ▼
┌─────────────────────┐
│  4. Performance      │  agents/performance_tracker.py
│  Tracker             │  Tracks income, views, gig stats
│  /log_income         │  Runs every 6h via APScheduler
└──────────┬──────────┘
           │ weekly data
           ▼
┌─────────────────────┐
│  5. Self Review      │  agents/self_review.py
│  Sunday midnight     │  Haiku analyzes 30d data
│  Auto-applies        │  Low-risk fixes auto, big → Telegram
│  safe config fixes   │
└─────────────────────┘
```

---

## Current Status (as of v0.8.1)

| Module              | File                           | Status   |
|---------------------|--------------------------------|----------|
| Job Scout           | agents/job_scout.py            | ✅ Built  |
| HumanVoice          | agents/human_voice.py          | ✅ Built  |
| CashClaw Applier    | agents/cashclaw_applier.py     | ✅ Built  |
| Performance Tracker | agents/performance_tracker.py  | ✅ Built  |
| Self Review         | agents/self_review.py          | ✅ Built  |

**Telegram handlers registered in content/receiver.py:**

| Command            | What it does                                     |
|--------------------|--------------------------------------------------|
| /cashclaw          | Full system status overview                      |
| /scout             | Show scout status / /scout run = scan now        |
| /approve_job N     | Approve pending gig at index N                   |
| /reject_job N      | Reject pending gig                               |
| /apply_job N       | Generate HumanVoice outreach for approved gig N  |
| /send_apply N      | Confirm outreach was sent → moves to applied     |
| /discard_apply N   | Discard a draft without sending                  |
| /log_income $ src  | Log income entry (e.g. /log_income 150 whop)     |

**Scheduled jobs (APScheduler):**
- Job Scout: every 6h — sends top 3 to Telegram for approval
- Performance Tracker: every 6h — pulls social stats + income snapshot
- Self Review: Sunday 23:59 UTC — Haiku analyzes 30d, auto-fixes safe configs

---

## HumanVoice Rules (Hardcoded in agents/human_voice.py)

Every outreach message must pass these checks before Telegram delivery:

1. No sentence starts with "I" — restructure instead
2. No openers: "I hope this finds you well", "Reaching out because", "My name is"
3. No corporate words: leverage, synergy, deliverables, bespoke, utilize, solutions, expertise
4. No em-dashes (—) — use plain dash or rewrite
5. Cold outreach: max 4 sentences. Follow-up: max 6.
6. Must include one specific detail from the actual listing
7. End with a clear, low-friction call to action

Violations are flagged in the Telegram preview — they don't block delivery,
but they show up so Ronnie can tweak before hitting /send_apply.

---

## Data Files

```
data/
  job_scout_state.json     — scout scan history, pending/approved/applied/rejected lists
  applier_state.json       — outreach drafts (pending → sent)
  performance_db.json      — social stats + income snapshots
  income_log.json          — all logged income entries
  logs/                    — trade + system logs
  reports/                 — weekly reports
```

---

## .env Keys Required for CashClaw

```env
ANTHROPIC_API_KEY=sk-...        # Claude Haiku for HumanVoice pass 2
TELEGRAM_BOT_TOKEN=...          # Bot delivery
ALLOWED_CHAT_ID=...             # Your chat ID (auth gate)
# Optional — for social stats tracking
TIKTOK_ACCESS_TOKEN=...
INSTAGRAM_ACCESS_TOKEN=...
```

---

## Extending CashClaw

### Add a new job source (e.g. Fiverr)

1. Add a new scraper function in `agents/job_scout.py`:
   ```python
   def scrape_fiverr_jobs(max_results: int = 10) -> list[dict]:
       ...
   ```
2. Add its results to `scrape_whop_jobs()` or create `scrape_all_jobs()`.
3. No other changes needed — scoring, approval, outreach all use the same dict schema.

### Add a new outreach style (e.g. LinkedIn DM)

1. Pass `style="linkedin_dm"` to `generate_apply(job_index, style="linkedin_dm")`.
2. Update the Haiku system prompt in `agents/human_voice.py` to handle the new style.

### Add a new income source

```
/log_income 200 fiverr "Python bot build"
/log_income 50 gumroad "Notion template sale"
```
The tracker picks it up automatically in the next 6h snapshot.

---

## First Command to Run

Open a terminal in the project root and run:

```bash
python -c "from agents.human_voice import generate_outreach; print(generate_outreach({'title':'Test Gig','description':'Need a Telegram bot built fast','budget_min':100,'budget_max':300,'platform':'whop'}))"
```

This verifies HumanVoice is wired (Ollama + Haiku).

Then start the bot:
```bash
python -m content.receiver
```

Then in Telegram:
```
/cashclaw        ← see the full system status
/scout run       ← fire a manual scout scan
```

---

## Income Math

| Source                  | Rate                      | Notes                          |
|-------------------------|---------------------------|--------------------------------|
| TikTok Creator Fund     | $0.02–$0.04 / 1K views   | Tracked in performance_db.json |
| Instagram Reels Bonus   | $0.01–$0.05 / 1K views   | Stub until IG token configured |
| Whop / freelance gigs   | $50–$500 / gig            | Logged via /log_income         |
| Notion templates        | $10–$50 / sale            | Logged via /log_income         |
| AI consulting retainer  | $500–$2000 / month        | Logged via /log_income         |

Conservative target: 2 gigs/month at $100 avg = **$200/month to start**
Optimized target: 6 gigs/month + 500K TikTok views = **$800–$1200/month**

---

*CashClaw v0.8.1 — OpenClaw project — 2026-04-16*
