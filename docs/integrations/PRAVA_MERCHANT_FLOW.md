# Prava customer-to-Thikra flow

The commercial direction is buyer → Thikra. Thikra creates a bounded Prava authorization session with Thikra as merchant and returns only iframe/public approval metadata. User approval changes authorization state, not payment state.

In `SANDBOX`/`PRODUCTION`, Thikra polls Prava's documented payment-result endpoint and keeps any one-time payment credential in process memory only. The installed Prava documentation does not expose a merchant acquiring/charge API or refund API. Therefore authorization completion moves to `MERCHANT_CHARGE_REQUIRED`; payment becomes `PAID` only after a supported merchant rail reports an exact quoted charge and Thikra reports the outcome to Prava. Refund requests open redress and remain `REQUESTED_UNSUPPORTED` rather than being called refunded.

In `DEMO`, explicit confirmation creates a visibly `SIMULATED_PAID` record. No demo transaction is described as real. No raw card data, secret key, session credential, or one-time credential is persisted or logged.

## Focused sandbox diagnostic

With `APP_MODE=SANDBOX` and matching `pk_test_` / `sk_test_` credentials, open
`/payments/prava-test`. The page creates one isolated `$0.00` session with a stable test user,
mounts the official Prava iframe, polls the same `session_id` every three seconds, and provides a
hosted-page fallback. It cannot authorize provider spend or launch a generation run.

The page checks the sandbox URL/key pairing, Prava health, browser secure-context status,
WebAuthn availability, platform-authenticator availability, iframe readiness, card validation,
SDK success/error events, and the sanitized payment result. On a browser/device that has not been
bound before, complete the hosted address/card fields, accept the terms, submit Pay Now, and use
sandbox OTP `456789`; the hosted Prava flow then owns passkey creation or verification. The
passkey prompt is not the initial iframe screen. `@prava-sdk/core` exposes no separate event proving that the passkey prompt was
displayed, so Thikra reports only events it can actually observe.

One-time network tokens and dynamic CVVs are removed by FastAPI before the diagnostic result is
returned. The diagnostic does not persist them or show them in the browser.

Local API startup runs `alembic upgrade head` before Uvicorn. This preserves existing payment and
run records while applying additive schema changes; `Base.metadata.create_all()` alone does not
migrate an existing SQLite database.

## HTTPS tunnels and mutation origins

When SvelteKit is reached through an HTTPS tunnel, its internal request URL may still use the local
host while the browser correctly sends the public HTTPS `Origin`. Configure the public URL in the
API root `.env` and trust the exact public origin in `apps/web/.env`:

```env
# .env — used by FastAPI for Prava callback URLs
PUBLIC_WEB_URL=https://your-tunnel.example

# apps/web/.env — server-only SvelteKit BFF configuration
CSRF_TRUSTED_ORIGINS=https://your-tunnel.example
```

Multiple public origins may be comma-separated. Only exact origins are accepted; wildcard domains,
paths, arbitrary forwarded headers, and sibling subdomains do not bypass the CSRF check. Restart the
development stack after changing either file.
