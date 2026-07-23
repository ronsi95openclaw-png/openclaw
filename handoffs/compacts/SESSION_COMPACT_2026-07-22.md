# Session Compact — 2026-07-22

## What Was Built / Changed
- openclaw repo: cleaned from 48 branches/17 open PRs to 1 branch/1 open PR
  (#27, still needs review). Default branch fixed to main. Security fix
  (Telegram allowlist fail-open) cherry-picked into main.
- cryptobot repo: found and merged PR #16 (5-week-old startup crash fix +
  secrets removed from tracking). Cleaned 40 branches -> 1, 16 PRs -> 0.
  Repo confirmed PUBLIC (was believed private) - credentials rotated.
  Repo archived.
- Hermes multi-model pipeline built from scratch: Strategist (Claude Fable
  5) -> Executor (DeepSeek/GLM/Kimi by complexity) -> Reviewer (Gemini 3
  Pro, fixed tier) -> bounded 2-cycle revise loop -> DECISION NEEDED
  escalation. Routes through OpenRouter only (OmniRoute dropped after 3
  provider-layer failures; direct-Anthropic dropped after a billing block).
  Validated via dry run (Reviewer correctly caught a deliberately injected
  brand-compliance violation) and one real read-only VibeTrader task
  (eval_gate/branch-forensics report, approved).
- hermes-self-review: built and proved (4 injected failure cases + success
  round-trip) a code-level write-validation safeguard for cron/jobs.json,
  addressing the gap from the original cron-wipe incident. Left disabled
  by choice, not by failure.
- HaulOps fully stopped: killed 3 independent relaunch mechanisms found
  one at a time over multiple checks - the live process itself, the
  HaulYeahBot Scheduled Task, and a Startup-folder .lnk shortcut. All
  disabled/renamed, not deleted.
- All 21 Hermes cron jobs disabled (0 enabled) pending finalized project
  directions. Hermes-Watchdog (self-healing keep-alive, lives in Windows
  Scheduled Tasks not jobs.json) deliberately excluded from this pause.
- Two retired-pillar (ClawBot) Scheduled Tasks deleted outright
  (ClawBot-Watchdog, ClawBot-LiquiditySweep-Watch).

## Decisions Made
- VibeTrader: chosen as the primary pillar to take start-to-finish, paper
  mode only - reason: lower blast radius than HaulOps for the pipeline's
  first live activation, and it's the project with the most unresolved
  technical debt worth a real push.
- HaulOps: paused pending a business-continuity decision (continue vs.
  archive) - reason: repeated automation failures (bot silently running
  after being told to stop, 3 separate times across different mechanisms)
  signal the business itself needs a decision before more engineering time
  goes in.
- Hermes pipeline architecture: OpenRouter-only, no OmniRoute, no direct
  Anthropic API - reason: OmniRoute failed 3 separate times at the
  provider-connection layer (billing, OAuth, ALL_ACCOUNTS_INACTIVE); its
  only real value-adds (RTK/Caveman compression, free-tier routing) were
  explicitly decided against anyway.
- Reviewer tier locked to Gemini 3 Pro, never substituted down - reason:
  the Reviewer's entire job is catching mistakes; a cheaper tier risks
  rubber-stamping instead.

## What Was Learned / Patterns
- Recurring root cause across BOTH openclaw and cryptobot: work getting
  produced (branches, PRs, daily cron fixes) but never merged, so nothing
  ever closes the loop. Not a coding-skill problem - a merge-discipline gap.
- "Confirmed stopped" needs re-verification with a real method
  (Get-CimInstance / full process tree), not a surface check
  (tasklist without command-line visibility silently missed a running
  process once already this session).
- Repo visibility (public/private) should be checked explicitly and early,
  not assumed - both openclaw and cryptobot turned out to be public when
  believed otherwise.
- A commit message claiming "per Ronnie's explicit request" is not
  independent proof of authorization, especially from a cron with a known
  history of fabricating authorization claims (hermes-auto-improve /
  hermes/auto-2026-07-13 / commit c5b56e0). Still unresolved.
- Multiple independent autostart/relaunch mechanisms can exist for the same
  process (Scheduled Task + Startup folder shortcut + live process) - a fix
  at one layer doesn't guarantee the others are gone. Worth a full-inventory
  check, not a single kill, when "actually stop this" matters.

## State Changes
| Pillar     | Before                                   | After                                            |
|------------|-------------------------------------------|---------------------------------------------------|
| openclaw   | 48 branches, 17 open PRs, wrong default   | 1 branch, 1 open PR (#27, needs review), main fixed |
| cryptobot  | 40 branches, 16 open PRs, bot down 5wks   | Archived; startup bug fixed; secrets rotated       |
| Hermes     | No standing multi-model pipeline          | Live pipeline (OpenRouter-only), validated          |
| HaulOps    | Believed paused, actually running 3x over | Genuinely stopped, 3 relaunch paths closed          |
| VibeTrader | No recent activity, untouched since 7/13  | Confirmed paper-mode gate intact, chosen as focus   |
| Cron jobs  | 22 jobs, mixed enabled/disabled           | 21 jobs (1 deleted), 0 enabled - full pause          |

## Files Touched
- pipeline/*.py, pipeline/combos/*.json, pipeline/.env(.example),
  pipeline/README.md - new Hermes pipeline module
- docs/superpowers/specs/2026-07-20-hermes-multi-model-pipeline-design.md
- scripts/safe_jobs_json_patch.py - new
- scripts/vibe_eod_summary.py (or wherever send_telegram() lives) - fixed
  Telegram delivery bug (wrong env var names + Markdown parse failure on
  underscore in mandate name)
- cron/jobs.json - all jobs disabled, haulyeah-weekly-review removed
- (openclaw/cryptobot repos - see GitHub directly for full commit history)

## Did NOT finish / Carry forward
- c5b56e0 merge (hermes/auto-2026-07-13) - keep/revert decision still open,
  hinges on Ronnie's own recollection, not further investigation
- openclaw PR #27 - real, good work (subagent scaffolding + trading
  bugfixes), still needs an actual review/merge decision
- HaulOps business decision (continue vs. archive) - not made yet
- Two ClawBot Startup-folder shortcuts (Dashboard/Receiver Autostart.lnk)
  flagged, not investigated or acted on
- Full project-goals/direction finalization for all 3 pillars - the reason
  cron jobs are paused - not yet done
