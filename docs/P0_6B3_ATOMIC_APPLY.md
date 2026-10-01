# MAESTRO P0.6B.3 — Governed Atomic Apply

P0.6B.3 introduces the first authorized physical filesystem
mutation in MAESTRO.

## Separation of authority

APPROVE != APPLY

Approval records human authorization only.
Apply is a separate execution operation.

## State path

AWAITING_APPROVAL
-> APPROVED
-> APPLY_STARTED
-> APPLIED

Failure paths include:

- STALE
- APPLY_FAILED
- ROLLED_BACK
- ROLLBACK_FAILED
- INTEGRITY_ERROR

## Apply contract

Before execution MAESTRO:

1. requires status APPROVED;
2. resolves workspace policy again;
3. checks file existence state;
4. checks current SHA-256 against before_sha256;
5. verifies stored proposed content against after_sha256.

Physical execution:

1. write same-directory temporary file;
2. flush + fsync temporary content;
3. re-check current target fingerprint;
4. perform atomic os.replace();
5. best-effort fsync parent directory;
6. re-read target;
7. verify after_sha256.

On post-replace failure, MAESTRO attempts rollback and verifies
the restored state.

APPLIED proposals are not replayable.

The workspace mutation boundary therefore remains explicitly
governed, auditable, and SHA-bound.
