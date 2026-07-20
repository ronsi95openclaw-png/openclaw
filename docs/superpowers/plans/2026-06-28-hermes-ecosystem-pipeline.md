# Hermes Ecosystem Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up a fully automated 4-stage nightly pipeline (compact → ruflo → self-review → memory), fix all delivery/encoding bugs in existing cron jobs, build the graphify code graph, and make Hermes self-improving with full auto-fix authority.

**Architecture:** Four chained cron jobs (23:00 / 00:00 / 01:00 / 02:00) each writing output files the next stage reads. hermes-self-review uses graphify to understand the codebase before applying fixes. All changes land in jobs.json + ruflo_skill.md + vault wikilinks.

**Tech Stack:** Hermes cron (jobs.json at `%LOCALAPPDATA%\hermes\cron\jobs.json`), graphify CLI (already in `.venv/Scripts/graphify.exe`), DeepSeek V3 via OpenRouter, Obsidian vault wikilinks.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `AppData/Local/hermes/cron/jobs.json` | Modify | Fix 5 existing jobs + add 2 new jobs |
| `Claude-openclaw/graphify-out/` | Create (auto by CLI) | Code knowledge graph |
| `Claude-openclaw/CLAUDE.md` | Modify | Note graphify-out is built + Hermes installed |
| `Obsidian Vault/ai_core/skills/ruflo_skill.md` | Rewrite | Operational spec replacing philosophy doc |
| `Obsidian Vault/45 - System/SESSION_COMPACT_CURRENT.md` | Modify | Add wikilink up to Home |
| `Obsidian Vault/20 - OpenClaw/Memory/OPENCLAW_DAILY_ROUTINE.md` | Modify | Add wikilink to OPENCLAW_MASTER_INDEX |
| `Obsidian Vault/20 - OpenClaw/Memory/SESSION_HANDOFF.md` | Modify | Add wikilink to OPENCLAW_MASTER_INDEX |
| `Obsidian Vault/25 - AI/` (MOC file, find via scan) | Modify | Add wikilink entry for ruflo_skill |

---

## Task 1: Graphify Bootstrap

**Files:**
- Create: `Claude-openclaw/graphify-out/` (auto-created by CLI)
- Modify: `Claude-openclaw/CLAUDE.md`

- [ ] **Step 1: Build the initial code graph**

Run from `Claude-openclaw/`:
```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
.\.venv\Scripts\graphify.exe update .
```

Expected output: lines like `Processed N files, N nodes, N edges → graphify-out/graph.json`  
If it errors with "no files found", try: `.\.venv\Scripts\graphify.exe update . --include "*.py"`

- [ ] **Step 2: Verify graph was built**

```powershell
Test-Path C:\Users\ronsi95openclaw\Claude-openclaw\graphify-out\graph.json
```
Expected: `True`

```powershell
Get-Content C:\Users\ronsi95openclaw\Claude-openclaw\graphify-out\graph.json | ConvertFrom-Json | Select-Object -ExpandProperty nodes | Measure-Object | Select-Object Count
```
Expected: Count > 0

- [ ] **Step 3: Install graphify skill into Hermes**

```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
.\.venv\Scripts\graphify.exe install --platform hermes
```

Expected: output confirming Hermes config updated, or a path was written.  
If the command errors, check: `.\.venv\Scripts\graphify.exe install --help` for the correct platform flag name.

- [ ] **Step 4: Verify graphify query works**

```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
.\.venv\Scripts\graphify.exe query "strategy"
```
Expected: output listing files related to strategy (strategy.py, backtest.py, etc.)

- [ ] **Step 5: Update CLAUDE.md graphify section**

Edit `C:\Users\ronsi95openclaw\Claude-openclaw\CLAUDE.md`. Find the `## graphify` section and update it:

```markdown
## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.
**Status:** Built and active. Hermes platform installed.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- hermes-self-review cron (01:00 nightly) runs `graphify update .` automatically.
```

- [ ] **Step 6: Commit**

```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
git add graphify-out/ CLAUDE.md
git commit -m "feat(graphify): bootstrap code graph + install Hermes platform skill"
```

---

## Task 2: Fix jobs.json — Delivery Targets

**Files:**
- Modify: `C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json`

`haulyeah-tight-leads` (id: `73e9e69d518f`) and `haulyeah-lead-review` (id: `a4f7e2c91b3d`) both deliver to a deleted Telegram group. Fix the `origin` block in each.

- [ ] **Step 1: Fix haulyeah-tight-leads delivery target**

In `jobs.json`, find the block for job id `73e9e69d518f`. Replace its `origin` object:

Old:
```json
      "origin": {
        "platform": "telegram",
        "chat_id": "-5282143697",
        "chat_name": "Ron and hermes",
        "thread_id": null
      },
```

New:
```json
      "origin": {
        "platform": "telegram",
        "chat_id": "6082698835",
        "chat_name": "Ronnie Irizarry",
        "thread_id": null
      },
```

- [ ] **Step 2: Fix haulyeah-lead-review delivery target**

In `jobs.json`, find the block for job id `a4f7e2c91b3d`. Replace its `origin` object with the same fix:

