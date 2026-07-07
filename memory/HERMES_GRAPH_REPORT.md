---
title: Hermes Graph Report 2026-06-27
created: 2026-06-27
type: log
tags:
  - openclaw
  - openclaw/knowledge-graph
status: active
---

# Hermes Knowledge Graph — 2026-06-27 03:46 UTC

# Graph Report - openclaw  (2026-06-27)

## Corpus Check
- 98 files · ~54,818 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1253 nodes · 2221 edges · 61 communities (51 shown, 10 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 86 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `721c1ea1`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]

## God Nodes (most connected - your core abstractions)
1. `Signal` - 40 edges
2. `TrashHaulingBot` - 32 edges
3. `is_authorized()` - 29 edges
4. `TrendContinuationStrategy` - 26 edges
5. `SheetsClient` - 25 edges
6. `BacktestResult` - 20 edges
7. `AuditLogger` - 20 edges
8. `sanitize_text()` - 19 edges
9. `CRYPTOBOT — DAILY OPERATING ROUTINE` - 19 edges
10. `VAULT ALL-CLEAR — RESUME PROTOCOL` - 19 edges

## Surprising Connections (you probably didn't know these)
- `TestEvaluate` --uses--> `EmaMomentumConfig`  [INFERRED]
  tests/test_ema_momentum.py → trading/strategies/ema_momentum.py
- `TestEvaluate` --uses--> `EmaMomentumStrategy`  [INFERRED]
  tests/test_ema_momentum.py → trading/strategies/ema_momentum.py
- `TestEvaluate` --uses--> `Signal`  [INFERRED]
  tests/test_ema_momentum.py → trading/strategy.py
- `TestWarmupAndGuards` --uses--> `Signal`  [INFERRED]
  tests/test_liquidity_sweep.py → trading/strategy.py
- `TestBullishSweep` --uses--> `Signal`  [INFERRED]
  tests/test_liquidity_sweep.py → trading/strategy.py

## Import Cycles
- None detected.

## Communities (61 total, 10 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (60): cmd_ask(), cmd_autotrade(), cmd_brain(), cmd_cancel(), cmd_clear(), cmd_dca(), cmd_demo(), cmd_help() (+52 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (44): Daily auto-trade job: scan → execute HIGH signals → notify., Public entry point to trigger the auto-trade scan immediately., _run_autotrade(), run_autotrade_now(), main(), One-shot verifier for the Crypto.com API credentials.  Run after refreshing CRYP, TestCircuitBreakerMessage, TestDrawdownPct (+36 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (41): get_cache_info(), get_clawbot_status(), get_ollama_status(), get_prices(), get_recent_trades(), get_tasks(), get_usage_today(), index() (+33 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (50): Captions, generate_captions(), LLM-powered caption and hashtag generator for OpenClaw reels.  Uses brain.py (Ol, Generate Instagram and TikTok captions using the local LLM.      Args:         t, _build_subtitle_filter(), _get_random_music(), process_video(), FFmpeg-based video editor for OpenClaw content pipeline.  Handles: - Auto-cut to (+42 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (37): atr_from_closes(), _atr_history(), bb_width_history(), bollinger_bands(), BreakoutExpansionConfig, BreakoutExpansionStrategy, _mean(), ClawBot — Breakout Expansion Strategy =====================================  The (+29 more)

