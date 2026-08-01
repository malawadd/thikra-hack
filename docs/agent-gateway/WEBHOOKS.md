# Webhooks

Subscriptions select quote/order lifecycle events. Each payload has `id`, `type`, `created_at`, and order data. Requests include `Thikra-Event-Id`, `Thikra-Timestamp`, and `Thikra-Signature: v1=…`; the signature is HMAC-SHA-256 over timestamp plus canonical JSON.

The implementation enforces a replay window, HTTPS outside an explicit demo allowlist, credential/fragment rejection, hostname resolution, and private/loopback/link-local/reserved IP rejection. Delivery revalidates the target, does not follow redirects, records attempts, uses bounded exponential backoff, and disables repeatedly failing subscriptions without blocking fulfillment.

The installed Prava contract documents authenticated polling rather than signed webhooks, so `/api/v1/webhooks/prava` returns an explicit unsupported response instead of accepting unverifiable input.
