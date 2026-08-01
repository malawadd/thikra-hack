# REST Agent Gateway

Base: `/api/v1`. OpenAPI: `/openapi.json`. The service and quote schemas are generated from the implementation.

The public flow uses service listing/detail, quote create/read/accept, order create/read/events/SSE, payment authorization/status, paid-only fulfillment start, retry, deliverables, delivery receipt, acceptance, dispute, and refund-request endpoints. Developer applications, one-time API keys, signed webhook subscriptions, operator service versions/status, and operator economics are also exposed.

All commerce mutations that may be retried require `Idempotency-Key`. A stored fingerprint returns the original response; conflicting reuse returns `IDEMPOTENCY_CONFLICT`. Errors carry stable codes and `X-Request-Id` is returned by the assembled application.
