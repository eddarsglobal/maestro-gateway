# P0.6B.1 — Governed Change Proposal Engine

P0.6B.1 introduces a proposal-only filesystem change protocol.

The authorized workspace remains protected from direct writes. MAESTRO can
stage a proposed `write`, `edit`, or `patch` as a local change record, compute
before/after SHA-256 fingerprints, generate a bounded unified diff, and place
the proposal into `AWAITING_APPROVAL`.

## State machine

`AWAITING_APPROVAL -> APPROVED | REJECTED | STALE`

Approval is state-only in P0.6B.1. It does **not** apply the proposed content to
the workspace. P0.6B.2 will expose diff approval in the UI; P0.6B.3 will add a
separate guarded application phase with a second freshness check immediately
before an atomic write.

## Safety controls

- session-authenticated local API only;
- authorized workspace required;
- path escape blocked;
- `.git`, virtualenv, build output, and other ignored directories blocked;
- sensitive credential filenames/suffixes blocked;
- existing symlink path components blocked for mutation proposals;
- binary/NUL content rejected;
- 1 MiB file/proposed-content ceiling;
- before/after SHA-256 recorded;
- stale proposals detected at approval;
- no delete capability;
- no terminal or Git execution;
- no workspace write occurs in this phase.

## API

- `GET /changes`
- `GET /changes/{change_id}`
- `POST /changes/propose`
- `POST /changes/{change_id}/approve`
- `POST /changes/{change_id}/reject`
