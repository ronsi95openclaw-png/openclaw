# Task Lifecycle

## State Machine

Tasks progress through defined states with clear transitions and responsibilities.

### States

| State | Description | Responsible Agent | Actions |
|-------|-------------|-------------------|---------|
| **Inbox** | New task arrived, not yet triaged | Orchestrator | Review, assign priority, route to appropriate agent |
| **Assigned** | Routed to agent, work not started | Assigned Agent | Acknowledge assignment, start work |
| **In Progress** | Agent actively working | Assigned Agent | Update progress, comment on blockers |
| **Review** | Quality check in progress | Reviewer | Check quality, provide feedback |
| **Revision** | Failed review, back to agent | Assigned Agent | Address feedback, resubmit |
| **Done** | Approved and delivered | Orchestrator | Mark complete, archive, report |

### Transitions

- **Inbox → Assigned**: Orchestrator assigns based on agent capabilities and workload
- **Assigned → In Progress**: Agent starts work within defined timeframe
- **In Progress → Review**: Agent completes work and submits for review
- **Review → Done**: Reviewer approves work
- **Review → Revision**: Reviewer rejects with specific feedback
- **Revision → Review**: Agent addresses feedback and resubmits

### Comments and Updates

Agents must comment on task progress at key points:
- **Start**: "Starting work on [task]"
- **Blocker**: "Blocked on [issue], need [help]"
- **Handoff**: "Completed [deliverable], ready for review"
- **Completion**: "Task complete, delivered [artifacts]"

### Timeouts and Escalation

- Tasks stuck in "Assigned" > 1 hour: Escalate to orchestrator
- Tasks stuck in "In Progress" > 24 hours: Escalate to orchestrator
- Tasks stuck in "Review" > 4 hours: Escalate to orchestrator
- Tasks stuck in "Revision" > 12 hours: Escalate to human

### Quality Gates

Before moving to "Done":
- Code: Passes linting, tests, security scan
- Docs: Clear, accurate, complete
- Config: Validated, tested in staging
- All artifacts: Verified by reviewer