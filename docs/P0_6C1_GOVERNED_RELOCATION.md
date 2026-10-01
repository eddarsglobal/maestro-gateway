# MAESTRO P0.6C.1 — Governed Rename / Move

P0.6C.1 adds governed file relocation.

Supported operations:

- rename: same parent directory;
- move: another existing directory in the same authorized workspace.

Directories are not relocated in this phase.

## Authority separation

PROPOSE != APPROVE != APPLY

Proposal and approval perform no filesystem relocation.

## Evidence

A relocation proposal records:

- source path;
- destination path;
- source SHA-256;
- source inode/device;
- source size;
- destination absence;
- human approval state;
- execution history.

## Collision policy

MAESTRO must never overwrite an unexpected destination.

P0.6C.1 deliberately avoids os.replace(source, destination).

Execution uses a NO-OVERWRITE hard-link transaction for regular
files on the same filesystem:

1. atomically create destination hard link;
2. verify destination inode/SHA;
3. remove source;
4. verify source absent and destination SHA;
5. commit APPLIED.

APPLY_STARTED provides crash recovery for either intermediate state.

## Rollback

If execution fails after destination creation:

- if source remains, destination link is removed;
- if source was removed, source is recreated as a hard link before
  destination is removed;
- restored SHA is verified.

Unexpected destination collisions are never overwritten.
