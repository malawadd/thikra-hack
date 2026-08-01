# Prava customer-to-Thikra flow

The commercial direction is buyer → Thikra. Thikra creates a bounded Prava authorization session with Thikra as merchant and returns only iframe/public approval metadata. User approval changes authorization state, not payment state.

In `SANDBOX`/`PRODUCTION`, Thikra polls Prava's documented payment-result endpoint and keeps any one-time payment credential in process memory only. The installed Prava documentation does not expose a merchant acquiring/charge API or refund API. Therefore authorization completion moves to `MERCHANT_CHARGE_REQUIRED`; payment becomes `PAID` only after a supported merchant rail reports an exact quoted charge and Thikra reports the outcome to Prava. Refund requests open redress and remain `REQUESTED_UNSUPPORTED` rather than being called refunded.

In `DEMO`, explicit confirmation creates a visibly `SIMULATED_PAID` record. No demo transaction is described as real. No raw card data, secret key, session credential, or one-time credential is persisted or logged.
