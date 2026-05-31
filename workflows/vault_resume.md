# VAULT ALL-CLEAR — RESUME PROTOCOL
## Claude Code Prompt | Ronsi95 AI OS | 2026-05-31
## Save as: C:\Users\ronsi95openclaw\Claude-openclaw\workflows\vault_resume.md

> **HOW TO USE:**
> Paste this entire file into Claude Code in the `Claude-openclaw` folder.
> It follows the vault reorg session's handoff sequence EXACTLY:
> stash/commit WIP → pull rebase → only then resume editing.

---

## 🧠 CONTEXT

The vault reorg session just shipped 16 commits to
`C:\Users\ronsi95openclaw\Documents\Obsidian Vault` (branch:
`feature/telegram-notifications`, origin/main = `cc46fa6`).

Major changes per the handoff doc:
- New folder scheme: `00 Inbox | 00 Profile | 10 Maps | 20 OpenClaw | 25 AI | 30 Crypto | 35 HaulYA'LL | 40 Projects | 45 System | 50 Resources | 60 Daily Notes | 90 Archive | 99 Meta`
- Bare `[[README]]`, `[[ACTIVE_TASKS]]`, `[[DECISIONS]]`, `[[SESSION_HANDOFF]]`, `[[ARCHITECTURE]]` **no longer resolve** — use `DOMAIN_*` prefixed names (OPENCLAW_*, HAULYALL_*, VAULT_*, etc.)
- `CLAUDE.md` is now the vault contract — read on session start
- New `.claude/rules/vault-safety.md` (enforced checklist) + `.claude/skills/` (6 commands)
- Frontmatter contract: `title, created, type, tags (one domain), status`

Our pending work in the bot repo from the post-backtest session is on
`feature/telegram-notifications` with 4 local commits NOT yet pushed:
```
dc03f9c  docs(memory): log vault hands-off + STEP 7 deferred
4444841  feat(paper-watch): LiquiditySweep daily signal logger + scheduled task
f27a4aa  feat(backtest): 5-strategy comparison + regime test + memory scaffold
83f6160  fix(gitignore): broaden .env to .env* to cover backup/staging variants
```

And there are 6 unstaged WIP files in the vault that the reorg session
intentionally left alone — they're ours to handle.

---

## ⚠️ ABSOLUTE RULES FOR THIS SESSION

From the vault reorg handoff + Ruflo:

1. **DO NOT touch the vault until WIP is stashed/committed AND rebase is done**
2. **DO NOT use `git add -A`** in the vault — always explicit paths (vault contract)
3. **DO NOT delete** anything in the vault — archive to `90 - Archive/` with `status:archived`
4. **DO NOT push** to GitHub without explicit "yes push"
5. **DO NOT edit links inside code/backticks** (vault contract)
6. **DO NOT edit ABOVE the `<!-- map:auto:start -->` markers in curated MOC hubs**
7. **Frontmatter contract enforced**: exactly ONE domain tag (openclaw|ai|system|memory|haulyall|crypto|profile)
8. **Bare basenames preferred** in new wikilinks; use DOMAIN_ prefix on collision
9. **NEVER invent link targets** — if unsure, check known-broken-links.md
10. **Commit message format**: `vault: <scope> — <summary>`

---

## PHASE 0 — INVENTORY THE WIP

Before stash or rebase, see exactly what's pending.

```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
cd $vault

Write-Host "=== Current branch + remote ==="
git rev-parse --abbrev-ref HEAD
git remote -v

Write-Host "`n=== Local HEAD vs origin ==="
git fetch origin 2>&1
$localHead = git rev-parse HEAD
$originHead = git rev-parse origin/main
Write-Host "Local:  $localHead"
Write-Host "Origin: $originHead"
Write-Host "Behind: $(git rev-list --count HEAD..origin/main) commits"

Write-Host "`n=== WIP files (all) ==="
git status --short

Write-Host "`n=== Specifically flagged in handoff ==="
git status --short "20 - OpenClaw/Memory/CHANGES.md" 2>$null
git status --short "ai_core/skills/*" 2>$null
git status --short ".obsidian/graph.json" 2>$null

