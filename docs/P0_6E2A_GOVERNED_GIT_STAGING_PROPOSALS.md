# MAESTRO P0.6E.2A — Governed Git Staging Proposals

P0.6E.2A introduces governed Git staging proposals.

It does not execute `git add`.

## Lifecycle

AWAITING_APPROVAL -> APPROVED

or:

AWAITING_APPROVAL -> REJECTED

A proposal can also become STALE if the bound repository state changes.

## Explicit paths only

A proposal must contain explicit repository-relative file paths.

Repository-wide staging is forbidden.

Examples that are rejected:

- `.`
- absolute paths
- `../...`
- sensitive credential paths
- symlink targets
- ignored files
- conflicted/unmerged paths
- rename/copy status records

## Bound state

Proposal approval revalidates the deterministic snapshot bound at proposal
time, including:

- Git index SHA-256/state
- selected worktree file state
- selected index entries
- repository-local Git config
- relevant Git attributes
- Git runner identity and SHA-256

Repository-local config includes are rejected.

External `core.attributesFile` is rejected.

Git clean filters on selected paths are rejected.

## API

Read:

- GET /git-stages
- GET /git-stages/{stage_id}

Governed state transitions:

- POST /git-stages/propose
- POST /git-stages/{stage_id}/approve
- POST /git-stages/{stage_id}/reject

There is deliberately no apply endpoint in P0.6E.2A.

## Important boundary

Proposal creation does not modify `.git/index`.

Approval does not modify `.git/index`.

Rejection does not modify `.git/index`.

`git.execute` remains unavailable.

P0.6E.2B will separately address explicitly approved staging application.
