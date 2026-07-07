# Hermes Ecosystem Pipeline Design
**Date:** 2026-06-28  
**Scope:** Nightly pipeline redesign (Approach B) + bug fixes + Ruflo automation + Graphify code graph + Hermes self-review (full auto-fix + audit trail) + Obsidian graph wiring  
**Status:** Approved — ready for implementation

---

## Problem Statement

The Hermes ecosystem has 14 cron jobs but no coherent nightly pipeline. Key issues found:

1. `haulyeah-tight-leads` + `haulyeah-lead-review` are delivering to a **deleted Telegram group** (-5282143697) — delivery fails silently every day
2. `claude-daily-compact` Step 1 picks up `COMPACT_ANALYSIS_*` files as session logs (no date filter on dir command)
3. `daily-memory-keeper` (2 AM) has **no context** about what happened that day — it can only read the existing sparse MEMORY.md and guess
4. `ruflo-scorecard` doesn't exist — the Ruflo cowork routine is manual-only (`/schedule` in Claude Code)
5. `ruflo_skill.md` is generic philosophy, not operational steps
6. Obsidian vault notes lack the wikilinks needed for graph view to show project relationships
7. Minor: encoding artifacts in 2 job prompts, vague haulyeah-weekly-review prompt

---

## Architecture: Nightly Pipeline (Approach B)

Four automated stages. Each stage reads the prior stage's output file as its source of truth.

```
23:00  claude-daily-compact    ──→  CC-Session-Logs/COMPACT_ANALYSIS_YYYY-MM-DD.md
                                    Vault: 45 - System/SESSION_COMPACT_CURRENT.md (append)
                                    CC-Session-Logs/COMPACT_READY_YYYY-MM-DD.flag
                                    Telegram: goal moves + commits + blockers

00:00  ruflo-scorecard (NEW)   ──→  reads COMPACT_ANALYSIS_YYYY-MM-DD.md
                                    CC-Session-Logs/RUFLO_LESSONS_YYYY-MM-DD.md
                                    Telegram: routine score + lesson proposals

01:00  hermes-self-review      ──→  reads RUFLO_LESSONS + jobs.json + SKILL.md files + scripts/
       (NEW, full auto-fix)         runs graphify update on ClawBot repo
                                    auto-applies safe fixes (encoding, paths, models, delivery)
                                    CC-Session-Logs/SELF_REVIEW_CHANGES_YYYY-MM-DD.md (audit log)
                                    Telegram: N fixes applied + N proposals sent

02:00  daily-memory-keeper     ──→  reads COMPACT_ANALYSIS + RUFLO_LESSONS + SELF_REVIEW_CHANGES
                                    Hermes memories/MEMORY.md (append only)
                                    Telegram: memory facts appended count
```

**Chain failure policy:** If prior stage's file is missing, job notes `[STALE_CHAIN: missing X]` in its output and proceeds on what it has (does not abort).

---

## Changes by Component

### 1. jobs.json — Fix delivery targets

**`haulyeah-tight-leads`** and **`haulyeah-lead-review`**:
- Change `origin.chat_id` from `-5282143697` (deleted group "Ron and hermes") to `6082698835` (personal Ronnie chat)
- Change `origin.chat_name` from `"Ron and hermes"` to `"Ronnie Irizarry"`

### 2. jobs.json — Fix claude-daily-compact Step 1

Current Step 1 dir command:
```
dir "C:\Users\ronsi95openclaw\CC-Session-Logs\*.md" /b /o-d
```

Replace with:
```
dir "C:\Users\ronsi95openclaw\CC-Session-Logs\??-??-????*.md" /b /o-d
```

The `??-??-????` glob matches date-formatted files only (e.g. `27-06-2026-*.md`). It skips `COMPACT_ANALYSIS_*.md`, `DAILY_COMPACT_SCHEDULE_PROMPT.md`, and any other non-session files.

### 3. jobs.json — Add ruflo-scorecard (new job)

**ID:** `ruflo-scorecard`  
**Name:** `ruflo-scorecard`  
**Schedule:** `0 0 * * *` (midnight nightly, after 23:00 compact)  
**Model:** `deepseek/deepseek-chat` via openrouter  
**Deliver to:** personal chat 6082698835  
**enabled_toolsets:** `["file", "terminal"]`  

