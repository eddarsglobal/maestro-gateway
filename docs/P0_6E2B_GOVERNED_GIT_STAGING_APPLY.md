# MAESTRO P0.6E.2B — Governed Git Staging Apply

P0.6E.2B extends P0.6E.2A with a separate,
explicit and transactional Git staging apply.

## Governance

- proposal creation does not modify `.git/index`;
- approval does not modify `.git/index`;
- apply requires status `APPROVED`;
- apply requires literal `confirm_stage=true`;
- only the explicit approved paths may change;
- repository-wide stage-all remains unavailable;
- arbitrary Git mutation remains unavailable.

## HTTP

P0.6E.2B adds:

- `POST /git-stages/{stage_id}/apply`

Request:

```json
{"confirm_stage": true}
```

## Transaction

A private candidate index is prepared through
`GIT_INDEX_FILE` with literal pathspec handling.

MAESTRO verifies the approved snapshot again after
candidate preparation and before entering `APPLYING`.

The real index is changed only after exclusive
creation of `.git/index.lock` and verification that
the approved real-index SHA-256 is still current.

Applied proposals are anti-replay.

Verification failure triggers an exact-index rollback
attempt. Startup recovery can finalize a committed
apply when the real index matches the recorded
candidate index.

## Scope limitation

Candidate `git add` may create unreferenced Git objects
before the real index transaction commits. P0.6E.2B
therefore does not claim zero repository mutation
before index commit.

Sparse/split indexes, submodules and SHA-256 repository
formats are not claimed as fully certified here.
