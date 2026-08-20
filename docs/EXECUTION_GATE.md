# Planning-to-Execution Gate

The Execution Gate is a first-class safety and workflow module.

## States

| State | Meaning |
|---|---|
| PLANNING | Task is being interpreted. No execution. |
| READY_FOR_APPROVAL | Plan is sufficiently defined. |
| EXECUTING | User-approved mode is active. |
| COMPLETE | Execution finished. |
| BLOCKED | Missing inputs or risk blocks execution. |

## Required output

- Mission
- Active vector
- Inputs required
- Inputs available
- Assumptions
- Gaps
- Risks
- Plan options
- Recommended mode
- Readiness percentage
- Approval lock state
- Execution trace ID

## Supported modes

- MANUAL_MODE
- TERMINAL_MODE
- LOCAL_IN_CHAT_MODE
- CLAUDE_CODE_MODE
- GITHUB_PR_MODE
- DESIGN_MODE
- AUTOMATION_MODE