Old:
```json
      "origin": {
        "platform": "telegram",
        "chat_id": "-5282143697",
        "chat_name": "Ron and hermes",
        "thread_id": null
      },
```

New:
```json
      "origin": {
        "platform": "telegram",
        "chat_id": "6082698835",
        "chat_name": "Ronnie Irizarry",
        "thread_id": null
      },
```

- [ ] **Step 3: Validate JSON is still valid**

```powershell
Get-Content "C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json" | ConvertFrom-Json | Select-Object -ExpandProperty jobs | Measure-Object | Select-Object Count
```
Expected: Count = 14 (existing jobs, before we add new ones)

- [ ] **Step 4: Commit**

```powershell
git -C C:\Users\ronsi95openclaw\Claude-openclaw commit --allow-empty -m "fix(hermes): redirect deleted group chat deliveries to personal Telegram"
```

Note: jobs.json is not in the Claude-openclaw git repo. After validating, note in session log that this was done manually.

---

## Task 3: Fix jobs.json — Compact Date Filter + Encoding + Haulyeah Weekly

**Files:**
- Modify: `C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json`

Three independent fixes in one commit: date filter on compact, encoding artifacts, haulyeah-weekly prompt.

- [ ] **Step 1: Fix claude-daily-compact Step 1 dir filter**

In `jobs.json`, find the `claude-daily-compact` job (id: `claude-daily-compact`). In its `prompt` field, find and replace:

Old string in prompt:
```
dir \"C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\*.md\" /b /o-d
```

New string:
```
dir \"C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\??-??-????*.md\" /b /o-d
```

This glob matches `DD-MM-YYYY*.md` format only, skipping `COMPACT_ANALYSIS_*` and reference files.

- [ ] **Step 2: Fix encoding artifacts in trade-eod-flatten-reminder**

In `jobs.json`, find the job id `b7d2e8f16a3c` (`trade-eod-flatten-reminder`). In its `prompt` field, find:

```
PAPER MODE ONLY ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â do not execute any trades.
```

Replace with:
```
PAPER MODE ONLY — do not execute any trades.
```

- [ ] **Step 3: Fix encoding artifacts in all-projects-daily-brief**

In `jobs.json`, find the job id `f1b3d8e92c4a` (`all-projects-daily-brief`). In its `prompt` field, replace every occurrence of the garbled sequence `ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â` with ` —`.

There are 3 occurrences (one after each pillar heading: HAULYEAH, VIBE TRADING, SYSTEM HEALTH).

- [ ] **Step 4: Fix haulyeah-weekly-review prompt**

In `jobs.json`, find the job id `c5e1a0b38f7d` (`haulyeah-weekly-review`). Replace its `prompt` field value with:

```
Sunday HaulY'all weekly review. Steps:\n\n(1) Get leads and jobs from this week:\nrun_command: Get-Content C:\\Users\\ronsi95openclaw\\Claude-openclaw\\trash_hauling_bot\\data\\audit.log -Tail 500\nCount lead_added events from the last 7 days. Count entries with status=confirmed (these are booked jobs).\n\n(2) Revenue this week:\nread_file: C:\\Users\\ronsi95openclaw\\Claude-openclaw\\trash_hauling_bot\\data\\revenue.json\nSum all entries from the last 7 days.\n\n(3) Bot health check:\nrun_command: tasklist | findstr python\nReport which bot processes are running.\n\n(4) Output (max 10 lines):\n- Leads this week: N\n- Jobs confirmed: N\n- Revenue: $X (or $0 if revenue.json empty)\n- Pipeline: N leads still in pending_outreach.json\n- Bot status: running/stopped\n- Next week focus: <1-2 priorities>\n\nAWAITING RONNIE APPROVAL before any actions.
```

- [ ] **Step 5: Validate JSON**

```powershell
Get-Content "C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json" | ConvertFrom-Json | Select-Object -ExpandProperty jobs | Select-Object name, id | Format-Table
```

Expected: all 14 jobs listed, no parse error.

---

## Task 4: Rewrite daily-memory-keeper Prompt

**Files:**
- Modify: `C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json`

- [ ] **Step 1: Replace daily-memory-keeper prompt**

In `jobs.json`, find the job id `d9f4b2e50c1a` (`daily-memory-keeper`). Replace its entire `prompt` field value with:

