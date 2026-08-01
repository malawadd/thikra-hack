# Backblaze delivery

Generated assets remain behind the existing storage abstraction. Delivery adds evidence manifests and verification reports through `evidence_storage`; no direct AWS client is introduced. Sandbox/production use configured Backblaze B2, while demo fixtures remain visibly simulated.

Deliverables store B2 object keys and hashes, never public permanent URLs. Authenticated retrieval produces five-minute download URLs; B2-backed content is presigned for a shorter hop. A storage failure prevents delivery completion and leaves the order available for retry/redress.
