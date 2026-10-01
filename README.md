# MAESTRO Local Gateway

Local-only bridge between MAESTRO UI and MAESTRO Core.

```text
maestro-ui / maestro.men
          |
          v
MAESTRO Local Gateway
          |
          v
MAESTRO Core
          |
          v
Sovereign Intelligence
          |
          v
Local providers
```

## Security baseline

- binds to `127.0.0.1` by default;
- rejects `0.0.0.0`;
- no wildcard CORS;
- explicit browser-origin allowlist;
- no cloud fallback;
- no direct browser-to-Ollama mission path;
- MAESTRO Core remains the authority boundary.

## Development install

Expected sibling layout:

```text
Github/
├── maestro/
├── maestro-ui/
└── maestro-gateway/
```

```bash
/usr/local/bin/python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip setuptools wheel
./.venv/bin/python -m pip install -e .
./.venv/bin/python -m pip install -e ../maestro
```

Run:

```bash
./.venv/bin/python -m maestro_gateway
```

Default address:

```text
http://127.0.0.1:8787
```

P0.3 exposes only `/health`, `/providers`, and `/models`.
Mission execution is intentionally disabled until the governed mission transport is implemented.