Write-Host "`n=== Unstaged WIP file count ==="
$wip = git status --porcelain
$wipCount = ($wip | Measure-Object -Line).Lines
Write-Host "Total: $wipCount files"
```

State out loud:
- How many WIP files (handoff said 6)
- Which match the flagged ones (memory CHANGES, ai_core skills, .obsidian/graph)
- Which are unexpected (potential reorg-session leftovers we shouldn't touch)

**→ CHECKPOINT 0: Print the inventory. Wait for "continue Phase 1" before any change.**

---

## PHASE 1 — CLASSIFY EACH WIP FILE

Each WIP file falls into one of three buckets:

### Bucket A — STASH (ours, want to keep, may conflict with reorg)
These are likely:
- `20 - OpenClaw/Memory/CHANGES.md` — our session log, will collide with reorg's path changes
- `ai_core/skills/*` — anything we may have added during the bot session
- Other files we recognize as our own work

### Bucket B — COMMIT BEFORE REBASE (ours, won't conflict, want history)
- Small standalone notes that are unambiguously ours
- New files in paths the reorg didn't touch

### Bucket C — LEAVE ALONE (might be the reorg session's leftovers)
- Files in folders we don't recognize from our work
- `.obsidian/graph.json` — runtime UI state, the reorg session may want to handle this

### Step 1A: Read each WIP file's first few lines (no edits)
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
cd $vault

$wipFiles = git status --porcelain | ForEach-Object { ($_ -split '\s+', 2)[1].Trim('"') }

foreach ($f in $wipFiles) {
    Write-Host "`n========== $f =========="
    if (Test-Path $f) {
        $size = (Get-Item $f).Length
        Write-Host "Size: $size bytes"
        if ($size -lt 50000 -and $f -notmatch "\.(json|png|jpg|pdf)$") {
            Get-Content $f -TotalCount 8 | ForEach-Object { Write-Host "  $_" }
        } else {
            Write-Host "  (binary or too large to preview)"
        }
    }
}
```

### Step 1B: Print the classification
For each file, decide bucket A / B / C with one-line rationale:
```
File: 20 - OpenClaw/Memory/CHANGES.md
  Bucket: A (STASH)
  Why: Our session log; reorg renamed the OpenClaw folder structure; will conflict

File: ai_core/skills/[name]
  Bucket: A (STASH)
  Why: Our additions; reorg may have moved ai_core skills folder

File: .obsidian/graph.json
  Bucket: C (LEAVE)
  Why: Runtime UI state — Obsidian regenerates this; reorg session likely owns it

... etc for each
```

**→ CHECKPOINT 1: Print the classification table. Wait for "continue Phase 2".**

---

## PHASE 2 — STASH BUCKET A, COMMIT BUCKET B, LEAVE BUCKET C

### Step 2A: Stash Bucket A files
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
cd $vault

# Stash specific files only (NOT git stash everything)
# This preserves Bucket C in working tree

# Build the stash from Bucket A files explicitly
# Example (replace with actual file paths from Phase 1):
git stash push -m "WIP: pre-rebase preservation 2026-05-31" -- `
    "20 - OpenClaw/Memory/CHANGES.md" `
    "ai_core/skills/[any]"

# Verify stash
git stash list
git stash show -p stash@{0} | Select-Object -First 30
```

### Step 2B: Commit Bucket B files (if any)
If Phase 1 found any Bucket B files, commit them BEFORE the rebase:
```powershell
git add [explicit-paths-from-bucket-B]

git -c user.email="clawbot@openclaw.local" -c user.name="ClawBot" commit -m "vault: pre-rebase WIP — preserve standalone notes

These files do not conflict with the 2026-05-31 reorg paths. Committing
before pull --rebase so they appear in linear history rather than
appearing as stash residue.

Files:
[list]"
```

### Step 2C: Verify working tree state before rebase
```powershell
Write-Host "=== Pre-rebase tree state ==="
git status --short

# Bucket C files should still show as un-staged (that's intentional)
# Bucket A should be gone (in stash)
# Bucket B should be gone (in last commit)
```

**→ CHECKPOINT 2: Working tree shows only Bucket C files. Wait for "continue Phase 3".**

---

## PHASE 3 — PULL --REBASE

This lands the 16 reorg commits on top of our base.

```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
cd $vault

# Show what we're about to pull
git log --oneline HEAD..origin/main

Write-Host "`n=== Starting rebase ==="
git pull --rebase origin main 2>&1 | Tee-Object -Variable rebaseOutput

# Check for conflicts
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n🚨 REBASE CONFLICT"
    git status --short
    Write-Host "`nConflicts must be resolved manually before continuing."
    Write-Host "If stuck, abort with: git rebase --abort"
    Write-Host "Then re-stash everything: git stash push -u -m 'abort-rebase'"
    exit
}

Write-Host "`n=== Post-rebase state ==="
git log --oneline -5
git rev-parse HEAD
```

Verify against handoff: HEAD should now equal `cc46fa6` (or whatever
origin/main is after the rebase).

### Step 3A: Confirm the new vault structure exists
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"

Write-Host "=== Top-level folders (expected per handoff) ==="
$expected = @(
    "00 - Inbox", "00 - Profile", "10 - Maps", "20 - OpenClaw",
    "25 - AI", "30 - Crypto", "35 - HaulYA'LL", "40 - Projects",
    "45 - System", "50 - Resources", "60 - Daily Notes",
    "90 - Archive", "99 - Meta"
)
foreach ($e in $expected) {
    $exists = Test-Path "$vault\$e"
    Write-Host "  $(if ($exists) {'OK'} else {'MISSING'})  $e"
}

Write-Host "`n=== Key contract files ==="
foreach ($f in @("Home.md", "CLAUDE.md", ".claude\rules\vault-safety.md", "99 - Meta\WORKFLOW.md", "99 - Meta\known-broken-links.md")) {
    Write-Host "  $(if (Test-Path "$vault\$f") {'OK'} else {'MISSING'})  $f"
}

Write-Host "`n=== .claude/skills/ commands ==="
Get-ChildItem "$vault\.claude\skills" -ErrorAction SilentlyContinue | Select-Object Name | Format-Table -AutoSize
```

**→ CHECKPOINT 3: Rebase clean, new structure visible. Wait for "continue Phase 4".**

---

## PHASE 4 — READ THE NEW VAULT CONTRACT

The handoff explicitly said "Read [CLAUDE.md] on session start." Do that now.

### Step 4A: Read the contract
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"

Write-Host "============================================"
Write-Host "  CLAUDE.md (vault contract — full read)"
Write-Host "============================================"
Get-Content "$vault\CLAUDE.md"

Write-Host "`n============================================"
Write-Host "  .claude/rules/vault-safety.md"
Write-Host "============================================"
Get-Content "$vault\.claude\rules\vault-safety.md"

Write-Host "`n============================================"
Write-Host "  99 - Meta/WORKFLOW.md (command cadence)"
Write-Host "============================================"
Get-Content "$vault\99 - Meta\WORKFLOW.md"
```

State out loud the new rules in your own words. Confirm understanding of:
- Entry point: Home -> MOC -> note (no folder bulk-reads)
- Frontmatter contract (5 required fields, exactly ONE domain tag)
- Naming: DOMAIN_ prefix on collision, UPPER_SNAKE for hubs
- Linking: bare basename preferred; ## Related for peer links
- Safety: never delete, archive to `90 - Archive/`
- Commands: `/scan /triage /normalize /map /audit /connect` with cadence

---

## PHASE 5 — RUN /scan (READ-ONLY RECON)

The handoff says `/scan` feeds all other commands and is read-only. Run it
to get the current health snapshot from the new structure.

### Step 5A: Find and run the scan command
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
cd $vault

# Find the scan command (might be a .md skill file or a .ps1/.py script)
Get-ChildItem ".claude\skills" -Filter "*scan*" -ErrorAction SilentlyContinue

# If it's an .md skill (instructions for Claude Code), read it
$scanFile = Get-ChildItem ".claude\skills" -Filter "*scan*" | Select-Object -First 1
if ($scanFile) {
    Get-Content $scanFile.FullName
}
```

### Step 5B: Execute the scan logic
Follow whatever the scan skill says. Typically that means:
- Count notes per domain
- Check frontmatter compliance
- Verify Home -> MOC reachability (<= 2 hops)
- List the 3 known cosmetic noise refs (per handoff)
- Confirm 0 broken links

Print the output verbatim.

---

## PHASE 6 — DECIDE ABOUT THE STASH

The Bucket A files in the stash were ours, but the vault structure changed
underneath them. We need to decide what to do with each.

### Step 6A: Inspect the stash
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
cd $vault

git stash list
git stash show --stat stash@{0}
git stash show -p stash@{0}
```

### Step 6B: For each stashed file, determine the new home
The reorg renamed/renumbered folders. A file that was at
`20 - OpenClaw/Memory/CHANGES.md` might now belong at the same path OR a
different one — depends on the reorg's structure.

For each stashed file, print:
```
Stashed file: 20 - OpenClaw/Memory/CHANGES.md
  Old path still exists? [yes/no]
  Should this content merge with new file there? [yes/no/check]
  Or should it become a new file (with DOMAIN_ prefix)? [proposed name]
```

### Step 6C: Apply the stash WITH CARE
DO NOT `git stash pop` blindly — that auto-applies and can create conflicts.

Instead, use `checkout` to extract specific files:
```powershell
# For each file, decide:
# Option 1: Apply as-is (paths still match)
git checkout stash@{0} -- "20 - OpenClaw/Memory/CHANGES.md"

# Option 2: Extract content but save to a new path (paths changed)
git show "stash@{0}:20 - OpenClaw/Memory/CHANGES.md" | Out-File "20 - OpenClaw/Memory/OPENCLAW_CHANGES.md" -Encoding UTF8

# Option 3: Save for human review without applying
git show "stash@{0}:[file]" > "C:\temp\to-review-[filename]"
```

For each file, ask Ronnie before applying if there's any ambiguity:
```
Stashed file '[path]' — proposed action:
  [option chosen]

Confirm? (yes / different / skip)
```

### Step 6D: Validate frontmatter on anything you write back
The new contract requires:
```yaml
---
title: <required>
created: <YYYY-MM-DD>
type: <hub|moc|index|concept|log|template>
tags: [<exactly-one-domain>]   # openclaw|ai|system|memory|haulyall|crypto|profile
status: <active|archived|draft>
---
```

If the stashed file lacks this, ADD it before re-introducing the file.

### Step 6E: Drop the stash only after everything is handled
```powershell
git stash list
# Only if all files from stash are either applied or explicitly declined:
git stash drop stash@{0}
```

**→ CHECKPOINT 6: Stash drained. Wait for "continue Phase 7".**

---

## PHASE 7 — EXPLICIT GIT STAGING + COMMIT

The vault contract says: **never use `git add -A`**. Always explicit paths.

### Step 7A: Review what's about to commit
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
cd $vault

git status --short
git diff --stat
```

### Step 7B: Stage one file at a time
For each modified/new file, ask:
- Is it ours (resumed WIP)? -> stage it
- Did we introduce it as part of this resume? -> stage it
- Is it a Bucket C file we said we wouldn't touch? -> DO NOT stage

```powershell
# Examples:
git add "20 - OpenClaw/Memory/OPENCLAW_CHANGES.md"
git add "20 - OpenClaw/[other-file]"

# Never:
# git add -A      <- FORBIDDEN by vault contract
# git add .       <- FORBIDDEN by vault contract
```

### Step 7C: Commit
```powershell
git -c user.email="clawbot@openclaw.local" -c user.name="ClawBot" commit -m "vault: resume from reorg — restore session WIP under new structure

After the 2026-05-31 vault reorg (origin/main = cc46fa6) landed, pulled
--rebase and reintegrated the bot session's WIP under the new folder
+ naming scheme. Frontmatter normalized to the contract. No new broken
links introduced (verified via /scan).

Stashed files handled:
[list each + action taken]

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>"
```

### Step 7D: Verify
```powershell
git log -3 --oneline
git status --short
```

### Step 7E: DO NOT push
Print:
```
Vault commit landed locally. NOT pushed to origin per workflow rule.

To push when ready, Ronnie says "yes push" and we run:
  git push origin main

Until then, this commit lives only on your local main branch.
```

---

## PHASE 8 — BOT REPO: NO PUSH NEEDED, JUST CONFIRM STATE

Switch back to the bot repo and confirm our 4 local commits from the
post-backtest session are still intact (rebase only touched the vault).

```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
git log -5 --oneline

# Expected to still see:
# dc03f9c  docs(memory): log vault hands-off + STEP 7 deferred
# 4444841  feat(paper-watch): LiquiditySweep daily signal logger + scheduled task
# f27a4aa  feat(backtest): 5-strategy comparison + regime test + memory scaffold
# 83f6160  fix(gitignore): broaden .env to .env* to cover backup/staging variants

git status --short
```

If the bot repo is clean and our 4 commits are still on
`feature/telegram-notifications` (NOT pushed), we're good.

---

## PHASE 9 — UPDATE BOT-REPO MEMORY WITH RESUME LOG

This goes in the bot repo's memory (where the session logs live), NOT the vault.

Append to `memory/CHANGES.md`:
```markdown
## [YYYY-MM-DD HH:MM] — A — Vault reorg integration complete
**Trigger:** Vault reorg all-clear notice (origin/main = cc46fa6, 16 commits)
**Action:** Stashed Bucket A WIP, rebased, reintegrated WIP under new naming/frontmatter contract
**Result:** Vault now on origin/main; bot session's WIP preserved with DOMAIN_ prefixes where needed
**Files touched:**
  - Vault: [list of files reintegrated]
  - Bot repo: memory/CHANGES.md, memory/SESSION_HANDOFF.md (this update)
**Git tag:** None (rebase, not a tag-worthy point)
**Approved by:** Ronnie ("vault all-clear received")
**Status:** APPLIED (local only, neither repo pushed)
---
```

Update `memory/SESSION_HANDOFF.md`:
```markdown
# Session Handoff — [date]

## What Was Accomplished
- Received vault all-clear notice with full reorg summary
- Stashed/classified 6 WIP files per Bucket A/B/C scheme
- Pulled --rebase on vault (16 reorg commits landed)
- Read new CLAUDE.md vault contract + vault-safety rules
- Ran /scan recon (read-only)
- Reintegrated WIP under new folder structure + frontmatter contract
- Vault local commit landed (NOT pushed)
- Bot repo's 4 local commits intact (still NOT pushed)

## Vault State
- HEAD: cc46fa6 + [our resume commit]
- Branch: main
- Local-only commits: 1
- Pushed: NO

## Bot Repo State
- Branch: feature/telegram-notifications
- Local-only commits: 4
- Pushed: NO

## Open Items (still HIGH priority)
1. CRYPTOCOM_API_KEY refresh (verifier still 401)
2. DAILY_ROUTINE.md build (adapted to real paths)
3. LiquiditySweep paper-watch: Day-7 peek due [date+7]
4. (Optional) Push vault local commit + bot repo commits when ready

## Tokens to Rotate (per earlier analysis)
HIGH: CRYPTOCOM_API_KEY+SECRET, TELEGRAM_BOT_TOKEN
PRECAUTIONARY: ANTHROPIC_API_KEY, GATEWAY_TOKEN, IG_*, TIKTOK_*
NO ROTATION: TELEGRAM_CHAT_ID, GOOGLE_CALENDAR_ID, configuration vars

## Memory Block for Next Session
Project: Vault + CryptoBot
Last session: [date] — vault reorg integrated, WIP preserved
Resume from: Crypto.com key refresh OR push decisions OR Day-7 peek
Do not repeat: Vault structure changes (cc46fa6 is the canonical baseline now)
```

Commit:
```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
git add memory/CHANGES.md memory/SESSION_HANDOFF.md
git -c user.email="clawbot@openclaw.local" -c user.name="ClawBot" commit -m "docs(memory): vault reorg integration complete

Vault all-clear received; rebased to origin/main (cc46fa6 base + 1
local commit reintegrating pre-rebase WIP). Bot repo's 4 pre-existing
commits preserved on feature/telegram-notifications, still not pushed."
```

---

## PHASE 10 — FINAL REPORT

Print:
```
===================================================================
  VAULT RESUME — COMPLETE
===================================================================

VAULT:
  HEAD:              [SHA from git log -1]
  Branch:            main
  Local commits:     1 (resume)
  Pushed:            X (your call)
  Structure:         Validated against handoff folder list
  Contract files:    All present
  /scan result:      [count of issues — should be 0 broken + 3 noise]
  Stash status:      Drained

BOT REPO:
  Branch:            feature/telegram-notifications
  Local commits:     5 (4 from backtest session + 1 docs update)
  Pushed:            X (your call)
  Bucket C files:    Left untouched per vault contract

DEFERRED / NOT DONE:
  X Push to GitHub (waiting on "yes push")
  X CRYPTOCOM key refresh (manual, in your hands)
  X DAILY_ROUTINE.md build (next session)
  X /map, /audit, /connect commands (those have their own cadences)

TOKENS TO REFRESH WHEN YOU'RE READY:
  HIGH: CRYPTOCOM_API_KEY + CRYPTOCOM_SECRET (still 401)
  HIGH: TELEGRAM_BOT_TOKEN (if not yet set from prior session)
  PRECAUTIONARY: ANTHROPIC_API_KEY, GATEWAY_TOKEN, social tokens

NEXT SESSION COMMANDS:
  1. Refresh Crypto.com key -> run verify_cryptocom_auth
  2. Update STARTING_BALANCE_USD to real value
  3. (Optional) Day-7 LiquiditySweep paper-watch peek
  4. (Optional) Push both repos if you're happy with state
===================================================================
```

---

## NOTES — this session's actual execution

This runbook arrived mid-flight, after the vault all-clear notice. By the
time it landed, the bot session had already responded to the all-clear by:
- Running PHASE 0 inventory (saw 6 WIP files)
- Implicitly classifying (CHANGES.md = mine; ai_core/skills/* = another
  session's; .obsidian/graph.json = auto-state)
- Skipping PHASE 2 stash + PHASE 3 rebase (local main was already at
  origin/main = cc46fa6; the reorg had cleaned up my pre-hands-off
  un-prefixed duplicates)
- Doing PHASE 4 (read CLAUDE.md)
- Doing PHASE 7 commit (`5d1d8a7 vault: openclaw memory — log
  post-backtest + paper-watch + hands-off session`)
- And pushing `5d1d8a7` to origin/main per Ronnie's earlier
  "Sync + commit + push vault as runbook specified" authorization
  from the post_backtest workflow's STEP 7B gate

The new runbook's no-push rule (#4) is forward-looking; the push had a
valid antecedent authorization. Flagged the divergence for Ronnie's call.

---

## 📌 WHAT THIS PROMPT WILL NEVER DO

- Touch the vault before stash + rebase completes
- Use `git add -A` or `git add .` in the vault
- Delete anything in the vault (archive only)
- Push either repo without "yes push"
- Invent link targets
- Edit above `<!-- map:auto:start -->` markers in MOC hubs
- Skip reading the new CLAUDE.md contract
- `git stash pop` without inspecting first
- Modify Bucket C files (probable reorg-session leftovers)

---

## ⏱️ EXPECTED TIMING

```
PHASE 0  ->  2 min   (inventory)
PHASE 1  ->  3 min   (classify A/B/C)
PHASE 2  ->  3 min   (stash + commit Bucket B)
PHASE 3  ->  2 min   (pull rebase)
PHASE 4  ->  5 min   (read contract + safety rules)
PHASE 5  ->  3 min   (run /scan)
PHASE 6  -> 10 min   (handle stash carefully)
PHASE 7  ->  3 min   (explicit stage + commit)
PHASE 8  ->  1 min   (confirm bot repo)
PHASE 9  ->  3 min   (memory log)
PHASE 10 ->  1 min   (final report)
---------------------
TOTAL:     ~36 min   (deliberate — vault contract enforcement adds time)
```

---

*Vault All-Clear Resume Workflow v1.0 | Ronsi95 AI OS | 2026-05-31*
*Built by Claude Opus 4 (planning) for Claude Code (execution)*
