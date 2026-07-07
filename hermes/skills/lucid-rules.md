# Skill: Lucid 25K Eval Rules

Authoritative trading-mandate reference for the Vibe-Trading sub-agent.
Source of truth: `vibe-trading/lucid_mandate.json` (this doc mirrors it for the LLM).

## Account
- **Account:** lucid_25k_eval
- **Size:** $25,000
- **Mode:** paper (never live until a separate, explicit promotion)

## Hard limits
| Rule | Value |
|---|---|
| Drawdown type | End-of-day |
| Max loss limit | $1,500 |
| Daily loss limit | (none set) |
| Consistency rule (eval) | 50% — no single day may exceed 50% of total profit |
| Overnight holds | **Not allowed** |
| Close end-of-day | **Required** |
| Allowed instruments | ES, MES, NQ, MNQ **only** |
| Max position size | 2 contracts |
| Daily trade cap | 10 |
| Min profitable days (payout) | 5 |

## Enforcement
- The bridge (`vibe-trading/agent/vibe_agent.py`) loads these rules and **clamps**
  every recommendation: instrument must be in the allowed list, size is capped at 2.
- A `KILL_SWITCH` file in `vibe-trading/` halts the agent before any action.
- This agent **recommends only** — it does not place orders or hold keys.
