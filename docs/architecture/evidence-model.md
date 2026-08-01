# Evidence model

Every material action is an `AuditEvent` with actor, run, payload, related IDs, previous hash, and current hash. Hashes use `SHA256(canonical_json(event_without_hash) + previous_hash)`. Canonical JSON sorts keys and UTC normalization survives database reload.

Exports join mandate versions, provider decision/scores, sanitized payment events, asset hashes/lineage, evaluations, chronological audit chain, and cases/notes. They exclude authorization headers, credentials, secrets, and private model reasoning.

The UI offers a chronological table and accessible SVG graph from backend IDs. Export writes the JSON through the central evidence adapter before download.
