# Machine discovery

- `/.well-known/thikra-services.json`: platform, interfaces, catalog, payment/delivery modes, webhooks, signing keys.
- `/.well-known/agent-card.json`: A2A 1.0-shaped Agent Card for the HTTP+JSON interface and creative-commerce skill.
- `/.well-known/ucp`: UCP `2026-04-08` profile. Quoted asynchronous creative work is honestly represented by the namespaced `space.thikra.creative.quote` extension because core UCP does not model it directly.
- `/.well-known/thikra-signing-keys.json`: Ed25519 public keys.
- `/openapi.json`: live FastAPI schema.
- `/llms.txt`: developer navigation only; it grants no authority.

Thikra advertises REST and MCP. It does not claim an unimplemented A2A JSON-RPC task service.