```
Daily memory keeper: chain from tonight's compact analysis and ruflo lessons.\n\nSTEP 1 — FIND COMPACT ANALYSIS:\nrun_command: dir \"C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\COMPACT_ANALYSIS_*.md\" /b /o-d\nTake the first result. Use read_file to read it.\nIf not found: output [STALE_CHAIN: no compact analysis] — do not modify MEMORY.md. Stop.\n\nSTEP 2 — FIND RUFLO LESSONS (optional):\nrun_command: dir \"C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\RUFLO_LESSONS_*.md\" /b /o-d\nIf found, read_file the first result. Extract any RUFLO LESSON lines.\n\nSTEP 3 — FIND SELF-REVIEW CHANGES (optional):\nrun_command: dir \"C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\SELF_REVIEW_CHANGES_*.md\" /b /o-d\nIf found, read_file the first result. Note any auto-applied fixes (these are durable facts).\n\nSTEP 4 — READ CURRENT MEMORY:\nread_file: C:\\Users\\ronsi95openclaw\\AppData\\Local\\hermes\\memories\\MEMORY.md\n\nSTEP 5 — EXTRACT DURABLE FACTS:\nFrom the compact analysis (and ruflo + self-review if available), extract facts worth remembering across future sessions.\nDurable = project state changes, new tools/skills, bugs fixed with root cause, open blockers, architectural decisions.\nNOT durable = routine task scores, checkpoint results, one-day context, file names without significance.\nSkip anything already in MEMORY.md (check exact phrasing before adding).\n\nSTEP 6 — APPEND TO MEMORY (append only, never edit/delete existing lines):\nIf durable facts found, append to MEMORY.md:\n\n## Auto-compact <YYYY-MM-DD> (memory-keeper)\n- <fact 1>\n- <fact 2>\n(max 5 bullets; keep each under 2 lines)\n\nIf nothing new: output [SILENT — no new durable facts] and do not modify the file.\n\nSTEP 7 — TELEGRAM OUTPUT:\n🧠 Memory Keeper — <DD-MM-YYYY>\nSource: COMPACT_ANALYSIS_<date>.md\nRuflo lessons read: <N or none>\nSelf-review changes read: <N fixes or none>\nFacts appended: <N> (or [SILENT])\n<bullets of what was added, or 'Nothing new this session'>
```

- [ ] **Step 2: Also update the model field** (ensure it stays DeepSeek for this job)

Confirm that the job's `model` field is `"deepseek/deepseek-chat"` and `provider` is `"openrouter"`. If currently `null`, set them explicitly.

- [ ] **Step 3: Validate JSON**

```powershell
Get-Content "C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json" | ConvertFrom-Json | Select-Object -ExpandProperty jobs | Where-Object { $_.name -eq "daily-memory-keeper" } | Select-Object name, model
```

Expected: `name=daily-memory-keeper`, `model=deepseek/deepseek-chat`

---

## Task 5: Add ruflo-scorecard Job

**Files:**
- Modify: `C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json`

- [ ] **Step 1: Insert ruflo-scorecard into the jobs array**

In `jobs.json`, add the following job object to the `"jobs"` array (insert before the closing `]`, after the last existing job). Add a comma after the preceding entry:

```json
{
  "id": "ruflo-scorecard",
  "name": "ruflo-scorecard",
  "prompt": "You are running the nightly Ruflo scorecard for Ronnie's AI ecosystem.\nSource of truth: the COMPACT_ANALYSIS written by claude-daily-compact at 23:00.\n\nSTEP 1 — READ COMPACT ANALYSIS:\nrun_command: dir \"C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\COMPACT_ANALYSIS_*.md\" /b /o-d\nTake the first result. read_file it.\nIf not found: output [STALE_CHAIN: no compact analysis found] and skip to STEP 4.\n\nSTEP 2 — READ DAILY ROUTINE:\nread_file: C:\\Users\\ronsi95openclaw\\Documents\\Obsidian Vault\\20 - OpenClaw\\Memory\\OPENCLAW_DAILY_ROUTINE.md\nExtract the 10 checkpoints (steps 0,2,4,6,7,8,9,10,11,12).\n\nSTEP 3 — SCORE EACH CHECKPOINT:\nBased on what the compact analysis says happened, score each:\n✅ DONE — explicitly mentioned as completed\n⚠️ PARTIAL — mentioned but incomplete or uncertain\n❌ MISSED — not mentioned, skipped, or noted as missed\n\nCheckpoints:\n□ Step 0: Contracts loaded (SESSION_HANDOFF + ACTIVE_TASKS + CHANGES.md read)\n□ Step 2: Bot health checked (ClawBot process, Ollama, system health)\n□ Step 4: Compliance 7/7 checked\n□ Step 6: Auto-fixes applied or proposals logged\n□ Step 7: CHANGES.md updated\n□ Step 8: Trade journal written\n□ Step 9: Memory files updated\n□ Step 10: sync_to_vault.bat ran\n□ Step 11: Local git commit\n□ Step 12: EOD Telegram sent\n\nScore: X/10\n\nSTEP 4 — APPLY 3 RUFLO PATTERNS:\nScan the compact analysis and checkpoint scores for improvements.\n\nSTRUCTURED PLANNING (routine gaps that need a new cron or reminder):\nRUFLO LESSON | structured-planning | <gap> | Proposed fix: <cron job or process change> | AWAITING RONNIE APPROVAL\n\nTASK OBSERVATION (steps that silently failed with no log entry):\nRUFLO LESSON | task-observation | <step> silent | Add check to: <file or skill> | AWAITING RONNIE APPROVAL\n\nLEARNING + MEMORY (knowledge Hermes or Claude memory is missing):\nRUFLO LESSON | learning-memory | <gap> | Add to: MEMORY.md or skills/<name>/SKILL.md | AWAITING RONNIE APPROVAL\n\nOnly emit a RUFLO LESSON line if the lesson is real and non-trivial. Omit pattern section if no lessons apply.\n\nSTEP 5 — WRITE RUFLO LESSONS FILE:\nwrite_file: C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\RUFLO_LESSONS_<YYYY-MM-DD>.md\nContent:\n# Ruflo Lessons — <DD-MM-YYYY>\n\n## Routine Scorecard\n| Step | Checkpoint | Status | Note |\n|---|---|---|---|\n<one row per checkpoint>\nScore: X/10\nMissed steps: <list or None>\n\n## Ruflo Lessons\n<RUFLO LESSON lines, one per finding, or [NONE] if clean>\n\n## Goal Progress\n<Copy goal progress section from compact analysis>\n\nSTEP 6 — TELEGRAM OUTPUT:\n📐 Ruflo Scorecard — <DD-MM-YYYY>\nRoutine: X/10 | Missed: <steps or None>\nGoal moves: ClawBot=<X> • HaulYeah=<X> • Vibe=<X> • Hermes=<X>\nRuflo lessons: <N> new | <list lesson titles or None>\nFile: RUFLO_LESSONS_<YYYY-MM-DD>.md ✅\nBlockers: <from compact or None>",
  "skills": [],
  "skill": null,
  "model": "deepseek/deepseek-chat",
  "provider": "openrouter",
  "base_url": null,
  "script": null,
  "no_agent": false,
  "context_from": null,
  "schedule": {
    "kind": "cron",
    "expr": "0 0 * * *",
    "display": "0 0 * * *"
  },
  "schedule_display": "0 0 * * *",
  "repeat": {
    "times": null,
    "completed": 0
  },
  "enabled": true,
  "state": "scheduled",
  "paused_at": null,
  "paused_reason": null,
  "created_at": "2026-06-28T00:00:00.000000-05:00",
  "next_run_at": "2026-06-29T00:00:00-05:00",
  "last_run_at": null,
  "last_status": null,
  "last_error": null,
  "last_delivery_error": null,
  "deliver": "origin",
  "origin": {
    "platform": "telegram",
    "chat_id": "6082698835",
    "chat_name": "Ronnie Irizarry",
    "thread_id": null
  },
  "enabled_toolsets": ["file", "terminal"],
  "workdir": "C:\\Users\\ronsi95openclaw",
  "fire_claim": null
}
```

