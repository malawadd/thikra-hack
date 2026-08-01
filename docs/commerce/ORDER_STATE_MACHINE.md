# Commercial order state machine

The authoritative transition map is `app/commerce/state_machine.py`. The main path is:

`QUOTED → PAYMENT_AUTHORIZATION_PENDING → PAYMENT_AUTHORIZED → PAYMENT_PENDING → PAID → ACCEPTED → FULFILLMENT_PENDING → FULFILLING → VERIFYING → READY → DELIVERED → COMPLETED`.

Explicit branches cover quote expiry, cancellation, failure, retry, review, dispute, redress, refund request, and refund completion. Invalid transitions raise a conflict. Every transition appends both an order-local SHA-256 hash-chain event and the existing workspace audit-chain event. A quote is not an order; authorization is not payment; generation is not delivery; delivery is not buyer acceptance; a refund request is not a refund.
