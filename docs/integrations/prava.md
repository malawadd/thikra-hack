# Prava integration

Thikra follows the installed official `prava-sdk-integration` skill v1.1.0 and pins `@prava-sdk/core` 0.1.2.

FastAPI creates `POST /v1/sessions` with `PRAVA_SECRET_KEY`. Svelte receives only `session_token`, `iframe_url`, and the publishable key and mounts the SDK iframe. FastAPI polls `GET /v1/sessions/{id}/payment-result`; token, dynamic CVV, and expiry fields in transaction line items stay in process memory. It reports the documented merchant result via `POST /v1/sessions/{id}/report-status` as `APPROVED` or `DECLINED`, then removes ephemeral credentials.

DEMO uses `DemoPaymentGateway`, visibly labels every action simulated, and never claims settlement.

## Honest capability boundary

The official skill does not specify webhook events, signature headers/algorithm, refunds, or a card-checkout contract for Genblaze AI providers. The webhook route returns `501 PRAVA_WEBHOOK_CONTRACT_UNDOCUMENTED`; reconciliation uses the authenticated result endpoint. Unsupported refunds become redress requests, never completed-refund claims. Genblaze API billing and Prava authorization remain separate until a merchant checkout contract exists.

Sandbox URL: `https://sandbox.api.prava.space`. Documented OTP: `456789`. Secrets never enter browser bundles, logs, database rows, or evidence exports.