- [ ] **Step 2: Validate JSON and verify job appears**

```powershell
Get-Content "C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json" | ConvertFrom-Json | Select-Object -ExpandProperty jobs | Select-Object name, id | Format-Table
```

Expected: 15 jobs listed, including `ruflo-scorecard`.

- [ ] **Step 3: Fire a test run manually**

```powershell
C:\Users\ronsi95openclaw\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -m hermes_cli.main cron run ruflo-scorecard
```

Expected: runs without error. Check Telegram for the scorecard output. If `COMPACT_ANALYSIS_*.md` doesn't exist yet, it should output `[STALE_CHAIN: no compact analysis found]` and still send Telegram output.

---

## Task 6: Add hermes-self-review Job

**Files:**
- Modify: `C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json`

- [ ] **Step 1: Insert hermes-self-review into the jobs array**

Add the following job object to the `"jobs"` array after the ruflo-scorecard entry:

```json
{
  "id": "hermes-self-review",
  "name": "hermes-self-review",
  "prompt": "You are running the nightly Hermes self-review. You have full authority to auto-apply safe fixes and MUST write every change to the audit log. You NEVER skip writing the audit log.\n\nSAFE AUTO-FIX CATEGORIES (apply without asking):\n- Encoding artifacts: garbled unicode sequences in job prompts (replace with correct character)\n- Dead delivery targets: group chat_ids confirmed dead by delivery_error containing 'Forbidden: the group chat was deleted' → replace with 6082698835\n- Broken file paths in prompts: paths that do not exist on disk → correct from context\n- Stale model names: deprecated IDs → current equivalent from config.yaml providers list\n- Dir command missing date filter: *.md without ??-??-????* prefix\n\nPROPOSAL CATEGORIES (send to Telegram, never auto-apply):\n- Schedule changes, prompt logic rewrites, new jobs, script business logic, config.yaml values\n\nSTEP 1 — UPDATE CODE GRAPH:\nrun_command: C:\\Users\\ronsi95openclaw\\Claude-openclaw\\.venv\\Scripts\\graphify.exe update C:\\Users\\ronsi95openclaw\\Claude-openclaw\nNote the output: nodes, edges, files changed since last run.\n\nSTEP 2 — READ RUFLO LESSONS (input):\nrun_command: dir \"C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\RUFLO_LESSONS_*.md\" /b /o-d\nIf found, read_file the first result. Extract any RUFLO LESSON lines as action items for this run.\n\nSTEP 3 — AUDIT JOBS.JSON:\nread_file: C:\\Users\\ronsi95openclaw\\AppData\\Local\\hermes\\cron\\jobs.json\nCheck every job for:\n- origin.chat_id starting with '-' (negative = group chat, may be deleted) — cross-check with last_delivery_error\n- Encoding artifacts (sequences of \\u00c3, \\u00e2, etc. in prompt strings)\n- File paths in prompts that start with C:\\ — verify they exist using search_files or run_command: Test-Path\n- dir commands using *.md without ??-??-????* date filter\n\nSTEP 4 — AUDIT SKILL FILES:\nsearch_files path=C:\\Users\\ronsi95openclaw\\AppData\\Local\\hermes\\skills\\ronsi95\\ pattern=SKILL.md\nFor each SKILL.md found, read_file it. Check:\n- File paths that no longer exist (C:\\ paths in skill steps)\n- References to retired features (e.g. 'Crypto.com exchange', 'qwen-coder')\n- Model names that are no longer in config.yaml providers\n\nSTEP 5 — AUDIT SCRIPTS:\nsearch_files path=C:\\Users\\ronsi95openclaw\\AppData\\Local\\hermes\\scripts\\ pattern=*.py\nFor each .py file, run: run_command: C:\\Users\\ronsi95openclaw\\Claude-openclaw\\.venv\\Scripts\\python.exe -m py_compile \"<path>\" 2>&1\nFlag any syntax errors.\n\nSTEP 6 — AUDIT AGENT LOG WARNINGS:\nrun_command: Get-Content C:\\Users\\ronsi95openclaw\\AppData\\Local\\hermes\\logs\\agent.log -Tail 300\nExtract WARNING lines. Group duplicates. Report patterns appearing 3+ times.\n\nSTEP 7 — CLASSIFY ALL ISSUES:\nFor each issue found, label it AUTO-FIX or PROPOSAL (see categories above).\n\nSTEP 8 — APPLY AUTO-FIXES:\nFor each AUTO-FIX issue:\na. If the fix touches code structure, first run: run_command: C:\\Users\\ronsi95openclaw\\Claude-openclaw\\.venv\\Scripts\\graphify.exe query \"<what is being changed>\"\nb. Apply the fix using write_file on the affected file (jobs.json or SKILL.md).\nc. Log: [FIXED] <file> | <issue> | <what changed>\n\nFor PROPOSAL issues:\nd. Log: PROPOSAL | <category> | <issue> | <proposed change> | AWAITING RONNIE APPROVAL\n\nSTEP 9 — WRITE AUDIT LOG:\nwrite_file: C:\\Users\\ronsi95openclaw\\CC-Session-Logs\\SELF_REVIEW_CHANGES_<YYYY-MM-DD>.md\n\n# Self-Review Changes — <DD-MM-YYYY>\n\n## Auto-applied fixes (<N total>)\n<[FIXED] lines, or [NONE]>\n\n## Proposals awaiting approval (<N total>)\n<PROPOSAL lines, or [NONE]>\n\n## Graphify update\n<output from Step 1>\n\n## Agent log warnings (recurring)\n<warning patterns, or [NONE]>\n\nSTEP 10 — TELEGRAM OUTPUT:\n🔧 Self-Review — <DD-MM-YYYY>\nAuto-fixed: <N> | Proposals: <N>\n<fixed items, max 5 lines>\n<proposals, max 3 lines>\nFull log: SELF_REVIEW_CHANGES_<YYYY-MM-DD>.md ✅",
  "skills": [],
  "skill": null,
  "model": "deepseek/deepseek-chat",
  "provider": "openrouter",
  "base_url": null,
  "script": null,
  "no_agent": false,
  "context_from": null,
  "schedule": {
    "kind": "cron",
    "expr": "0 1 * * *",
    "display": "0 1 * * *"
  },
  "schedule_display": "0 1 * * *",
  "repeat": {
    "times": null,
    "completed": 0
  },
  "enabled": true,
  "state": "scheduled",
  "paused_at": null,
  "paused_reason": null,
  "created_at": "2026-06-28T01:00:00.000000-05:00",
  "next_run_at": "2026-06-29T01:00:00-05:00",
  "last_run_at": null,
  "last_status": null,
  "last_error": null,
  "last_delivery_error": null,
  "deliver": "origin",
  "origin": {
    "platform": "telegram",
    "chat_id": "6082698835",
    "chat_name": "Ronnie Irizarry",
    "thread_id": null
  },
  "enabled_toolsets": ["file", "terminal"],
  "workdir": "C:\\Users\\ronsi95openclaw",
  "fire_claim": null
}
```