### Community 5 - "Community 5"
Cohesion: 0.06
Nodes (54): _cli(), _extract_digest(), graphify_available(), Hermes — daily knowledge-graph agent for OpenClaw.  Runs graphify on the codebas, Pull the most informative lines from GRAPH_REPORT.md for a Telegram message., Persist the graph report to memory/ so sync_to_vault.bat can pick it up., Orchestrate the full Hermes knowledge-graph cycle., Build env for graphify subprocess — inherits current env + .env values. (+46 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (26): load_closes(), main(), print_decision_matrix(), print_per_symbol_block(), print_regime_block(), Strategy comparison harness.  Runs all 5 strategies (4 candidates + RSI+MACD bas, Run one strategy on one symbol; return a flat dict for the report., run_one() (+18 more)

### Community 7 - "Community 7"
Cohesion: 0.08
Nodes (26): evaluate_one(), fetch_recent_candles(), main(), paper_watch_liquiditysweep.py — Log LiquiditySweep signals against live Crypto.c, Public endpoint — no auth required. Returns chronological-order candles or None., Fetch candles, run the strategy, return a serializable observation., LiquiditySweepConfig, LiquiditySweepStrategy (+18 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (12): Sub-Agent 3 — Google Calendar Sync Agent  Monitors the Leads sheet for rows with, _maybe_append_quote(), Sub-Agent 2 — Outreach Agent  Pulls uncontacted leads from Google Sheets, genera, Optionally tack a short price-range estimate onto an outreach message.      Gate, Sub-Agent 1 — Facebook Marketplace Scraper  Monitors FB Marketplace for trash ha, _score_urgency(), _is_authorized(), TestMaybeAppendQuote (+4 more)

### Community 9 - "Community 9"
Cohesion: 0.05
Nodes (41): ⚠️ ABSOLUTE RULES FOR THIS SESSION, Bucket A — STASH (ours, want to keep, may conflict with reorg), Bucket B — COMMIT BEFORE REBASE (ours, won't conflict, want history), Bucket C — LEAVE ALONE (might be the reorg session's leftovers), Claude Code Prompt | Ronsi95 AI OS | 2026-05-31, 🧠 CONTEXT, ⏱️ EXPECTED TIMING, NOTES — this session's actual execution (+33 more)

### Community 10 - "Community 10"
Cohesion: 0.12
Nodes (32): ask_claude(), ask_hybrid(), ask_llm(), ask_openrouter(), _cache_key(), classify_complexity(), _compress(), _compress_history() (+24 more)

### Community 11 - "Community 11"
Cohesion: 0.10
Nodes (8): TestExtractPhone, TestPromptInjection, TestSanitizeLeadField, TestValidateFbUrl, extract_phone(), is_prompt_injection(), sanitize_lead_field(), validate_fb_url()

### Community 12 - "Community 12"
Cohesion: 0.11
Nodes (14): BaseException, Exception, _FakeHTTPError, _FakeResponse, TestIsRateLimitError, TestIsRetryable, TestWithBackoff, is_rate_limit_error() (+6 more)

### Community 13 - "Community 13"
Cohesion: 0.06
Nodes (31): ⚠️ ABSOLUTE RULES, Claude Code Prompt | Ronsi95 AI OS | May 2026, 🧠 CONTEXT, CRYPTOBOT — POST-BACKTEST COMMIT & NEXT STEPS, ⏱️ EXPECTED TIMING, Save as: C:\Users\ronsi95openclaw\Claude-openclaw\workflows\post_backtest.md, STEP 0 — LOAD RUFLO + READ CONTINUITY, STEP 1 — PRE-COMMIT SANITY CHECK (+23 more)

### Community 14 - "Community 14"
Cohesion: 0.16
Nodes (14): TestFormatReport, TestRecordAndLoad, TestSummarize, _trade(), format_report(), load_trades(), Trade history — a small structured JSON store of executed trades, plus summary/r, Load the trade list; returns [] if missing or corrupt. (+6 more)

### Community 15 - "Community 15"
Cohesion: 0.12
Nodes (10): estimate(), estimate_tier(), format_quote(), Quote estimator — maps a free-text job description to a flat-rate price tier.  P, Classify a job description into a pricing tier; defaults to 'minimum'., Return tier, price, and a customer-facing price range for a description., Build a short, friendly customer message with the estimated price range., TestEstimate (+2 more)

### Community 16 - "Community 16"
Cohesion: 0.07
Nodes (26): Adapted from v2.1 template, real paths confirmed 2026-05-31, Auto-fix loop hits daily cap, CATEGORY A — Safe auto-fixes (apply with git tag for rollback), CATEGORY B — Strategy/parameter proposals (NEVER auto-apply), CATEGORY C — Always escalate (Telegram + memory log), Circuit breaker triggered, Crypto.com auth returns non-200 (401, 400, 500, etc.), CRYPTOBOT — DAILY OPERATING ROUTINE (+18 more)

### Community 17 - "Community 17"
Cohesion: 0.12
Nodes (15): EmaMomentumConfig, EmaMomentumStrategy, ClawBot - EMA Momentum Strategy =============================== Thesis: A fast E, Rate of change percent: (closes[-1] / closes[-period-1] - 1) * 100.      Pure. R, EMA crossover + ROC confirmation strategy.      Uses the fast/slow EMAs to detec, Min candles required before evaluate is meaningful., Evaluate the most recent candle and return a Signal., roc() (+7 more)

### Community 18 - "Community 18"
Cohesion: 0.15
Nodes (15): Parameters for the Trend Continuation strategy., Pullback-into-trend strategy keyed off EMA slope + RSI., Score the latest candle. Returns a Signal compatible with the executor., TrendContinuationConfig, TrendContinuationStrategy, _downtrend(), Unit tests for trading.strategies.trend_continuation.  The strategy is pure, so, Smooth uptrend: closes rise by `step` each candle. (+7 more)

### Community 19 - "Community 19"
Cohesion: 0.08
Nodes (23): [2026-05-30] — Backtest uses 1d candles instead of 4h for the regime test, [2026-05-30] — Defer strategy-wiring; paper-watch LiquiditySweep in DEMO for 2 weeks, [2026-05-31] — Migrate Crypto.com private API surface from v1 to v2, DECISIONS, Cross-references, Decision, Files, Headline Result — NO STRATEGY HIT THE 3/4 BAR (+15 more)

### Community 20 - "Community 20"
Cohesion: 0.09
Nodes (19): _FakeResponse, _patch_signing(), Tests for trading.executor._place_order transient-network retry.  The retry poli, Two consecutive Timeouts -> re-raise., Server responded (e.g. 500) -> raise_for_status fires -> do NOT retry.      Orde, Happy path: post returns 200 immediately, only one attempt., API-level rejection (code != 0) -> ValueError, no retry., Stand-in for requests.Response. (+11 more)

### Community 21 - "Community 21"
Cohesion: 0.12
Nodes (8): is_blocked(), Command blocklist for /run and /py shell execution.  Pure-stdlib, dependency-fre, Return the first matching blocklist pattern, or None if the command is clean., Tests for security.blocklist and security.audit., Redirect the audit log into a temp dir for the duration of the test., temp_audit_log(), TestIsBlocked, TestLogCommand

### Community 22 - "Community 22"
Cohesion: 0.16
Nodes (3): TestDedupStore, TestFingerprint, DedupStore

### Community 23 - "Community 23"
Cohesion: 0.10
Nodes (20): ⚠️ ABSOLUTE RULES, ⚠️ ADAPTATION NOTES (filled in from prior session), Claude Code Workflow Command | Ronsi95 AI OS | May 2026, CRYPTOBOT — NEXT SESSION WORKFLOW, 🧠 IDENTITY & MISSION, PHASE 1 — FIX CRYPTO.COM 401, PHASE 2 — UPDATE STARTING_BALANCE_USD, PHASE 3 — HISTORICAL DATA (already fetched) (+12 more)

### Community 24 - "Community 24"
Cohesion: 0.11
Nodes (18): Category A — Safe auto-fixes (always tag for rollback first), Category B — Propose only, never auto-apply, Category C — Always escalate (Telegram + memory log, never self-fix), CHANGES.md ENTRY FORMAT, ESCALATION HIERARCHY, Git, HERMES — Knowledge Graph, MEMORY FILE PATHS (real paths, confirmed 2026-05-31) (+10 more)

### Community 25 - "Community 25"
Cohesion: 0.18
Nodes (11): ClawBot — Liquidity Sweep Strategy ================================== Smart-mone, Trend Continuation Strategy =========================== Thesis: in an establishe, TestMediumConfidence, calculate_ema(), calculate_macd(), calculate_rsi(), detect_macd_crossover(), ClawBot — RSI + MACD Strategy Module ===================================== Strat (+3 more)

### Community 26 - "Community 26"
Cohesion: 0.15
Nodes (5): CalendarSyncAgent, Age leads with no activity beyond the configured threshold to no_response., Create calendar events for any scheduled leads that don't have one yet., Mark a lead as scheduled, create its calendar event, and return the event ID., CalendarClient

### Community 27 - "Community 27"
Cohesion: 0.21
Nodes (3): Mark leads with no activity for stale_days as no_response. Returns count changed, SheetsClient, Worksheet

### Community 28 - "Community 28"
Cohesion: 0.12
Nodes (15): [2026-05-30 18:30] — A — Bootstrap memory/ directory, [2026-05-30 18:30] — A — Fixed Unicode crash in verify_cryptocom_auth, [2026-05-30 21:25] — A — Strategy comparison run + decision documented, [2026-05-30 21:30] — A — Extended sync_to_vault.bat to cover Claude-openclaw memory/, [2026-05-31 00:10] — C — Pre-commit sanity check caught .gitignore gap, [2026-05-31 00:15] — A — Two atomic local commits made (NOT pushed), [2026-05-31 06:35] — A — LiquiditySweep paper-watch infra built + scheduled, [2026-05-31 06:55] — C — HANDS-OFF on vault; STEP 7 vault commit + push aborted (+7 more)

### Community 29 - "Community 29"
Cohesion: 0.19
Nodes (7): Flat baseline, a brief uptick pushes fast EMA above slow EMA,         then a sha, Same bullish cross construction as the HIGH case, but with the         threshold, Same bearish cross construction as the HIGH case, but with the         threshold, A smooth, established uptrend keeps fast above slow on both of the         last, Flat baseline, a brief dip pulls fast EMA below slow EMA, then a         big rip, _strategy(), TestEvaluate

### Community 30 - "Community 30"
Cohesion: 0.13
Nodes (14): 0. Add paper-watch calendar reminders to phone (DUE: before 2026-06-07), 1. Before any LIVE-mode flip: verify `private/create-order` on v2, 2. Build DAILY_ROUTINE.md adapted to real paths, 2. ~~Refresh Crypto.com API key~~ — DONE 2026-05-31, 3. Day-7 LiquiditySweep paper-watch review — 2026-06-07, 5. Day-14 LiquiditySweep paper-watch final decision — 2026-06-14, 6. Patch `infra/sync_to_vault.bat` to OPENCLAW_ prefix convention, 7. ~~Ruflo skill installation~~ — DONE 2026-06-27 (+6 more)

### Community 31 - "Community 31"
Cohesion: 0.16
Nodes (9): Open a visible browser window so the user can log into Facebook once., Scrape FB Marketplace across all configured keywords. Returns count of new leads, ScraperAgent, AsyncIOScheduler, BrowserContext, Create and start the global scheduler. Call once at bot startup., start_scheduler(), Page (+1 more)

### Community 32 - "Community 32"
Cohesion: 0.14
Nodes (13): Bot Repo Commits This Session (8 PUSHED + 1 pending this entry's commit), Calendar Reminders Set (manually, on phone), Did NOT Do (intentional), How to Disable Things If Needed, Key Decisions Made (see DECISIONS.md for full reasoning), Current State, Memory Block for Next Session, Open Items (HIGH priority — full list in ACTIVE_TASKS.md) (+5 more)

### Community 33 - "Community 33"
Cohesion: 0.29
Nodes (4): OutreachAgent, Generate a message for this lead and add it to the pending queue., Mark an outreach as sent after explicit team confirmation. Never auto-sends., Remove from queue and mark the lead declined.

### Community 36 - "Community 36"
Cohesion: 0.29
Nodes (3): Post-job review request — builds a short, friendly message asking a customer to, review_request_message(), TestReviewRequestMessage

### Community 37 - "Community 37"
Cohesion: 0.29
Nodes (9): best_effort_fetch_tf(), days_span(), fetch_candles(), main(), normalize_candles(), Pre-fetch 4h candles for the 4-symbol basket from Crypto.com Exchange v1.  Endpo, Single GET against the public candlestick endpoint., Coerce candle records to a stable dict shape and sort oldest-first. (+1 more)

### Community 38 - "Community 38"
Cohesion: 0.20
Nodes (8): Commands  ← highest-value section, fill this in precisely, Conventions, How to work here, Layout, Memory & handoff, Never, Project, Response style

### Community 39 - "Community 39"
Cohesion: 0.25
Nodes (7): 2026-05-29 — Adapt the RONSI95 template, don't execute it literally, 2026-05-29 — Circuit breaker measures drawdown from a fixed start, 2026-05-29 — No Supabase, 2026-05-29 — Quote/review as pure standalone helpers, 2026-05-29 — `/report` is activity-only, 2026-05-29 — Watchdog is alert-only; detection fixed, Decisions

### Community 40 - "Community 40"
Cohesion: 0.25
Nodes (7): Environment Variables, Notes, OpenClaw (ClawBot) v0.1, Project Structure, Requirements, Setup, Usage

### Community 41 - "Community 41"
Cohesion: 0.33
Nodes (6): main(), Cloud entry point — runs Telegram bot + web dashboard in one process.  Bot runs, Start the Telegram bot in this thread (blocking)., Start Flask dashboard in this thread (blocking)., _run_bot(), _run_dashboard()

### Community 42 - "Community 42"
Cohesion: 0.33
Nodes (5): CLAUDE.md — OpenClaw (ClawBot), Layout, Project, Rules, Run

### Community 43 - "Community 43"
Cohesion: 0.33
Nodes (5): Next priorities, Open problems / not done (deferred on purpose), Session Handoff, What this session did, Current state

### Community 45 - "Community 45"
Cohesion: 0.33
Nodes (5): ABSOLUTE RULES, Claude Code Prompt | Ronsi95 AI OS | 2026-05-31, CONTEXT, POST-VAULT-RESUME — NEXT STEPS (KEY ALREADY REFRESHED), v1.1 — supersedes v1.0; saved as workflows/post_vault_next.md

### Community 46 - "Community 46"
Cohesion: 0.33
Nodes (5): ABSOLUTE RULES, Claude Code Prompt | Ronsi95 AI OS | 2026-05-31 -> next session, CONTEXT — WHERE WE ARE, Saved as: workflows/session_close.md, SESSION CLOSE + FIRST DAILY-ROUTINE RUN

### Community 48 - "Community 48"
Cohesion: 0.40
Nodes (4): Done (this session), Next up, Not doing (deferred — see DECISIONS.md), Active Tasks

## Knowledge Gaps
- **210 isolated node(s):** `Project`, `Run`, `Layout`, `Rules`, `Requirements` (+205 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Signal` connect `Community 17` to `Community 0`, `Community 4`, `Community 6`, `Community 7`, `Community 18`, `Community 25`, `Community 29`?**
  _High betweenness centrality (0.099) - this node is a cross-community bridge._
- **Why does `BreakoutExpansionStrategy` connect `Community 4` to `Community 17`, `Community 6`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Why does `RSIMACDStrategy` connect `Community 6` to `Community 0`, `Community 1`, `Community 5`, `Community 25`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `Signal` (e.g. with `BreakoutExpansionConfig` and `BreakoutExpansionStrategy`) actually correct?**
  _`Signal` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `TrashHaulingBot` (e.g. with `CalendarSyncAgent` and `OutreachAgent`) actually correct?**
  _`TrashHaulingBot` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Hermes — daily knowledge-graph agent for OpenClaw.  Runs graphify on the codebas`, `Build env for graphify subprocess — inherits current env + .env values.`, `Invoke graphify CLI on the project root. Returns (success, log_output).      Arg` to the rest of the system?**
  _440 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.053860719545550176 - nodes in this community are weakly interconnected._