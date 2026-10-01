# P0.6A.1 — Recoverable Workspace Tool Loop

This hotfix prevents a malformed or semantically invalid model tool request
from aborting the whole governed mission.

- `workspace.read` requires a non-empty relative file path.
- Tool argument failures are returned to the intelligence resource as structured errors.
- The resource may correct the call inside the same mission.
- Attempted tool calls, including failures, are retained in mission evidence.
- Prompt echo protection remains enabled.
- Filesystem writes remain disabled.
