# P0.4A — Governed M0_DIRECT Mission Transport

P0.4A enables the first real Gateway mission transport.

```text
UI
  -> POST /session
  -> session token
  -> POST /mission
  -> EX lifecycle
  -> AI local inference
  -> evidence
  -> AY delivery-contract verification
  -> TX durable commit
  -> response
```

## Verification scope

P0.4A intentionally distinguishes execution/delivery integrity from semantic
or factual truth.

AY may issue `JUSTE` in P0.4A only for this bounded contract:

- one bounded local inference;
- successful provider execution;
- non-empty output;
- resource identity recorded;
- evidence recorded;
- TX mission trace committed.

P0.4A does not claim that every free-form model answer is factually true.

The API therefore returns:

```json
{
  "verification_scope": "execution_delivery_integrity_only",
  "semantic_truth_verified": false
}
```

## Session

`POST /session` returns an in-memory session token.

`POST /mission` requires:

```text
X-Maestro-Session: <token>
```

The token dies when the Gateway process stops.

## Mission request

```json
{
  "prompt": "Your request",
  "model": "qwen3:8b",
  "max_tokens": 512
}
```

`model` is optional.

## Security

P0.4A preserves:

- loopback-only listener;
- explicit CORS origins;
- no wildcard CORS;
- bounded request body;
- bounded prompt length;
- bounded generated-token request;
- serialized governed mission execution;
- no direct browser-to-Ollama official mission path.\n