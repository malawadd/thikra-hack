# Delivery receipts

Thikra issues one receipt only after verification permits a delivery package. The canonical JSON payload links buyer principal/agent, service version, quote, order, customer payment, mandate, generation run, deliverable hashes, verification report hash, and issuance time.

The server computes SHA-256 and signs canonical JSON with Ed25519. Public OKP keys are at `/.well-known/thikra-signing-keys.json`; verification is available at `POST /api/v1/delivery-receipts/verify`. Demo uses a documented deterministic development key. Production refuses that key and requires `THIKRA_RECEIPT_SIGNING_PRIVATE_KEY`.

The external agent verifies both the receipt hash and signature before saving media.
