# OpenClaw System Audit
> [[index]]
> Audited: 2026-04-17 | Auditor: ClawBot Audit Agent

## Summary

| Category | Total | Functional | Broken | Missing |
|---|---|---|---|---|
| Telegram commands (registered) | 32 | 32 | 0 | 0 |
| Telegram commands (planned, not wired) | 8 | 0 | 0 | 8 |
| Dashboard routes | 6 | 5 | 1 | 0 |
| Dashboard API endpoints | 9 | 9 | 0 | 0 |
| Agent chat endpoints | 6 | 6 | 0 | 0 |
| Orchestrator multi-agent functions | 3 | 0 | 0 | 3 |

**Functional rate: 62/68 features = 91%**
**Critical gaps: 9 items need immediate wiring**

## Key Risks

1. **CashClaw pipeline not accessible from Telegram** — 8 commands exist in agent files but were never registered in `content/receiver.py`. The entire income engine (Scout → HumanVoice → Applier) cannot be triggered by Ronnie via chat.
2. **Holdings page permanently broken** — `CRYPTOCOM_API_KEY` returns error 10002 (UNAUTHORIZED). All live balance data unavailable.
3. **Multi-agent orchestrator missing 3 core functions** — `forward_message`, `validate_agent_output`, `sweep_stale_tasks` were designed but not implemented in `skills/agent_team_orchestrator.py`.
4. **`/fng` command in dashboard quick bar has no Telegram handler** — button copies `/fng` to clipboard but no handler registered.

## Links
- [[feature-map]] — Full command/button table
- [[failure-log]] — Each failure with root cause + fix
- [[backend-architecture]] — How the system actually works
- [[improvement-roadmap]] — Priority fixes ranked by impact
- [[autopilot-audit]] — Deep command and route audit