- [ ] **Step 2: Validate JSON — 16 jobs total**

```powershell
Get-Content "C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json" | ConvertFrom-Json | Select-Object -ExpandProperty jobs | Select-Object name, schedule_display | Format-Table
```

Expected: 16 jobs listed. `ruflo-scorecard` at `0 0 * * *`, `hermes-self-review` at `0 1 * * *`.

- [ ] **Step 3: Update jobs.json `updated_at` timestamp**

In `jobs.json`, update the top-level `"updated_at"` field to today's date:
```json
"updated_at": "2026-06-28T00:00:00.000000-05:00"
```

- [ ] **Step 4: Fire a test run of hermes-self-review**

```powershell
C:\Users\ronsi95openclaw\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -m hermes_cli.main cron run hermes-self-review
```

Expected: runs, writes `SELF_REVIEW_CHANGES_YYYY-MM-DD.md` to CC-Session-Logs, sends Telegram message. The first run should find the existing encoding bugs we fixed in Task 3 as already resolved (since we fixed them), and may find new issues in skill files.

- [ ] **Step 5: Reload Hermes cron scheduler**

After all jobs.json edits, restart Hermes gateway so the scheduler picks up the new jobs:

```powershell
# Check current Hermes process
tasklist | findstr python

# Use the standard restart method (not force-kill):
Start-Process "C:\Users\ronsi95openclaw\AppData\Local\hermes\Hermes.exe"
```

