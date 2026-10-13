# V8.1 source-control integration candidate

The consolidated V8.1 source was staged in an isolated Git clone, without deleting repo-only files.
This candidate is not committed, pushed or production-certified.

## Official tri-repository source-control contract (V8.1 reconciliation)

- Official local roots: `/Users/noury/Documents/App_py/MAESTRO_AGENT/Github/maestro`,
  `/Users/noury/Documents/App_py/MAESTRO_AGENT/Github/maestro-gateway`,
  `/Users/noury/Documents/App_py/MAESTRO_AGENT/Github/maestro-ui`.
- Official GitHub repositories: `eddarsglobal/maestro`, `eddarsglobal/maestro-gateway`,
  `eddarsglobal/maestro-ui`. This document is installed separately in each repository.
- V8.1 source ZIP SHA-256: `446a9fcbbd5d96773e119e3733d919141f86ec9e1430c9e3030cc7bf49fc7503`.
- Baseline source commits: `maestro=c8f425af1f50810b9cbf8dc779d4c642ceea2215`;
  `maestro-gateway=0e35e8056e774ab5ba89c7b2b58d9f83ec53ed78`;
  `maestro-ui=dd15753e8110ef4b7f798a11cf85caad0d435b53`.
- **Source of truth**: each official Git repository and its reviewed commits. ZIPs are
  immutable delivery snapshots, not a substitute for Git history.
- Maintain coordinated tri-repo commit provenance for each reviewed integration.
  Do not overwrite independent LICENSE, CI, governance or documentation files.
- Guardian rules: verify the three repo identities, clean source history,
  integrated regressions, reviews, GitHub CI, and release evidence. Never mark a
  gate PASS merely because a ZIP test passed. Do not auto-push or force-push.
- The legacy consolidated-package Guardian remains the G0/G1/G2/G3 test gate.
  The repo-local Guardian adapter only checks source-control identity; it does
  **not** replace the consolidated certification tests.
- Local-first: English is the default among EN/FR/DE/AR/PT/ES/HR/IT; retain
  one active conversational LLM, provider neutrality, no automatic cloud-primary
  inference or model downloads, and no semantic claims from tiny probes.
- **Release status: BLOCKED**. G1 full P0 closure, G2 semantic language review
  and G3 notarized macOS distribution are not certified by this integration.
