---
name: agent-team-orchestration
description: "Orchestrate multi-agent teams within Claude-openclaw with defined roles, task lifecycles, handoff protocols, and review workflows. Use when: (1) Setting up a team of 2+ agents with different specializations, (2) Defining task routing and lifecycle (inbox → spec → build → review → done), (3) Creating handoff protocols between agents, (4) Establishing review and quality gates, (5) Managing async communication and artifact sharing between agents."
---

# Agent Team Orchestration

Production playbook for running multi-agent teams within the Claude-openclaw system with clear roles, structured task flow, and quality gates.

## Quick Start: Minimal 2-Agent Team

A builder and a reviewer. The simplest useful team.

### 1. Define Roles

```
Orchestrator (core/brain.py) — Route tasks, track state, report results
Builder agent               — Execute work, produce artifacts
```

### 2. Spawn a Task

```
1. Create task record in data/tasks.json
2. Spawn builder with:
   - Task ID and description
   - Output path for artifacts
   - Handoff instructions (what to produce, where to put it)
3. On completion: review artifacts, mark done, report via Telegram
```

### 3. Add a Reviewer

```
Builder produces artifact → Reviewer checks it → Orchestrator ships or returns via content modules
```

That's the core loop. Everything below scales this pattern.

## Core Concepts

### Roles

Every agent has one primary role. Overlap causes confusion.

| Role | Purpose | Model guidance |
|------|---------|---------------|
| **Orchestrator** | Route work, track state, make priority calls | High-reasoning model (handles judgment) |
| **Builder** | Produce artifacts — code, docs, configs | Can use cost-effective models for mechanical work |
| **Reviewer** | Verify quality, push back on gaps | High-reasoning model (catches what builders miss) |
| **Ops** | Cron jobs, standups, health checks, dispatching | Cheapest model that's reliable |

→ *Read [references/team-setup.md](references/team-setup.md) when defining a new team or adding agents.*

### Task States

Tasks move through states. Track them explicitly in data/tasks.json.

| State | Description | Next |
|-------|-------------|------|
| **Inbox** | New task arrived, not yet triaged | Assigned |
| **Assigned** | Routed to agent, work in progress | In Progress |
| **In Progress** | Agent actively working | Review |
| **Review** | Quality check in progress | Revision or Done |
| **Revision** | Failed review, back to agent | Review |
| **Done** | Approved and delivered | — |

### Handoffs

When work passes between agents, the handoff message includes:

1. **What was done** — summary of changes/output
2. **Where artifacts are** — exact file paths
3. **How to verify** — test commands or acceptance criteria
4. **Known issues** — anything incomplete or risky
5. **What's next** — clear next action for the receiving agent

Bad handoff: *"Done, check the files."*
Good handoff: *"Built auth module at `/shared/artifacts/auth/`. Run `npm test auth` to verify. Known issue: rate limiting not implemented yet. Next: reviewer checks error handling edge cases."*

### Reviews

Every artifact gets eyes before shipping. Reviews catch what builders miss.

- **Self-review**: Builder checks own work first
- **Peer review**: Another agent reviews
- **Quality gates**: Automated checks (tests, lint, etc.)
- **Escalation**: If review fails, route back with specific feedback

## Reference Files

| File | Read when... |
|------|-------------|
| [team-setup.md](references/team-setup.md) | Defining agents, roles, models, workspaces |
| [task-lifecycle.md](references/task-lifecycle.md) | Designing task states, transitions, comments |
| [communication.md](references/communication.md) | Setting up async/sync communication, artifact paths |
| [patterns.md](references/patterns.md) | Implementing specific workflows (spec→build→test, parallel research, escalation) |

## Integration with Claude-openclaw

This skill integrates with your existing bot architecture:

- **Task storage**: Uses data/tasks.json for task tracking
- **Agent spawning**: Leverages core/brain.py for agent management
- **Communication**: Uses content/ modules for artifact handling
- **User interface**: Reports progress via Telegram through receiver.py
- **Scheduling**: Integrates with core/scheduler.py for timed operations

## Common Pitfalls

### Spawning without clear artifact output paths
Agent produces great work, but you can't find it. Always specify the exact output path in the spawn prompt. Use a shared artifacts directory with predictable structure.

### No review step = quality drift
"It's a small change, skip review." Do this three times and you have compounding errors. Every artifact gets at least one set of eyes that didn't produce it.

### Agents not commenting on task progress
Silent agents create coordination blind spots. Require comments at: start, blocker, handoff, completion. If an agent goes silent, assume it's stuck.

### Not verifying agent capabilities before assigning
Assigning browser-based testing to an agent without browser access. Assigning image work to a text-only model. Check capabilities before routing.

### Orchestrator doing execution work
The orchestrator routes and tracks — it doesn't build. The moment you start "just quickly doing this one thing," you've lost oversight of the rest of the team.

## When NOT to Use This Skill

- Single-agent tasks (just spawn directly)
- Unstructured collaboration (use chat instead)
- Real-time pair programming (use shared sessions)
- When agents need to negotiate requirements (use human-in-the-loop)

This skill is for orchestrated, asynchronous multi-agent work with clear deliverables and quality gates.