Or if Hermes is managed by the watchdog task, it will auto-restart. Verify in agent.log:

```powershell
Get-Content C:\Users\ronsi95openclaw\AppData\Local\hermes\logs\agent.log -Tail 20
```

Expected: `Scheduler started` + `Gateway running with 1 platform(s)` + `In-process cron scheduler started`

---

## Task 7: Rewrite ruflo_skill.md

**Files:**
- Rewrite: `C:\Users\ronsi95openclaw\Documents\Obsidian Vault\ai_core\skills\ruflo_skill.md`

- [ ] **Step 1: Write the new ruflo_skill.md**

Overwrite the entire file with:

```markdown
---
name: ruflo_skill
description: "Operational Ruflo intelligence: nightly pipeline spec, routine scoring rubric, lesson patterns, and self-review categories for the Hermes ecosystem"
version: 2.0.0
author: Ronsi95
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [ruflo, pipeline, memory, self-review, scoring, automation]
    related_skills: [claude-daily-compact, hermes-memory-keeper, hermes-self-review, obsidian-vault]
---

# Ruflo Operational Intelligence — Hermes Ecosystem

## Purpose

Ruflo intelligence means: **observe what happened, score it, extract lessons, act on them automatically**.
In this ecosystem Ruflo runs as a 4-stage nightly pipeline — not a philosophy, a machine.

---

## The Nightly Pipeline

```
23:00  claude-daily-compact    ──→  CC-Session-Logs/COMPACT_ANALYSIS_YYYY-MM-DD.md
                                    Vault: 45 - System/SESSION_COMPACT_CURRENT.md
                                    Telegram: goal moves + blockers

00:00  ruflo-scorecard         ──→  reads COMPACT_ANALYSIS
                                    CC-Session-Logs/RUFLO_LESSONS_YYYY-MM-DD.md
                                    Telegram: routine score + lesson proposals

01:00  hermes-self-review      ──→  reads RUFLO_LESSONS + infrastructure files
                                    runs graphify update on ClawBot repo
                                    auto-applies safe fixes (encoding, paths, delivery, models)
                                    CC-Session-Logs/SELF_REVIEW_CHANGES_YYYY-MM-DD.md
                                    Telegram: N fixes applied + proposals

02:00  daily-memory-keeper     ──→  reads COMPACT_ANALYSIS + RUFLO_LESSONS + SELF_REVIEW_CHANGES
                                    Hermes memories/MEMORY.md (append only)
                                    Telegram: facts appended
