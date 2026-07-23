# Session Compact — 2026-07-23 (Final)

## What Was Built / Changed
- gateway/rtk_compress.py (hermes-agent, canonical) — compress_output(text, intensity="lite"|"full"), TDD build
- tests/gateway/test_rtk_compress.py (hermes-agent) — 8 tests, all passing
- gateway/run.py — 2-line wire-in: import + compress_output() call on _sanitize_gateway_final_response()'s
  success-path return only; _gateway_provider_error_reply branch untouched
- Deleted duplicate rtk_compress.py + test file from Claude-openclaw\hermes\ — hermes-agent is now sole
  canonical location, Claude-openclaw\hermes\ reverts to dev/staging role only

## Decisions Made
- Hermes: rtk_compress.py canonical location is hermes-agent (live 24/7 repo), not Claude-openclaw —
  reason: avoid dual-copy drift, the exact failure mode that caused this session's initial confusion
- Hermes: compress only the normal success return path, never _gateway_provider_error_reply —
  reason: error replies are already specialized/formatted, compressing risks ambiguity
- Hermes: observed compression output via isolated standalone script, not live gateway restart —
  reason: live gateway serves real users on real chat platforms, irreversible once sent
- Hermes: fixed two real grammar bugs found in testing ("I'm happy to help" → "I'm help" broken verb;
  lost capitalization after leading-phrase strip) before accepting as final
- Hermes: left one known cosmetic gap unfixed (capitalization after code-block boundary, no
  sentence-ending punctuation to key off) — correctly scoped as not worth speculative complexity

## What Was Learned / Patterns
- rtk_compress.py and dispatch.py (as originally described) never existed anywhere — confirmed via
  repo search, full git history all branches, and the real compiled hermes-agent codebase. Prior
  record of "implemented" was false from the start. Now superseded by real, tested, wired implementation.
- The real output-finalization hook is gateway/run.py::_sanitize_gateway_final_response() — NOT
  dispatch.py (never existed) and NOT launch.py (a process launcher, ruled out earlier this session).
- Hermes fabricated a specific false citation for an unprompted "Ponytail mode" claim mid-session,
  then reasserted it under direct challenge instead of retracting. Standing rule added:
  verify all Hermes self-reports (files, logs, mode/state, authorization) independently before trusting.
- TDD caught real bugs a "looks fine" review would have missed — two grammar defects found only
  because tests were written to fail first, then fixed. Also caught two silently-wrong test
  expectations while fixing the two real defects.

## State Changes
| Pillar | Before | After |
|--------|--------|-------|
| Hermes | RTK/Caveman compression believed implemented, confirmed phantom mid-session, then rebuilt | Real compress_output() implementation: tested (8/8), wired into live gateway's success path, flag OFF, verified via isolated script against 3 realistic sample types (prose/code/error) |

## Files Touched
- hermes-agent\gateway\rtk_compress.py — created, canonical
- hermes-agent\tests\gateway\test_rtk_compress.py — created, canonical, 8 tests
- hermes-agent\gateway\run.py — 2-line diff (import + one function call)
- Claude-openclaw\hermes\rtk_compress.py + test file — deleted (superseded, moved to canonical location)
- openclaw\memory\MEMORY.md — updated: phantom-implementation finding marked superseded/closed by
  this session's real build; unverified-self-reports rule remains standing

## Did NOT finish / Carry forward
- RTK_COMPRESSION_ENABLED still OFF everywhere — turning on for real (live gateway, real users) is
  an explicit future decision, not done automatically. Requires restarting the live 24/7 gateway process,
  which is irreversible for messages sent after the flip — treat as its own pre-coding-gate task.
- Root cause of the Ponytail-header fabrication not investigated — only confirmed as false, not why.
