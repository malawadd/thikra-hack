# Prava integration

Thikra follows the installed official `prava-sdk-integration` skill v1.1.0 and pins `@prava-sdk/core` 0.1.2.

FastAPI creates `POST /v1/sessions` with `PRAVA_SECRET_KEY`. Svelte receives only `session_token`, `iframe_url`, and the publishable key and mounts the SDK iframe. FastAPI polls `GET /v1/sessions/{id}/payment-result`; token, dynamic CVV, and expiry fields in transaction line items stay in process memory. It reports the documented merchant result via `POST /v1/sessions/{id}/report-status` as `APPROVED` or `DECLINED`, then removes ephemeral credentials.

Every Prava session is single-use. The authorization page therefore exposes a
fresh-session action both beside the iframe and inside SDK error recovery. It
revokes the prior session when possible, creates a new server-side session with
a unique idempotency key, and force-remounts the iframe with the replacement
token on the same page. Reloading a consumed token remains available only as a
separate action for transient rendering failures.

SANDBOX also exposes an explicit `$0.00` verification-only session. It exercises
the real secure iframe and card enrollment/tokenization path without granting a
spend authorization. A completed zero-value session enters `VERIFIED`, its
ephemeral credential is discarded, and the run-launch gate continues to require
a distinct positive `AUTHORIZED` payment. Zero-value sessions are rejected
outside `SANDBOX`.

DEMO uses `DemoPaymentGateway`, visibly labels every action simulated, and never claims settlement.

## Honest capability boundary

The official skill does not specify webhook events, signature headers/algorithm, refunds, or a card-checkout contract for Genblaze AI providers. The webhook route returns `501 PRAVA_WEBHOOK_CONTRACT_UNDOCUMENTED`; reconciliation uses the authenticated result endpoint. Unsupported refunds become redress requests, never completed-refund claims. Genblaze API billing and Prava authorization remain separate until a merchant checkout contract exists.

Sandbox URL: `https://sandbox.api.prava.space`. Documented OTP: `456789`. Secrets never enter browser bundles, logs, database rows, or evidence exports.
