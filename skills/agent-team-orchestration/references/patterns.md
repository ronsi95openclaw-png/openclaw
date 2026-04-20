# Patterns

Proven multi-agent workflows. Copy and adapt.

## Spec → Review → Build → Test

Sequential workflow for high-quality deliverables.

### Roles
- **Spec Agent**: Requirements gathering and specification
- **Review Agent**: Spec validation and feedback
- **Build Agent**: Implementation
- **Test Agent**: Quality assurance

### Flow
1. Spec Agent creates detailed requirements
2. Review Agent validates spec completeness
3. Build Agent implements per approved spec
4. Test Agent validates implementation

### Quality Gates
- Spec must pass review before build starts
- Build must pass tests before delivery
- All artifacts reviewed by non-creator

## Parallel Research

Fan-out research with synthesis.

### Roles
- **Research Coordinator**: Topic breakdown and assignment
- **Research Agents**: Individual research tasks
- **Synthesis Agent**: Combine findings into coherent output

### Flow
1. Coordinator breaks topic into sub-questions
2. Multiple research agents work in parallel
3. Synthesis agent combines results
4. Coordinator reviews final output

### Quality Gates
- Each research agent provides sources and methodology
- Synthesis includes conflict resolution for contradictory findings
- Final output cites all contributing research

## Escalation

Handling blockers and edge cases.

### Roles
- **Primary Agent**: Handles standard cases
- **Escalation Agent**: Handles complex/blocked cases
- **Human**: Final escalation point

### Flow
1. Primary agent attempts task
2. If blocked, escalates to escalation agent
3. If still blocked, escalates to human
4. Human provides guidance or resolution

### Quality Gates
- Clear escalation criteria defined upfront
- Escalation includes full context and attempted solutions
- Resolution documented for future reference

## Cron-Based Ops

Scheduled maintenance and monitoring.

### Roles
- **Ops Agent**: Executes scheduled tasks
- **Monitor Agent**: Checks system health
- **Alert Agent**: Handles anomalies

### Flow
1. Ops agent runs scheduled tasks (backups, updates, etc.)
2. Monitor agent checks results and system status
3. Alert agent notifies if issues detected

### Quality Gates
- Tasks complete within time windows
- Monitoring covers all critical systems
- Alerts include actionable remediation steps

## Batch Processing

High-volume task processing.

### Roles
- **Batch Coordinator**: Manages queue and distribution
- **Worker Agents**: Process individual items
- **Aggregator**: Combines results

### Flow
1. Coordinator receives batch of tasks
2. Distributes to available workers
3. Workers process in parallel
4. Aggregator combines outputs

### Quality Gates
- All items processed (no drops)
- Consistent output format across workers
- Aggregated results validated

## Review Rotation

Distributed quality assurance.

### Roles
- **Builder Agents**: Create artifacts
- **Review Pool**: Rotating reviewers
- **Quality Coordinator**: Manages review assignments

### Flow
1. Builder completes work
2. Quality coordinator assigns reviewer from pool
3. Reviewer provides feedback
4. Builder addresses feedback or appeals

### Quality Gates
- Reviewers have relevant expertise
- Feedback is specific and actionable
- Appeals process prevents review bias