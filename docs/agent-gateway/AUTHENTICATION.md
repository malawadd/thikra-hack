# Authentication

Public service discovery and documentation require no credential. Persisted quotes, orders, payment authorization, private status, deliverables, disputes, developer applications, and webhooks require `Authorization: Bearer thikra_test_…` or `thikra_live_…`.

API keys are shown once and stored only as an HMAC-SHA-256 digest using `THIKRA_API_KEY_PEPPER`. Prefixes support lookup; the final comparison is constant-time. Keys have scopes, last-used time, optional expiry, and revocation. Declared buyer-agent metadata remains `DECLARED`; API-key identity is `AUTHENTICATED`; a supplied display name never becomes verified principal identity.

The public `/api/v1` and `/mcp` surfaces share a sliding-window limit keyed by a one-way digest of the bearer credential (or client address before authentication). Defaults are 120 gateway requests and 30 quote creations per 60 seconds. Responses include `X-RateLimit-Limit` and `X-RateLimit-Remaining`; rejected requests return `429` with `Retry-After`. Configure the limits with `THIKRA_RATE_LIMIT_REQUESTS`, `THIKRA_QUOTE_RATE_LIMIT_REQUESTS`, and `THIKRA_RATE_LIMIT_WINDOW_SECONDS`.

`orders:test` is a separate, explicit scope for local Sandbox test fulfillment.
It cannot bypass payment in DEMO or PRODUCTION, on a non-loopback API host, or
when the server-side test flag is disabled. It authorizes real provider work
without customer payment only for the configured low-value test cap.
