# Runtime Memory for the OpenClaw Ecosystem — Design Proposal

**Status: DESIGN ONLY. No code in this document has been implemented.**
Written 2026-07-09 as Phase 4 of the full-ecosystem fix pass. Requires Ronnie's
explicit approval before any build session starts.

---

## 1. The question this doc has to answer honestly

The original ask was "Graphify as runtime memory." Before designing that, it's
worth checking whether Graphify is actually the right substrate — building on
the wrong foundation because it was the one named in the prompt would be a
worse outcome than recommending something else.

**Verdict: Graphify is the wrong substrate for runtime memory. It should stay
exactly what it is today — a dev-tool AST code graph — and runtime memory
should be a new, separate, Supabase-backed layer.**

### Why Graphify doesn't fit

Graphify's node/edge model is **code structure**: functions, classes, imports,
call relationships. Its refresh cadence is **nightly, full-repo AST re-parse**
(`graphify update`, run by `hermes-self-review` at 01:00). Its query surface
(`graphify query "<symbol>"`, `graphify path A B`, `graphify explain "<concept>"`)
answers "what calls this function" — a fundamentally different question shape
than "how many times has this kill-zone setup appeared in the last 30 days and
what happened."

Runtime memory needs to store **events**: a trade decision at a timestamp, a
lead contact outcome, a signal that fired and what it resolved to. These are
rows with numeric outcomes, timestamps, and relationships between entities
(trade → signal → instrument → session) — not code symbols. Forcing this into
an AST graph would mean inventing a second, unrelated node/edge schema inside
a tool built and tuned for a different one, then bolting a real-time or
15-minute-cadence incremental-update path onto something that currently only
knows how to do a full nightly rebuild. That's building a database inside a
tool that isn't a database, when a database already exists in this ecosystem
and is proven to work.

### Why Supabase is the right substrate instead

The `openclaw` Supabase project already runs a working, independently-verified
pattern for exactly this shape of problem: `push_cron_health.py` writes
structured, timestamped rows (job name, status, timestamps) that Hermes's own
brief-generation jobs and this session's audit both queried directly with
plain SQL, with zero drama. That pattern — a `no_agent` script writes rows
after its normal JSONL logging, a cron job snapshots/pushes them, anything
that wants historical context queries the table — generalizes directly to
trade decisions, lead events, and daily lessons. No new infrastructure
concept needs to be introduced; the same mechanism gets three more tables.

---

## 2. What "runtime memory" should mean, per pillar

### VibeTrader
At decision time, useful context is things like: *"this exact setup shape
(kill zone + sweep direction + HTF bias) has appeared N times in the last 30
days; M of those hit TP1, K hit SL."* That's valuable for:
- **Hermes chat narration** (`trade brief`, `loss analysis`, `bot status`) —
  richer, data-backed commentary instead of "no trades today."
- **`strategy_reviewer.py`-style analysis** — currently greps flat JSONL;
  a queryable table makes "win rate by setup type" a five-line SQL query
  instead of a bespoke script.
- **Weekly/monthly trend reporting** — P&L trend, consistency-rule margin,
  drawdown-versus-limit over time, for Ronnie's own review, not the bot's.

**What it must NEVER do:** feed back into `generate_signal()` or
`RiskGuard.check()` as a live input that changes a real-time trading decision.
Position sizing, the max-loss halt, the consistency cap, the kill switch, the
EOD flatten — all of that stays 100% deterministic from `lucid_mandate.json`
and the mandate-driven `RiskGuard`, exactly as it is today. Memory is a
read-only lens for humans and for chat narration, never a second, informal
gate sitting next to `risk_guard.py`.

### HaulOps
Useful context: *"have we contacted this phone/address before, and what
happened."* Today this lives partly in Google Sheets and `audit.log`; a
queryable table would let `haulyeah_lead_review`/`haulyeah_top_leads` cron
jobs give richer, deduped, historically-aware summaries instead of re-scanning
raw logs each run.

**What it must never do:** auto-decide to contact or exclude a lead. The
existing approval-gated, FB-safe, DRY_RUN-guarded flow stays exactly as it is
— memory informs the summary a human reads, it doesn't drive outreach.

