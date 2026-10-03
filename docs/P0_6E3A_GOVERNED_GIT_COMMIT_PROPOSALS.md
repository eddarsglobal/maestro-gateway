# MAESTRO P0.6E.3A — Governed Git Commit Proposals

P0.6E.3A introduces governed Git commit proposals.

This milestone is proposal/approval-only. It does not create Git
commit objects, move refs, change HEAD, modify the real Git index,
push, pull, fetch, or expose arbitrary Git mutation.

## Protocol

- protocol: `P0.6E.3A`
- mode: `proposal_approval_only`
- capability: `git.commit`
- general `git.execute`: unavailable

The commit proposal ledger is stored under:

`~/.maestro/user/git-commits`

## State machine

A proposal begins in:

`AWAITING_APPROVAL`

It may transition to:

- `APPROVED`
- `REJECTED`
- `STALE`

Approval and rejection are state-only operations.

There is no `apply` operation in P0.6E.3A.

## Governed snapshot

A commit proposal is bound to evidence including:

- authorized workspace;
- trusted Git executable fingerprint;
- repository root and Git directory;
- symbolic HEAD branch;
- exact HEAD commit;
- exact raw Git index fingerprint;
- staged path inventory;
- staged raw-diff fingerprint;
- repository-local Git configuration fingerprint;
- HEAD metadata fingerprint;
- normalized commit message.

The approval binding covers both the proposal snapshot and the
normalized commit message.

## Index-only semantics

Commit proposals describe the state that Git would commit from the
index.

A worktree-only edit that does not alter the Git index does not make
the proposal stale.

Changes to the bound HEAD, branch, index, staged inventory,
configuration, or trusted Git runner make approval stale.

## Fail-closed states

P0.6E.3A rejects or fails closed on important unsupported or unsafe
states, including:

- file workspaces;
- non-repositories;
- nested or escaped repository metadata;
- detached HEAD;
- unborn or unresolved HEAD;
- merge, rebase, cherry-pick, revert, or sequencer state;
- unmerged index entries;
- empty staged sets;
- sensitive staged paths;
- repository-local Git config includes;
- incomplete or truncated Git inspection output.

Sensitive staged paths are rejected without reading or returning their
content.

## HTTP surface

P0.6E.3A exposes:

- `GET /git-commits`
- `GET /git-commits/{id}`
- `POST /git-commits/propose`
- `POST /git-commits/{id}/approve`
- `POST /git-commits/{id}/reject`

It intentionally does not expose:

- `/git-commits/{id}/apply`
- arbitrary Git argv;
- `git commit`;
- `commit-tree`;
- `write-tree`;
- `update-ref`;
- fetch, pull, or push.

## Capability contract

For an authorized directory workspace:

`git.commit = available_governed_proposal_approval_only`

For a file workspace:

`git.commit = not_available_requires_directory_workspace`

Without an active workspace:

`git.commit = available_requires_workspace`

General Git mutation remains:

`git.execute = not_available_general_git_mutation_p0_6e3a`

Existing governed Git staging from P0.6E.2B remains available.

## Certification

P0.6E.3A was certified with:

- 18 dedicated Git commit proposal tests;
- 18 capability tests;
- 164 total gateway regression tests;
- live HTTP proposal, GET, list, approve, reject, and stale flows;
- approval anti-replay;
- rejection anti-replay;
- live verification that `/apply` is absent;
- exact HEAD immutability;
- exact index immutability across governed proposal/approval/rejection;
- staged inventory immutability;
- temporary repository commit count remaining unchanged;
- workspace cleanup;
- exact gateway PID shutdown;
- port cleanup;
- no unexpected gateway traceback.

During live certification, a `git write-tree` probe was found to change
the raw index bytes of the temporary repository before the governed
proposal. It was therefore removed from the final immutability probe.

Production P0.6E.3A does not use `write-tree`.

## Security boundary

A model may propose a commit message and request a governed proposal.

The model does not obtain commit authority.

User approval records authorization of the exact bound proposal only.
Approval does not itself create a Git commit.

Commit execution, object creation, ref updates, author/committer
identity, hooks, signing, and transactional ref movement require a
separate future governed milestone.

## Known limitations

P0.6E.3A does not provide commit execution.

The proposal ledger uses local JSON records protected by filesystem
permissions and atomic replacement, but records are not
cryptographically signed against a same-user compromise.

The in-process proposal lock is not a cross-process distributed lock.

The governed snapshot is conservative and may become stale after a
Git index metadata rewrite even when the staged semantic content is
equivalent.

Snapshot collection is not protected by one repository-wide atomic
read transaction. Concurrent external Git mutation during inspection
is therefore not claimed to provide a single atomic repository view.

Only the repository configurations exercised by certification are
claimed as certified. SHA-256 repositories, linked worktrees,
sparse/split indexes, submodules, and intent-to-add edge cases are not
claimed as fully certified by P0.6E.3A.

Any future commit-execution milestone must separately analyze Git
object-database writes, compare-and-swap ref updates, identity,
signing, hooks, crash recovery, and cross-process concurrency.
