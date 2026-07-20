# Communication

## Async vs Sync Communication

### Async Communication (Preferred)
- **File-based artifacts**: Code, docs, configs stored in shared directories
- **Task comments**: Progress updates, blocker notifications, handoff messages
- **Status updates**: State changes, completion notifications
- **Review feedback**: Specific, actionable feedback with examples

### Sync Communication (When Needed)
- **Real-time collaboration**: Pair programming sessions
- **Requirement clarification**: Direct Q&A with orchestrator
- **Emergency escalation**: Immediate human intervention needed

## Artifact Paths

### Directory Structure
```
shared/
├── artifacts/          # All deliverables
│   ├── [task-id]/     # Task-specific artifacts
│   └── reviews/       # Review feedback
├── tasks/             # Task metadata and state
└── logs/              # Communication logs
```

### Naming Conventions
- **Artifacts**: `[task-id]_[artifact-type]_[version].[ext]`
- **Reviews**: `[task-id]_review_[reviewer]_[timestamp].md`
- **Logs**: `[task-id]_comm_[timestamp].log`

## Handoff Protocol

### Required Elements
Every handoff message must include:

1. **Task Summary**: What was accomplished
2. **Artifact Locations**: Exact file paths
3. **Verification Steps**: How to test/validate
4. **Known Issues**: Limitations, risks, incomplete work
5. **Next Steps**: What the receiving agent should do

### Format Template
```markdown
## Handoff: [Task Name]

### Completed
- [Summary of work done]
- [Key changes/decisions]

### Artifacts
- `[path/to/artifact1]`: [description]
- `[path/to/artifact2]`: [description]

### Verification
- [Step 1]: [command/result]
- [Step 2]: [command/result]

### Known Issues
- [Issue 1]: [impact, workaround]
- [Issue 2]: [impact, workaround]

### Next Steps
[Receiving agent should...]
```

## Communication Channels

### Primary Channels
- **Task Files**: data/tasks.json for state tracking
- **Artifact Directories**: shared/artifacts/ for deliverables
- **Log Files**: For audit trails and debugging

### Secondary Channels
- **Telegram Bot**: For human notifications and escalations
- **Dashboard**: For monitoring and status updates
- **Email**: For scheduled reports and alerts

## Escalation Paths

### Automatic Escalation
- **Silent Agents**: No updates > 2 hours → notify orchestrator
- **Failed Reviews**: 3+ revisions → notify human
- **Timeout Violations**: State timeouts exceeded → notify human

### Manual Escalation
- **Requirement Changes**: Agent detects scope creep → orchestrator
- **Technical Blockers**: Agent cannot proceed → orchestrator
- **Quality Concerns**: Reviewer finds critical issues → human