**Prompt** (full):
```
You are running the nightly Ruflo scorecard for Ronnie's AI ecosystem.
Source of truth: the COMPACT_ANALYSIS written by claude-daily-compact at 23:00.

STEP 1 — READ COMPACT ANALYSIS:
Use run_command: dir "C:\Users\ronsi95openclaw\CC-Session-Logs\COMPACT_ANALYSIS_*.md" /b /o-d
Take the first result. Use read_file to read it.
If not found: output [STALE_CHAIN: no compact analysis found] and skip to STEP 4.

STEP 2 — READ DAILY ROUTINE:
Use read_file: C:\Users\ronsi95openclaw\Documents\Obsidian Vault\20 - OpenClaw\Memory\OPENCLAW_DAILY_ROUTINE.md
Extract the 10 checkpoints (steps 0,2,4,6,7,8,9,10,11,12).

STEP 3 — SCORE EACH CHECKPOINT:
Based on what the compact analysis says happened, score each:
✅ DONE — explicitly mentioned as completed
⚠️ PARTIAL — mentioned but incomplete or uncertain
❌ MISSED — not mentioned, skipped, or noted as missed

Checkpoints:
□ Step 0: Contracts loaded (SESSION_HANDOFF + ACTIVE_TASKS + CHANGES.md read)
□ Step 2: Bot health checked (ClawBot process, Ollama, Crypto.com / system health)
□ Step 4: Compliance 7/7 checked
□ Step 6: Auto-fixes applied / proposals logged
□ Step 7: CHANGES.md updated
□ Step 8: Trade journal written
□ Step 9: Memory files updated
□ Step 10: sync_to_vault.bat ran
□ Step 11: Local git commit
□ Step 12: EOD Telegram sent

Score: X/10

STEP 4 — APPLY 3 RUFLO PATTERNS:
Scan the compact analysis and your checkpoint scores for improvements.

STRUCTURED PLANNING (routine gaps that need a new cron or reminder):
RUFLO LESSON | structured-planning | <gap> | Proposed fix: <cron job or process change> | AWAITING RONNIE APPROVAL

TASK OBSERVATION (steps that silently failed with no log entry):
RUFLO LESSON | task-observation | <step> silent | Add check to: <file or skill> | AWAITING RONNIE APPROVAL

LEARNING + MEMORY (knowledge Hermes or Claude memory is missing that caused repeated gaps):
RUFLO LESSON | learning-memory | <gap> | Add to: MEMORY.md or skills/<name>/SKILL.md | AWAITING RONNIE APPROVAL

Only emit a RUFLO LESSON line if the lesson is real and non-trivial. Omit the pattern section if no lessons apply.

STEP 5 — WRITE RUFLO LESSONS FILE:
Use write_file: C:\Users\ronsi95openclaw\CC-Session-Logs\RUFLO_LESSONS_<YYYY-MM-DD>.md
Content:
# Ruflo Lessons — <DD-MM-YYYY>

## Routine Scorecard
<checkpoint table: Step | Status | Note>
Score: X/10
Missed steps: <list or None>

## Ruflo Lessons
<RUFLO LESSON lines, one per finding, or [NONE] if clean>

## Goal Progress
<Copy from compact analysis>

STEP 6 — TELEGRAM OUTPUT:
📐 Ruflo Scorecard — <DD-MM-YYYY>
Routine: X/10 | Missed: <steps or None>
Goal moves: ClawBot=<X> • HaulYeah=<X> • Vibe=<X> • Hermes=<X>
Ruflo lessons: <N> new | <list titles or "None">
File: RUFLO_LESSONS_<YYYY-MM-DD>.md ✅
Blockers: <from compact or None>
```

### 4. jobs.json — Rewrite daily-memory-keeper prompt

