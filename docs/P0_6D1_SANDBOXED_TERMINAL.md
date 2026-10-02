# MAESTRO P0.6D.1 — Governed Sandboxed Terminal

P0.6D.1 introduces a deliberately narrow terminal execution surface.

## Security model

`terminal.run` is inspection-only.

It:

- requires an authorized directory workspace;
- accepts structured `argv[]`, never a shell command string;
- always executes with `shell=False`;
- disables stdin;
- uses a minimal environment;
- confines `cwd` and file operands to the authorized workspace;
- denies protected and credential paths;
- enforces a 100–5000 ms timeout;
- limits exposed stdout/stderr to 32 KB;
- records SHA-256 execution evidence;
- does not expose Git, shells, interpreters, package managers, network tools,
  or filesystem mutation commands.

## Allowlisted commands

- `pwd`
- `head`
- `tail`
- `wc`
- `stat`

Directory discovery remains the responsibility of `workspace.tree` so its
existing sensitive-path filtering cannot be bypassed by a raw `ls`.

## Explicitly out of scope

P0.6D.1 does not provide:

- `sh`, `bash`, or `zsh`;
- Python/Node execution;
- Git;
- npm/pip/package managers;
- curl/wget/network access;
- redirection or pipes;
- arbitrary binaries;
- mutative terminal commands.

Those capabilities require later governed stages.

## Gateway visibility

The Gateway root and `/health` expose:

- `terminal_protocol: P0.6D.1`
- `terminal_execution: inspection_only`
- `terminal_shell: false`

`terminal.run` is advertised as executable only when the authorized workspace
is a directory. A single-file workspace remains valid for read operations but
does not grant terminal execution.
