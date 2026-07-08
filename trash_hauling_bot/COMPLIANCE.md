# COMPLIANCE — HaulY'all Bot

Operational rules for the `trash_hauling_bot`. These exist to keep the business's
Facebook account from getting banned and to keep all outreach human-controlled.
**These are hard rules. Code that violates them must not ship.**

## 1. No automated posting or DMing on Facebook
- The bot **never** auto-posts to Marketplace/groups and **never** auto-DMs/cold-messages
  anyone on the personal/logged-in Facebook account. Auto-posting and Playwright cold-DM
  violate Facebook ToS and are the #1 ban cause.
- The bot also never auto-sends SMS/email/Messenger to customers.

## 2. Human-in-the-loop (AI drafts, human approves)
- AI may only **draft** outreach/ads/images. Ronnie reviews and approves in Telegram.
- Sending happens **manually**: Ronnie copy-pastes / taps the one-tap link, or replies
  in Messenger to people who message **first**.
- In code: any path that could externally send/post must pass through
  `utils.compliance.assert_human_approved(...)` with an explicit human-approval flag.
  `outreach.confirm_send` only marks a queued draft as sent **after** a human runs
  `/confirm`; it performs no network send.

## 3. Paid ads — official Meta Ads only
- Paid ads go through the **official Meta Ads API / Ads Manager** only.
  Never browser-automate ad creation or posting.
- Ad drafting (Hermes side) produces paste-ready campaign text. Actual posting is
  **gated** on the Meta account being enabled + payment method + a connected Page,
  and on human approval. Do not attempt to post until those are in place.

## 4. Scrape pacing (read-only, human-like)
- The scraper is **read-only browsing only**. It never bulk-acts, never clicks
  message/contact, never submits forms.
- Keep human-like jitter between actions (the scraper already sleeps ~2–4s per keyword
  and ~1–2s between scrolls). New pacing code uses
  `utils.compliance.human_pace_sleep(min_s, max_s)` (jittered, never fixed-rate).

## 5. Audit everything
- Every draft, approval, denial, and external-facing action is logged to
  `data/audit.log` via `utils.audit.AuditLogger`. No silent actions.

## Allowed vs forbidden (quick reference)
| Action | Allowed? |
| --- | --- |
| Scrape/read Marketplace listings (read-only, paced) | yes |
| Draft outreach message for human review | yes |
| Draft ad campaign text (paste-ready) | yes |
| Draft brand image prompt (Hermes/Higgsfield, on approval) | yes |
| Notify the team in Telegram (internal) | yes |
| Mark a draft "sent" after human `/confirm` | yes |
| Auto-post to Marketplace / FB groups | **NO** |
| Auto-DM / cold-message customers (FB, SMS, email) | **NO** |
| Browser-automate Meta Ads creation/posting | **NO** |
| Send anything externally without human approval | **NO** |

## Ban-risk notes
- The fastest way to lose the FB account is automated send/post on the logged-in
  session. Keep all send/post human-initiated and use official APIs for paid ads.
- Never raise scrape volume or remove jitter to "go faster" — pacing is a safety control,
  not a performance knob.
