# MAESTRO P0.6E.1 — Governed Git Inspection

P0.6E.1 introduces dedicated read-only Git inspection.

It does not enable arbitrary Git execution and it does not extend terminal.run.

Available tools:

- git.status
- git.diff
- git.log
- git.branch_current

## Workspace boundary

Git inspection requires an authorized directory workspace.

The authorized directory must itself be the Git repository root.

Repositories whose Git metadata resolves outside the authorized workspace,
including linked worktrees whose .git metadata escapes the workspace, are
rejected.

## Execution boundary

MAESTRO owns the Git argv.

The model cannot provide arbitrary Git flags, commands, revisions or shell
strings.

The outer process uses:

- shell=False
- stdin disabled
- private HOME
- no inherited global Git configuration
- no system Git configuration
- no Git terminal prompts
- no pager
- GIT_OPTIONAL_LOCKS=0
- bounded streamed stdout/stderr
- SHA-256 evidence
- timeout and process-group termination

git.diff additionally disables:

- external diff helpers
- textconv helpers

No network Git operation is available.

## Operations

### git.status

Uses porcelain v2 with branch metadata and untracked files.

### git.diff

Supports only:

- worktree
- staged

No arbitrary revision expression is accepted in P0.6E.1.

### git.log

Accepts only max_count from 1 through 50.

### git.branch_current

Returns the symbolic branch when available and the current HEAD SHA when one
exists.

## Mutation

The following remain unavailable through P0.6E.1:

- add
- commit
- reset
- checkout
- switch
- restore
- clean
- merge
- rebase
- tag mutation
- branch mutation
- fetch
- pull
- push

The legacy git.execute capability therefore remains unavailable.

P0.6E.1 is governed read-only Git inspection, not a general OS sandbox.
