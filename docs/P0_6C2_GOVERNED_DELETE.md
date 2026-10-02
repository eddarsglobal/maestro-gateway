# MAESTRO P0.6C.2 — Governed Recoverable Delete

P0.6C.2 adds governed deletion of regular files.

Directories, recursive deletion, symlinks, ignored/protected paths,
and sensitive credential targets are not supported.

## Authority separation

PROPOSE != APPROVE != APPLY

Proposal creation does not delete.

Approval does not delete.

Delete approval additionally requires:

    confirm_delete=true

The file must still exist after approval.

## Fingerprint

A delete proposal captures:

- SHA-256;
- inode;
- device;
- size;
- mode;
- authorized workspace path.

The fingerprint is revalidated during approval, during apply, and
again immediately before unlink.

## Recovery staging

Before workspace deletion MAESTRO creates a private recovery copy
under the local change store.

Recovery directory permissions are requested as 0700.

Recovery file permissions are requested as 0600.

The private absolute recovery path is not exposed by the public
proposal API.

The staged copy must match the proposal SHA-256 before deletion can
continue.

## Delete execution

1. APPLY_STARTED is persisted.
2. Recovery bytes are copied and fsynced.
3. RECOVERY_STAGED is persisted.
4. Source fingerprint is checked again.
5. Source is unlinked.
6. Source directory is fsynced.
7. Source absence and recovery SHA are verified.
8. Proposal becomes APPLIED.

## Rollback

If verification fails after unlink, MAESTRO restores from the
private recovery copy.

Restoration is NO-OVERWRITE: an unexpected file appearing at the
original path is never overwritten.

## Crash completion

If MAESTRO restarts after unlink but before APPLIED is persisted,
a later apply can complete the transaction when:

- APPLY_STARTED is present;
- source is absent;
- private recovery stage exists;
- recovery SHA matches the proposal.

## Scope

P0.6C.2 remains file-only.

Recovery bytes are retained after successful apply. Automatic
retention/garbage-collection policy is intentionally deferred to a
later governed lifecycle stage.