Replace current prompt with:
```
Daily memory keeper: chain from tonight's compact analysis.

STEP 1 — FIND COMPACT ANALYSIS:
Use run_command: dir "C:\Users\ronsi95openclaw\CC-Session-Logs\COMPACT_ANALYSIS_*.md" /b /o-d
Take the first result. Use read_file to read it.
If not found: output [STALE_CHAIN: no compact analysis] — do not modify MEMORY.md. Stop.

STEP 2 — FIND RUFLO LESSONS (optional):
Use run_command: dir "C:\Users\ronsi95openclaw\CC-Session-Logs\RUFLO_LESSONS_*.md" /b /o-d
If found, read_file the first result. Extract any RUFLO LESSON lines.

STEP 3 — READ CURRENT MEMORY:
Use read_file: C:\Users\ronsi95openclaw\AppData\Local\hermes\memories\MEMORY.md

STEP 4 — EXTRACT DURABLE FACTS:
From the compact analysis, extract facts worth remembering across future sessions.
Durable = state changes, new tools/skills, fixed bugs with root cause, open blockers.
NOT durable = routine task completions, scores, file names, one-day context.

Skip anything already in MEMORY.md (check exact phrasing).

STEP 5 — APPEND TO MEMORY (append only, never edit/delete):
If durable facts found, append to MEMORY.md:

## Auto-compact <YYYY-MM-DD> (memory-keeper)
- <fact 1>
- <fact 2>
(max 5 bullets; keep each under 2 lines)

If nothing new: output [SILENT — no new durable facts] and do not modify the file.

STEP 6 — TELEGRAM OUTPUT:
🧠 Memory Keeper — <DD-MM-YYYY>
Source: COMPACT_ANALYSIS_<date>.md
Facts appended: <N> (or [SILENT])
<bullets of what was added, or "Nothing new">
```

### 5. jobs.json — Fix encoding artifacts

**`trade-eod-flatten-reminder`** prompt: replace `ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â` (garbled em dash) with ` —`  

**`all-projects-daily-brief`** prompt: same replacement — all `Ã...` sequences are corrupted em dashes → replace with ` —`

### 6. jobs.json — Fix haulyeah-weekly-review

Replace vague "read log files in trash_hauling_bot/" with:
```
(1) read_file: C:/Users/ronsi95openclaw/Claude-openclaw/trash_hauling_bot/data/audit.log (last 200 lines via run_command: Get-Content ... -Tail 200)
(2) Count lead_added events in the last 7 days. Count jobs with status=confirmed.
(3) read_file: C:/Users/ronsi95openclaw/Claude-openclaw/trash_hauling_bot/data/revenue.json
(4) Check if HaulYeah bot process is running: run_command: tasklist | findstr python
```

---

## Graphify — Code Knowledge Graph

Graphify is already installed at `Claude-openclaw\.venv\Scripts\graphify.exe`. The output directory `graphify-out/` does not yet exist.

### One-time bootstrap
Run `graphify update .` from `Claude-openclaw/` to build the initial graph. This is AST-only (no LLM, no API cost, fast). Output: `graphify-out/graph.json` + `graphify-out/wiki/index.md` + `graphify-out/GRAPH_REPORT.md`.

### Install graphify skill into Hermes
Run: `graphify install --platform hermes` from `Claude-openclaw/`
This registers the graphify query tools so Hermes can call `graphify query`, `graphify path`, and `graphify explain` as terminal commands.

### How hermes-self-review uses graphify
Before applying any fix that touches code structure:
1. `graphify query "<what I'm about to change>"` — understand what imports/calls the target
2. `graphify path "<fileA>" "<fileB>"` — check if two files share a dependency path
3. Only after understanding the impact does it apply the fix

### Daily graph update in hermes-self-review
Step 1 of the self-review job runs: `graphify update .` (updates the graph with any code changes from the day). This keeps the graph current without any manual maintenance.

---

## New Job: `hermes-self-review`

**Schedule:** `0 1 * * *` (1 AM, after ruflo at midnight)  
**Model:** `deepseek/deepseek-chat` via openrouter  
**Deliver to:** personal chat 6082698835  
**workdir:** `C:\Users\ronsi95openclaw`  
**enabled_toolsets:** `["file", "terminal"]`

