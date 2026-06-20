---
name: haulyeah-outreach
description: Draft a HaulYeah first-touch outreach message for a trash-hauling / junk-removal / cleanout lead in the DFW area. Use when the hermes bot needs to reply to a Marketplace inquiry or a user asks for an outreach message or container pitch. Localizes to the DFW city and highlights the F-150 + drop-off container service. Drafts only — never auto-sends.
---

# HaulYeah Outreach Drafting

## When to use
- A new lead/inquiry comes in and you need a message to send.
- A user asks for "an outreach message", "what do I say to this lead", or "the container pitch".

## How to run
From the repo root:

```
python .claude/skills/haulyeah-outreach/scripts/outreach.py --job-type "garage cleanout" --city "Plano"
```

Arguments (all optional):
- `--job-type` — what the lead posted about (default: `junk removal`).
- `--city` — DFW city; localizes the greeting. Unknown cities fall back to "across the DFW area".
- `--no-container` — omit the drop-off-container line (use for small one-off pickups).

The script prints a ready-to-send outreach message plus a short container pitch. The copy
comes from `trash_hauling_bot/agents/marketing.py` (`outreach_message`, `container_pitch`)
— the single source of truth, so edits there flow to the bot's `/pitch` command too.

## Rules
- **Draft only.** Present the message for a human to copy/send; do not send on anyone's behalf.
- Do not invent prices, phone numbers, addresses, or availability. If a quote is wanted, use
  the quote estimator (`trash_hauling_bot/agents/quote.py`) and say "final price confirmed on-site".
- Keep it short and human — the generated copy already is; don't pad it.
