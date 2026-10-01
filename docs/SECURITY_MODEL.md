# MAESTRO Local Gateway P0.3 Security Model

## Network boundary

Default listener:

```text
127.0.0.1:8787
```

P0.3 refuses `0.0.0.0` and other non-loopback host values.

## Browser origins

Default allowed origins:

```text
http://localhost:5173
http://127.0.0.1:5173
https://maestro.men
```

Wildcard CORS is rejected.

## Authority boundary

The official path remains:

```text
Browser
  -> Local Gateway
  -> MAESTRO Core
  -> Sovereign Intelligence
  -> Provider Adapter
  -> Inference Runtime
```

There is deliberately no mission execution endpoint in P0.3.
