# RUFLO — Universal Session Skill
## Ronsi95 AI OS | OpenClaw | v1.0

> Paste or load this file at the **start of every Claude Code session**
> before touching any code, vault, or config.
> These rules override defaults and apply universally across all projects.

---

## STEP 0 — CONTRACT LOADING (non-negotiable first action)

Run the following and state the results out loud:

```powershell
$root     = "C:\Users\ronsi95openclaw\Claude-openclaw"
$vault    = "$env:USERPROFILE\Documents\Obsidian Vault\CLAUDE.md"

# 1. Vault contract (source of truth for vault rules)
if (Test-Path $vault) { Get-Content $vault }

# 2. Bot repo continuity files
Get-Content "$root\memory\SESSION_HANDOFF.md"
Get-Content "$root\memory\ACTIVE_TASKS.md"
Get-Content "$root\memory\CHANGES.md" -Tail 20
```

State out loud:
- Last commit SHA and anything pending push
- Open ACTIVE_TASKS crossing overnight
- Whether vault is in sync

---

## UNIVERSAL RULES

### Session behaviour
- Never auto-apply more than what was explicitly requested in the prompt
- No broad refactors without asking Ronnie first
- Do not create planning files, decision docs, or READMEs unless asked

### Git
- Always stage specific paths — never `git add -A` or `git add .`
- Always identify with: `git -c user.email="clawbot@openclaw.local" -c user.name="ClawBot"`
- Never push without explicit "yes push" from Ronnie
- Never skip hooks (`--no-verify`)
- Before every Category A auto-fix, create a rollback tag:
  `git tag autofix-$(date +%Y%m%d-%H%M)-<id>`

### Vault
- Never touch `Documents/Obsidian Vault/` except via `infra/sync_to_vault.bat`
- Vault Bucket C files (owned by other Claude sessions, do NOT edit):
  - `.obsidian/graph.json`
  - `ai_core/skills/*`
- All wiki-links must use domain prefix: `[[OPENCLAW_*]]`, `[[HAULYALL_*]]`, etc.
- `[[ACTIVE_TASKS]]`, `[[DECISIONS]]` (bare names) no longer resolve — use prefixed versions

### Secrets
- Never log or print `.env` values — check presence/length only
- Never hardcode tokens, chat IDs, or credentials in source files
- Never commit `.env*` files

### Trading
- Bot is always in DEMO mode unless Ronnie explicitly says "go live"
- LIVE flip requires: complete pre-live checklist + 24h cooling-off + ACTIVE_TASKS #1 done
- Never modify `trading/risk.py` without explicit approval
- Circuit breaker triggered → stop, read alert, escalate — do NOT restart

---

## ESCALATION HIERARCHY

### Category A — Safe auto-fixes (always tag for rollback first)

| Issue | Fix | Daily cap |
|-------|-----|-----------|
| Bot process down | `python start.py` (re-launch) | 3 |
| Ollama unreachable | `ollama serve` (background) | 3 |
| Scheduled task result ≠ 0 | `schtasks /run /tn <name>` | 2 |
| Stale open position > 48h | close at market, log reason | unlimited |
| Missing `data/logs/` dir | `mkdir data/logs` | 1 |
| Watchdog stopped | `schtasks /run /tn ClawBot-Watchdog` | 3 |
| Hermes / paper-watch task stopped | `schtasks /run /tn <name>` | 3 |

Cap exceeded → auto-escalate to Category C.

### Category B — Propose only, never auto-apply

- Strategy parameter changes
- New coin or market additions
- Timeframe or signal threshold adjustments

→ Write proposal to `memory/STRATEGY_DECISIONS.md`. Wait for "yes apply".

### Category C — Always escalate (Telegram + memory log, never self-fix)

- Any change to `trading/risk.py`, `MAX_TRADE_RISK_PCT`, `MAX_DRAWDOWN_PCT`
- `TRADING_MODE` flip to LIVE
- Adding capital
- Disabling any safety filter or circuit breaker
- API credential changes
- Vault contract violations
- Category A fix hitting its daily cap
- `private/create-order` v2 endpoint (ACTIVE_TASKS #1 — unverified)

---

## MEMORY FILE PATHS (real paths, confirmed 2026-05-31)

```
memory/SESSION_HANDOFF.md    — current state snapshot
memory/ACTIVE_TASKS.md       — open tasks + priorities
memory/CHANGES.md            — session change log (append-only)
memory/DECISIONS.md          — architectural decisions (append-only)
memory/DAILY_ROUTINE.md      — operational routine (run each morning)
```

Windows vault mirror:
```
Documents\Obsidian Vault\20 - OpenClaw\Memory\OPENCLAW_*.md
```

---

## CHANGES.md ENTRY FORMAT

```markdown
## [YYYY-MM-DD HH:MM] - [A/B/C] - <Title>
**Trigger:** what was detected (with measurement)
**Action:** what was done (or proposed)
**Result:** what changed
**Files touched:** <list>
**Git tag:** autofix-...
**Approved by:** Auto / Ronnie (date)
**Status:** APPLIED / PENDING / REJECTED / ROLLED-BACK
---
```

---

## HERMES — Knowledge Graph

Hermes runs graphify daily at 09:30 UTC and syncs the knowledge graph to Obsidian.

```
/hermes          — show status
/hermes on       — enable daily run (09:30 UTC default)
/hermes on HH:MM — custom time
/hermes off      — disable
/hermes now      — run immediately
```

Output locations (all git-ignored):
- `graphify-out/graph.html` — interactive graph browser
- `graphify-out/GRAPH_REPORT.md` — insights + god nodes
- `graphify-out/obsidian/` — Obsidian-compatible markdown
- `memory/HERMES_GRAPH_REPORT.md` — synced to vault via sync_to_vault.bat

---

## SESSION-END CHECKLIST

- [ ] `memory/SESSION_HANDOFF.md` updated with current state
- [ ] `memory/ACTIVE_TASKS.md` updated if priorities shifted
- [ ] `memory/CHANGES.md` updated (one entry per change, Step 7 format)
- [ ] `infra/sync_to_vault.bat` run (or noted as deferred)
- [ ] Local commit with correct user config (do NOT push unless Ronnie says so)
- [ ] No `.env*` files staged

---

## NEVER DO

- `git add -A` or `git add .`
- Push without "yes push" from Ronnie
- Modify `trading/risk.py` without approval
- Flip `TRADING_MODE` without checklist + 24h cooling-off + ACTIVE_TASKS #1
- Touch the vault outside `infra/sync_to_vault.bat`
- Edit Bucket C files (`.obsidian/graph.json`, `ai_core/skills/*`)
- Log or print `.env` values
- Skip Step 0 (contract load)
- Apply Category B or C fixes without explicit approval
- Create `_Archive/` entries without archiving the file first

---

*Ruflo v1.0 — initialized 2026-06-27*
*Install path: `skills/ruflo/SKILL.md` (this file)*
*Alt path (Claude AppData): `%APPDATA%\Claude\skills\ruflo\SKILL.md`*