```

Key paths:
- Session logs: `C:\Users\ronsi95openclaw\CC-Session-Logs\`
- Hermes memory: `C:\Users\ronsi95openclaw\AppData\Local\hermes\memories\MEMORY.md`
- ClawBot repo: `C:\Users\ronsi95openclaw\Claude-openclaw\`
- Graphify binary: `C:\Users\ronsi95openclaw\Claude-openclaw\.venv\Scripts\graphify.exe`
- Jobs file: `C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json`
- Skill files: `C:\Users\ronsi95openclaw\AppData\Local\hermes\skills\ronsi95\`
- Obsidian vault: `C:\Users\ronsi95openclaw\Documents\Obsidian Vault\`

Chain failure policy: if prior stage file is missing, note `[STALE_CHAIN]` and proceed on available data.

---

## Routine Scoring Rubric (10 checkpoints)

Score from COMPACT_ANALYSIS: ✅ DONE / ⚠️ PARTIAL / ❌ MISSED

| Step | Checkpoint | ✅ DONE | ⚠️ PARTIAL | ❌ MISSED |
|---|---|---|---|---|
| 0 | Contracts loaded | SESSION_HANDOFF + ACTIVE_TASKS both mentioned | Only one read | Not mentioned |
| 2 | Bot health checked | ClawBot + Ollama + system health all noted | Only one service | Not mentioned |
| 4 | Compliance 7/7 | All 7 rules confirmed | Some rules only | Not mentioned |
| 6 | Auto-fixes / proposals | Fix log entries OR proposals sent | Mentioned but not logged | Not mentioned |
| 7 | CHANGES.md updated | Explicit CHANGES.md write confirmed | Partial update | Not mentioned |
| 8 | Trade journal written | OPENCLAW_journal-YYYY-MM-DD.md confirmed | Started, incomplete | Not mentioned |
| 9 | Memory files updated | SESSION_HANDOFF + ACTIVE_TASKS updated | Partial updates | Not mentioned |
| 10 | Vault sync ran | sync_to_vault.bat completion confirmed | Started, no confirm | Not mentioned |
| 11 | Git commit | Commit hash or message mentioned | Staged not committed | Not mentioned |
| 12 | EOD Telegram sent | Balance/trades/compliance sent | Partial data | Not mentioned |

**Repeat miss rule:** same checkpoint ❌ two nights in a row → emit a Structured Planning lesson.

---

## The 3 Lesson Patterns

### Pattern 1: Structured Planning
Gap: a routine step keeps failing because there is no automated trigger for it.
Ask: "Should this become a Hermes cron job or daily reminder?"

Example:
Step 8 (trade journal) missed 3 nights.
→ `RUFLO LESSON | structured-planning | Step 8 (trade journal) missed 3× | Add Hermes cron: vault-trade-journal at 16:30 ET weekdays | AWAITING RONNIE APPROVAL`

### Pattern 2: Task Observation
Gap: a step ran but produced no log entry — silent failure with no way to audit.
Ask: "Does this script/skill write an explicit confirmation line?"

Example:
sync_to_vault.bat ran but no confirmation in session log.
→ `RUFLO LESSON | task-observation | Step 10 (vault sync) runs silently | Add log write to infra/sync_to_vault.bat | AWAITING RONNIE APPROVAL`

### Pattern 3: Learning + Memory
Gap: Hermes or Claude had to be corrected on something it already knew, or should have known.
Ask: "Is this fact in MEMORY.md? In the relevant SKILL.md?"

Example:
Hermes tried Firecrawl for Craigslist again despite prior fix.
→ `RUFLO LESSON | learning-memory | Firecrawl blocks Craigslist — recurring miss | Verify MEMORY.md entry; add explicit ban to haulyeah-agent SKILL.md | AWAITING RONNIE APPROVAL`

---

## Proposal Format

Every RUFLO LESSON line must follow this format exactly:
```
RUFLO LESSON | <pattern> | <gap description> | <proposed fix> | AWAITING RONNIE APPROVAL
```

- `<pattern>`: one of `structured-planning`, `task-observation`, `learning-memory`
- `<gap description>`: specific — which step, file, or job failed
- `<proposed fix>`: names the file, cron job, or skill to change
- Always ends: `AWAITING RONNIE APPROVAL`

Only emit if the lesson is real and non-trivial. No lessons → write `[NONE]`.

---

## How Proposals Are Acted On

1. ruflo-scorecard sends lessons to Telegram at 00:00
2. hermes-self-review at 01:00 reads RUFLO_LESSONS and auto-applies any that fall under safe categories
3. For the rest: Ronnie approves in Telegram → Claude Code implements in next session
4. Next compact marks approved lessons as "Decisions Made" in COMPACT_ANALYSIS

---

## Self-Review: Safe Auto-Fix Categories

hermes-self-review auto-applies these without approval:
- Encoding artifacts (garbled unicode in job prompts)
- Dead delivery targets (deleted group chat_ids confirmed by last_delivery_error)
- Broken file paths in prompts or skill steps (path does not exist on disk)
- Stale model names (deprecated IDs → current from config.yaml providers list)
- Dir commands missing date filter (`*.md` → `??-??-????*.md`)

Anything else → `PROPOSAL | ... | AWAITING RONNIE APPROVAL` in Telegram.

---

## Graphify Integration

Code graph location: `Claude-openclaw/graphify-out/graph.json`
Updated nightly by hermes-self-review Step 1: `graphify update .`

Before applying a fix that touches code structure:
```
graphify query "<what is being changed>"     # what imports/calls this
graphify path "<fileA>" "<fileB>"            # dependency path
graphify explain "<concept>"                 # what a node does
```

This prevents fixes from breaking downstream consumers.
```

- [ ] **Step 2: Reload skills in Hermes so it picks up the new ruflo_skill.md**

```powershell
C:\Users\ronsi95openclaw\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -m hermes_cli.main skills reload
```

Or send to Hermes via Telegram: `/reload-skills`

---

## Task 8: Obsidian Vault Wikilinks

**Files:**
- Modify: `C:\Users\ronsi95openclaw\Documents\Obsidian Vault\45 - System\SESSION_COMPACT_CURRENT.md`
- Modify: `C:\Users\ronsi95openclaw\Documents\Obsidian Vault\20 - OpenClaw\Memory\OPENCLAW_DAILY_ROUTINE.md`
- Modify: `C:\Users\ronsi95openclaw\Documents\Obsidian Vault\20 - OpenClaw\Memory\SESSION_HANDOFF.md`
- Modify: `C:\Users\ronsi95openclaw\Documents\Obsidian Vault\25 - AI\` (find the AI MOC)

- [ ] **Step 1: Find the 25 - AI MOC file**

```powershell
Get-ChildItem "C:\Users\ronsi95openclaw\Documents\Obsidian Vault\25 - AI\" -Filter "*.md" | Select-Object Name
```

Identify the MOC file (likely `AI_MAP.md`, `25-AI-MOC.md`, or similar).

- [ ] **Step 2: Add upward link to SESSION_COMPACT_CURRENT.md**

Read `45 - System/SESSION_COMPACT_CURRENT.md`. At the very top (before the first `##` heading), ensure this exists:

```markdown
up:: [[Home]]
```

If frontmatter exists, add `up: "[[Home]]"` inside it. If no frontmatter, add a bare `up:: [[Home]]` line at the top.

- [ ] **Step 3: Add upward link to OPENCLAW_DAILY_ROUTINE.md**