### What it reviews
1. **jobs.json** — delivery targets, model names, encoding artifacts, broken file paths in prompts
2. **SKILL.md files** — stale file paths, outdated model references, dead skill steps
3. **scripts/** — Python syntax check, import errors, outdated paths
4. **config.yaml warnings** — from agent.log (recurring WARNING lines)
5. **RUFLO_LESSONS** — action items flagged for code-level fixes

### Auto-fix categories (no approval needed, full auto-apply)
| Category | Examples |
|---|---|
| Encoding artifacts | Double-UTF-8 em dashes, garbled unicode in prompts |
| Dead delivery targets | Deleted group chats, wrong chat_ids caught in delivery errors |
| Stale model names | Deprecated model IDs → current equivalent |
| Broken file paths | Paths that don't exist → corrected from context |
| Dir command filters | `*.md` without date filter → `??-??-????*.md` pattern |

### Proposal categories (AWAITING RONNIE APPROVAL in Telegram)
| Category | Examples |-
|---|---|
| Schedule changes | Moving a job to a different time |
| Logic changes | Rewriting a cron prompt's core behavior |
| New jobs | Proposing a new cron job |
| Script logic | Changing business rules in Python scripts |
| Config changes | agent.max_turns, model routing rules |

### Audit trail
Every run writes `CC-Session-Logs/SELF_REVIEW_CHANGES_YYYY-MM-DD.md`:
```markdown
# Self-Review Changes — DD-MM-YYYY

## Auto-applied fixes
- [jobs.json] haulyeah-weekly-review: fixed audit.log path (was relative, now absolute)
- [SKILL.md: ruflo] updated pipeline file paths to match current structure

## Proposals (AWAITING RONNIE APPROVAL)
- PROPOSAL | schedule | compact job at 23:00 fires too early on weekends | Shift to 23:30 | AWAITING APPROVAL

## Graphify update
- graphify update ran: N nodes, N edges, N files changed since last run
```

### Full prompt for jobs.json
```
You are running the nightly Hermes self-review. You have full authority to auto-apply
safe fixes and MUST write every change to the audit log. You NEVER skip the audit log.

STEP 1 — UPDATE CODE GRAPH:
run_command: C:\Users\ronsi95openclaw\Claude-openclaw\.venv\Scripts\graphify.exe update C:\Users\ronsi95openclaw\Claude-openclaw
Note the output (nodes, edges, files changed). If graphify-out/ does not exist, this creates it.

STEP 2 — READ RUFLO LESSONS:
run_command: dir "C:\Users\ronsi95openclaw\CC-Session-Logs\RUFLO_LESSONS_*.md" /b /o-d
If found, read_file the first result. Extract any RUFLO LESSON lines as action items.

STEP 3 — AUDIT HERMES INFRASTRUCTURE:
Read and check each of the following for issues:

a. read_file: C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json
   Check: delivery targets (chat_id -* group chats may be deleted), model names, encoding artifacts,
   file paths in prompts, dir commands without date filters.

b. Use search_files to list all SKILL.md files under:
   C:\Users\ronsi95openclaw\AppData\Local\hermes\skills\ronsi95\
   Read each one. Check: file paths that no longer exist, outdated model names,
   references to retired features (e.g., Crypto.com exchange).

c. Use search_files to list all .py files under:
   C:\Users\ronsi95openclaw\AppData\Local\hermes\scripts\
   For each: run_command: python -m py_compile <path> 2>&1
   Flag any syntax errors.

d. run_command: Get-Content C:\Users\ronsi95openclaw\AppData\Local\hermes\logs\agent.log -Tail 200
   Extract recurring WARNING lines (appearing 3+ times). These indicate config issues.

STEP 4 — CLASSIFY EACH ISSUE:
For each issue found, classify as:
AUTO-FIX: encoding artifacts, dead chat_ids confirmed in delivery errors, broken file paths,
          stale model names (check if model is in config.yaml providers list),
          dir commands missing date filter.
PROPOSAL: schedule changes, prompt logic rewrites, new jobs, script business logic,
          config.yaml value changes.

STEP 5 — APPLY AUTO-FIXES:
For each AUTO-FIX:
a. Use graphify query to verify no downstream impact if the fix touches code structure.
b. Apply the fix using write_file (for jobs.json or SKILL.md) or by correcting the file.
c. Log each fix with: [FIXED] file | issue | what was changed

For PROPOSAL items:
d. Log each with: PROPOSAL | category | issue | proposed change | AWAITING RONNIE APPROVAL

STEP 6 — WRITE AUDIT LOG:
write_file: C:\Users\ronsi95openclaw\CC-Session-Logs\SELF_REVIEW_CHANGES_<YYYY-MM-DD>.md
Content:
# Self-Review Changes — <DD-MM-YYYY>

## Auto-applied fixes (<N total>)
<[FIXED] lines>

## Proposals (<N total>)
<PROPOSAL lines>

## Graphify update
<output from Step 1>

## Agent log warnings (recurring)
<warning patterns found>

STEP 7 — TELEGRAM OUTPUT:
🔧 Self-Review — <DD-MM-YYYY>
Auto-fixed: <N> | Proposals: <N>
<list of fixed items, max 5 lines>
<list of proposals, max 3 lines>
Full log: SELF_REVIEW_CHANGES_<YYYY-MM-DD>.md
```

---

## ruflo_skill.md Rewrite

File: `C:\Users\ronsi95openclaw\Documents\Obsidian Vault\ai_core\skills\ruflo_skill.md`

Transform from philosophy to operational spec:

### New structure:
1. **Purpose** — what Ruflo means for this system (1 paragraph)
2. **The nightly pipeline** — the 3-stage chain with file paths and timing
3. **The 10 routine checkpoints** — scoring rubric with what counts as ✅/⚠️/❌ for each
4. **The 3 lesson patterns** — with concrete examples from this ecosystem
5. **Proposal format** — exact `RUFLO LESSON | pattern | gap | fix | AWAITING APPROVAL` syntax
6. **How to act on proposals** — Ronnie approves in Telegram → Claude Code implements → marked done in next compact

---

## Obsidian Graph View — Wikilink Fixes

Goal: all project memory notes reachable from `Home.md` via wikilinks.

**Files that need wikilinks added/fixed:**

| File | Add link to |
|---|---|
| `45 - System/SESSION_COMPACT_CURRENT.md` | `[[45 - System MOC]]` or `[[Home]]` |
| `20 - OpenClaw/Memory/OPENCLAW_DAILY_ROUTINE.md` | `[[OPENCLAW_MASTER_INDEX]]` |
| `20 - OpenClaw/Memory/SESSION_HANDOFF.md` | `[[OPENCLAW_MASTER_INDEX]]` |
| `35 - HaulYA'LL/HAULYALL_SESSION_HANDOFF.md` | HaulYA'LL MOC |
| `ai_core/skills/ruflo_skill.md` | link FROM `25 - AI` MOC → `[[ruflo_skill]]` |

**Implementation:** use the vault `connect` skill to propose links per note, gate before writing. No bulk rewrites.

---

## Implementation Order

1. **Graphify bootstrap** — run `graphify update .` from Claude-openclaw/, run `graphify install --platform hermes`
2. **jobs.json** — fix delivery targets (haulyeah jobs) + date filter (compact Step 1) + encoding fixes + haulyeah-weekly-review
3. **jobs.json** — rewrite daily-memory-keeper prompt (chain from compact + ruflo + self-review)
4. **jobs.json** — add ruflo-scorecard job (new entry, 00:00)
5. **jobs.json** — add hermes-self-review job (new entry, 01:00)
6. **ruflo_skill.md** — full rewrite (operational spec with pipeline docs)
7. **Obsidian vault** — wikilink additions via `connect` skill
8. **CLAUDE.md** — update graphify section to note `graphify-out/` is built and Hermes is installed

---

## Success Criteria

- `haulyeah-tight-leads` and `haulyeah-lead-review` Telegram delivery succeeds (no "Forbidden" error)
- `claude-daily-compact` at 23:00 only reads session log files (not COMPACT_ANALYSIS)
- `ruflo-scorecard` fires at 00:00, writes `RUFLO_LESSONS_YYYY-MM-DD.md`, sends Telegram
- `hermes-self-review` fires at 01:00, runs `graphify update`, auto-fixes infrastructure, writes `SELF_REVIEW_CHANGES_YYYY-MM-DD.md`, sends Telegram
- `daily-memory-keeper` at 02:00 reads COMPACT_ANALYSIS + RUFLO_LESSONS + SELF_REVIEW_CHANGES and appends real durable facts to MEMORY.md
- `graphify-out/graph.json` exists and `graphify query` works from Claude-openclaw/
- `ruflo_skill.md` documents the 4-stage pipeline + scoring rubric + lesson format + self-review categories
- Obsidian graph view shows SESSION_COMPACT_CURRENT, routine files, and ruflo_skill in the connected graph

---

## Out of Scope

- Changing Hermes main model (stays DeepSeek V3)
- Modifying any trading logic or HaulYeah scraper business logic
- Automating the Claude Code `/schedule` cowork scorecard (that stays manual via `/schedule`)
- Any new cron jobs beyond ruflo-scorecard and hermes-self-review