### Hermes's own brief-generation jobs
`all-projects-daily-brief`, `hermes-performance-report`, `ruflo-scorecard`
already do informal memory today — reading `COMPACT_ANALYSIS_*.md`,
`routine_markers/*.json`, `RUFLO_LESSONS_*.md` by filename-sorting and
grepping. A structured table (see §3) turns "list the last N files, grep for
a keyword" into a real query, and makes trend answers ("how often has step 8
been missed this month") a query instead of manually reading N files.

---

## 3. Proposed architecture

**No new infrastructure concept — three additive tables in the same
`openclaw` Supabase project already proven by `cron_health`.**

Illustrative shape (not a migration — for discussion only):

| Table | Rough shape | Written by |
|---|---|---|
| `vibe_decisions` | timestamp, instrument, decision (skip/approve/reject/flat), reason, setup features (kill_zone, side, htf_bias) | `vibe_paper_scan.py`, after its existing JSONL write |
| `vibe_trades` | timestamp, instrument, side, entry/stop/tp1/tp2, outcome (TP1/TP2/SL/EOD), realized P&L | same script, on a resolved fill |
| `haulyeah_leads` | timestamp, contact hash (not raw PII beyond what Sheets already stores), source, outcome, follow-up status | `haulyeah_lead_review.py`-family scripts |
| `agent_lessons` (optional, lower priority) | timestamp, pillar, lesson text, source doc | `ruflo-scorecard`/`hermes-self-review`, mirroring `RUFLO_LESSONS_*.md` |

### Data flow
1. Existing `no_agent` scripts keep writing their current JSONL logs exactly
   as they do today — **this is additive, not a replacement.** JSONL stays
   the audit-of-record; Supabase is a queryable mirror.
2. Each script gets a small addition (same shape as `push_cron_health.py`):
   after its normal work, upsert the new rows it just produced.
3. Read side: Hermes chat-skill commands and brief-generation cron jobs query
   Supabase directly instead of shelling out to grep/parse JSONL files.
4. Graphify's role is unchanged: still the nightly AST code graph, still
   consumed by Claude Code sessions and the vault copy, still untouched by
   any of this.

### Failure modes
- **Supabase unreachable when a script tries to write:** fail closed exactly
  like every other fallback pattern already in this codebase (mandate
  fallback, `is_live_enabled`'s fail-closed eval_gate check) — log the
  failure, keep the JSONL write (already happened first), never block or
  crash the actual trading/lead-processing cycle over a memory-write failure.
- **Supabase unreachable when something tries to read for context:** return
  an empty/absent context and say so plainly in the chat response ("no
  historical context available") rather than silently proceeding as if there
  were no prior setups — silence-as-success is exactly the confabulation
  failure mode this whole audit pass has been hunting.
- **Schema drift** (a script writes a field the table doesn't have): additive
  columns only, never a breaking rename, same discipline as the
  `lucid_mandate.json` single-source-of-truth fix in Phase 1.

### The hard line (restated, because it's the one that matters)
**No query against this memory layer may ever override, soften, or bypass a
risk rule.** `risk_guard.py` reads `lucid_mandate.json` and nothing else for
gating. Memory can tell Ronnie "this setup type usually wins" in a chat
response; it can never tell `RiskGuard.check()` to approve a size it
otherwise wouldn't, skip the consistency cap, or waive the kill switch. If a
future build ever proposes wiring memory into the risk path, that's a
separate, explicitly-flagged decision — not something this design enables by
default.

---

## 4. Phased build estimate

- **Phase A (small, ~1 session):** create the 2–3 core tables in the
  existing Supabase project; add the write-side addition to
  `vibe_paper_scan.py`/`vibe_eod_summary.py` and one HaulYeah script, mirroring
  `push_cron_health.py`'s proven pattern exactly. Purely additive logging —
  no new decision logic anywhere.
- **Phase B (medium, ~1 session):** read-side — update the `trade brief` /
  `bot status` / `loss analysis` chat-skill commands to query Supabase for
  historical context; add a similar historical-context query to
  `haulyeah_lead_review`.
- **Phase C (larger, optional, only if A/B prove useful):** richer analytics
  — win-rate-by-setup-type views, a proper lead-history view — built once
  there's real usage signal that the simple version is being used and useful.

Each phase should get its own approval gate, same as this whole pass has used
— build the smallest useful slice, verify it end-to-end (same discipline as
Phases 1–3: manual dry runs, independent Supabase queries, not "it printed
success"), then decide whether the next phase is worth it.
