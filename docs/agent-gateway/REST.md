# REST Agent Gateway

Base: `/api/v1`. OpenAPI: `/openapi.json`. The service and quote schemas are generated from the implementation.

The public flow uses service listing/detail, quote create/read/accept, order create/read/events/SSE, payment authorization/status, paid-only fulfillment start, retry, deliverables, delivery receipt, acceptance, dispute, and refund-request endpoints. Developer applications, one-time API keys, signed webhook subscriptions, operator service versions/status, and operator economics are also exposed.

For local live-pipeline testing only, `POST /orders/{id}/test-fulfillment`
requires `orders:test` plus the normal order scope and an `Idempotency-Key`.
It is disabled by default and is accepted only on a loopback `SANDBOX` API with
an accepted quote at or below the configured test cap. It never contacts Prava,
records zero customer payment, and labels the resulting receipt
`TEST_BYPASSED_NO_CUSTOMER_PAYMENT`; configured media providers may still bill.

All commerce mutations that may be retried require `Idempotency-Key`. A stored fingerprint returns the original response; conflicting reuse returns `IDEMPOTENCY_CONFLICT`. Errors carry stable codes and `X-Request-Id` is returned by the assembled application.
