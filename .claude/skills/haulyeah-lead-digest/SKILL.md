---
name: haulyeah-lead-digest
description: Build the HaulYeah new-leads alert as a compact, length-bounded digest. Use this whenever the hermes bot sends the periodic "haulyeah-lead-alert" (or a user asks for a lead summary). It replaces the old alert that failed with "Response remained truncated after 3 continuation attempts" — the output is hard-capped so it never overflows Telegram.
---

# HaulYeah Lead Digest

## When to use
- The scheduled `haulyeah-lead-alert` job fires.
- Someone asks "what are the new leads" / "send the lead alert" / "lead summary".

## Why this skill exists
The previous alert dumped every lead into one message, which grew past Telegram's
4096-char limit and failed with `RuntimeError: Response remained truncated after 3
continuation attempts`. This skill produces a fixed-size digest instead: leads ranked
by urgency, top few shown, the rest rolled into a `+N more` footer. **Always send the
digest output verbatim — do not re-expand it into a long message.**

## How to run
From the repo root:

```
python .claude/skills/haulyeah-lead-digest/scripts/digest.py
```

- By default it pulls leads with status `new` from the project's Google Sheet (uses the
  bot's existing `SheetsClient` and the env in the root `.env`).
- To run against a fixed set of leads (testing, or when Sheets is unavailable):
  `python .claude/skills/haulyeah-lead-digest/scripts/digest.py --leads-json path/to/leads.json`
  where the JSON is a list of `{"id","urgency_score","job_type","location"}` objects.
- Optional: `--max-chars 1200 --max-leads 8` to tune the bounds.

Print exactly what the script outputs as the alert. The logic lives in
`trash_hauling_bot/agents/lead_alert.py` (`build_digest`) — edit there, not here.

## Do not
- Do not reformat the digest into a per-lead breakdown — that reintroduces the overflow.
- For full lead detail, point the user at the bot's `/leads` and `/lead <id>` commands.
