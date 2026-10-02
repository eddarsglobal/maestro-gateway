# MAESTRO P0.6D.2B — Governed Project Execution

P0.6D.2B adds real project build/test/validation execution after a separately
approved P0.6D.2B proposal.

Lifecycle:

PROPOSED
-> APPROVED
-> EXECUTION_STARTED
-> PASSED | FAILED | TIMED_OUT | EXECUTION_ERROR

Approval never starts project code.

Execution requires a separate request with literal:

`confirm_execution=true`

## Profiles

The executable argv remains owned by MAESTRO and is derived from detected
profiles.

The requester cannot supply an arbitrary argv.

Initial profiles remain:

- python_unittest
- node_npm_build
- node_npm_lint
- node_npm_test only when explicitly defined by package.json

## Process policy

MAESTRO:

- invokes the outer process with shell=False;
- disables stdin;
- uses a minimal environment rather than inheriting user secrets;
- uses a dedicated private HOME/TMP/npm cache;
- bounds retained stdout and stderr;
- hashes the complete captured streams;
- runs the process in a new process session;
- terminates the process group on timeout;
- records exit code, duration and execution evidence;
- revalidates the profile and workspace snapshot immediately before execution;
- rejects replay after execution.

## Security boundary

P0.6D.2B is not an operating-system sandbox.

Project code may itself:

- spawn child processes;
- create its own new sessions;
- access files permitted to the local user;
- access the network;
- mutate workspace content;
- perform other actions available to the user account.

Therefore:

- network_isolation remains false;
- os_filesystem_sandbox remains false.

For npm scripts, npm may itself launch a shell to interpret the package script
even though MAESTRO launches npm with shell=False.

## Legacy P0.6D.2A proposals

P0.6D.2A audit proposals are not executable retroactively.

Only proposals created under protocol P0.6D.2B may enter EXECUTION_STARTED.

## API

Execution endpoint:

POST /executions/{run_id}/execute

Body:

{
  "confirm_execution": true
}

The boolean must be a literal JSON true value.
