# Skill: Ronsi95 AI OS — Operating Manual

How the Hermes orchestration layer is wired on this box.

## Bodies (separate, by design)
- **CryptoBot / ClawBot** — the existing live crypto trading body (Crypto.com DCA).
  Hermes reads its signals **read-only**; Hermes never imports or modifies it.
- **Vibe-Trading** — paper-only futures analysis bridge (Lucid 25K mandate). Recommends only.
- **Hermes** — non-trading orchestrator: comms, briefings, dispatch, scheduling.

## Sub-agents (all isolated, all kill-switchable)
| Agent | Path | Role | Safety |
|---|---|---|---|
| trading-agent | `sub-agents/trading-agent/tjr_extractor.py` | Pull TJR funded-account strategy transcripts | read-only scrape; output to local files |
| crypto-agent | `sub-agents/crypto-agent/cryptobot_reader.py` | Read CryptoBot signals from logs | **read-only**, never imports CryptoBot |
| haulyall-agent | `sub-agents/haulyall-agent/haulyall_ops.py` | HaulYA'LL draft content + job log | **draft-only**, never auto-posts |
| vibe bridge | `vibe-trading/agent/vibe_agent.py` | Paper trade analysis under Lucid mandate | paper-only, recommend-only, no keys |

## Commands (orchestrator surface)
- `/status`   — health of each sub-agent + freshness of their output files
- `/briefing` — daily roll-up (portfolio, paper-watch, DCA confirm, task freshness)
- `/trade`    — Vibe-Trading paper recommendation (analysis only)
- `/crypto`   — latest CryptoBot read-only signal summary
- `/haul`     — generate a HaulYA'LL draft post (awaits approval)

## Hard rules (carried from this repo's locked guardrails)
- Trading code is **wrapped, never rewritten**; live trading path is untouched.
- Every sub-agent halts on its `KILL_SWITCH` file.
- Secrets stay in `.env` files; nothing is hardcoded; outputs are DRAFT until approved.
- Hermes keeps its **own** `~/.hermes/.env` — it does not read CryptoBot's keys.
