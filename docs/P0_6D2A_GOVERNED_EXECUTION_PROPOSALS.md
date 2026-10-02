# MAESTRO P0.6D.2A — Governed Project Execution Proposals

P0.6D.2A introduces profile detection and explicit human approval for
project-code execution.

It does not execute builds, tests, linters, interpreters, package scripts,
or arbitrary commands.

## Profiles

Initial profiles:

- `python_unittest`
- `node_npm_build`
- `node_npm_lint`
- `node_npm_test` only when a project explicitly defines a test script

The model does not provide an argv.

MAESTRO detects the project and owns the planned argv.

## Governance

Lifecycle:

`PROPOSED -> AWAITING_APPROVAL -> APPROVED`

or:

`PROPOSED -> STALE`

or:

`PROPOSED -> REJECTED`

Approval never executes project code.

`execution_performed` remains false throughout P0.6D.2A.

Before approval, MAESTRO recomputes a bounded workspace snapshot. Changes to
the execution profile or governed workspace snapshot make the proposal stale.

## Risk boundary

Project builds and tests are not equivalent to P0.6D.1 inspection commands.

They may execute project-controlled code, spawn subprocesses, mutate files,
or access the network.

P0.6D.2A therefore records:

- `project_code_execution=true`
- `outer_shell=false`
- `network_isolation=false`
- `os_filesystem_sandbox=false`
- `risk=high`

For npm profiles, the npm runner may itself spawn a command shell for package
scripts. MAESTRO does not claim otherwise.

## API

- `GET /execution-profiles?workspace_id=...`
- `GET /executions`
- `GET /executions/{run_id}`
- `POST /executions/propose`
- `POST /executions/{run_id}/approve`
- `POST /executions/{run_id}/reject`

There is intentionally no execute endpoint in P0.6D.2A.