Read the file. Verify or add to frontmatter / top of file:
```markdown
up:: [[OPENCLAW_MASTER_INDEX]]
```

- [ ] **Step 4: Add upward link to SESSION_HANDOFF.md (OpenClaw)**

Read `20 - OpenClaw/Memory/SESSION_HANDOFF.md`. Add to top:
```markdown
up:: [[OPENCLAW_MASTER_INDEX]]
```

- [ ] **Step 5: Add ruflo_skill link to the 25 - AI MOC**

Open the AI MOC file found in Step 1. Find the section that lists skills or tools. Add:
```markdown
- [[ruflo_skill]] — Ruflo operational intelligence: nightly pipeline spec + scoring rubric
```

If no such section exists, add a `## Skills` section at the bottom with this entry.

- [ ] **Step 6: Verify links in Obsidian**

Open Obsidian, navigate to `Graph View`. Confirm that `SESSION_COMPACT_CURRENT`, `OPENCLAW_DAILY_ROUTINE`, `SESSION_HANDOFF`, and `ruflo_skill` all appear as connected nodes (not isolated). If still isolated, the wikilink syntax may need adjustment — check that `[[OPENCLAW_MASTER_INDEX]]` resolves to a real file.

- [ ] **Step 7: Commit vault changes**

```powershell
cd "C:\Users\ronsi95openclaw\Documents\Obsidian Vault"
git add "45 - System/SESSION_COMPACT_CURRENT.md" "20 - OpenClaw/Memory/OPENCLAW_DAILY_ROUTINE.md" "20 - OpenClaw/Memory/SESSION_HANDOFF.md" ai_core/skills/ruflo_skill.md
git add "25 - AI/"
git commit -m "vault: graph-wiring — add upward links to system/memory files + ruflo_skill to AI MOC"
```

---

## Task 9: End-to-End Verification

- [ ] **Step 1: Trigger a full pipeline test**

Manually fire all 4 pipeline jobs in order with a 30-second gap:

```powershell
$hermes = "C:\Users\ronsi95openclaw\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
& $hermes -m hermes_cli.main cron run claude-daily-compact
Start-Sleep -Seconds 30
& $hermes -m hermes_cli.main cron run ruflo-scorecard
Start-Sleep -Seconds 30
& $hermes -m hermes_cli.main cron run hermes-self-review
Start-Sleep -Seconds 30
& $hermes -m hermes_cli.main cron run daily-memory-keeper
```

- [ ] **Step 2: Verify all 4 output files were created**

```powershell
$logs = "C:\Users\ronsi95openclaw\CC-Session-Logs"
$today = Get-Date -Format "yyyy-MM-dd"
Test-Path "$logs\COMPACT_ANALYSIS_$today.md"
Test-Path "$logs\RUFLO_LESSONS_$today.md"
Test-Path "$logs\SELF_REVIEW_CHANGES_$today.md"
Get-Content "C:\Users\ronsi95openclaw\AppData\Local\hermes\memories\MEMORY.md" | Select-Object -Last 10
```

Expected: first 3 return `True`, MEMORY.md last 10 lines show either new facts or `[SILENT]`.

- [ ] **Step 3: Check Telegram**

Confirm 4 Telegram messages arrived:
1. 📦 Daily Compact (from claude-daily-compact)
2. 📐 Ruflo Scorecard (from ruflo-scorecard)
3. 🔧 Self-Review (from hermes-self-review)
4. 🧠 Memory Keeper (from daily-memory-keeper)

- [ ] **Step 4: Confirm haulyeah delivery fix**

```powershell
Get-Content "C:\Users\ronsi95openclaw\AppData\Local\hermes\cron\jobs.json" | ConvertFrom-Json | Select-Object -ExpandProperty jobs | Where-Object { $_.name -in @("haulyeah-tight-leads","haulyeah-lead-review") } | Select-Object name, @{n="chat_id";e={$_.origin.chat_id}}
```

Expected: both show `chat_id = 6082698835` (not `-5282143697`).

- [ ] **Step 5: Confirm graphify graph is current**

```powershell
.\.venv\Scripts\graphify.exe query "receiver" 2>&1 | Select-Object -First 10
```

Expected: lists files related to receiver.py with node counts, no error.

---

## Verification Checklist (Success Criteria from Spec)

- [ ] `haulyeah-tight-leads` + `haulyeah-lead-review` deliver to chat `6082698835`
- [ ] `claude-daily-compact` Step 1 uses `??-??-????*.md` filter
- [ ] `ruflo-scorecard` fires at 00:00, writes RUFLO_LESSONS, sends Telegram
- [ ] `hermes-self-review` fires at 01:00, runs graphify update, writes SELF_REVIEW_CHANGES, sends Telegram
- [ ] `daily-memory-keeper` reads all 3 prior outputs, appends durable facts to MEMORY.md
- [ ] `graphify-out/graph.json` exists, `graphify query` works
- [ ] `ruflo_skill.md` is the operational spec (pipeline + rubric + lesson format + self-review categories)
- [ ] Obsidian graph shows SESSION_COMPACT_CURRENT, OPENCLAW_DAILY_ROUTINE, SESSION_HANDOFF, ruflo_skill as connected nodes
