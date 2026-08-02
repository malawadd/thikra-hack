# Prava verification debug report

Prepared for Prava support on 2026-08-02. This report describes Thikra's current
sandbox integration and the failure that prevents the hosted flow from reaching
passkey verification. All keys, session tokens, one-time payment credentials,
and cardholder data are intentionally redacted.

## Support summary

The Prava sandbox API is reachable, our test and secret keys match the sandbox
environment, `POST /v1/sessions` succeeds with HTTP 201, and the returned Prava
card-collection surface loads. After card submission, the hosted flow displays:

> **Verification Unavailable**
>
> We couldn't reach the secure verification service. Please check your connection
> and try again.

The passkey prompt is never shown. The latest payment-result poll remains pending
because hosted verification has not completed.

### Retest update after the client corrections

After separating hosted and embedded sessions and correcting the merchant
identity, fresh session creation was retested directly against Prava. On this
retest the sandbox returned `500 INTERNAL_ERROR` for all of these request shapes:

- the old request shape without `integration_type`, using the old placeholder
  URL and `US`;
- explicit `embedding` with the old identity;
- explicit `embedding` with the public URL and `US`; and
- explicit `embedding` with the public URL and `SA`.

No probe returned a session, so no session link was left active. Because the old
control request now fails identically, this current 500 is not isolated to the
merchant URL, country, or integration-type corrections. Prava should inspect
the merchant's sandbox configuration and response traces before passkey testing
continues.

During reproduction, the browser console also showed this message from a script
served by `sandbox.collect.prava.space`:

```text
⚠️ [VISA] No ready session within 10000ms — retrying (1/2)
```

This places the observed failure after our backend has created the Prava session
and after Prava's iframe has mounted, but before Prava/Visa's secure verification
session becomes ready. We are not claiming that the upstream service is the only
possible cause. The integration corrections described below were applied after
the observation and the flow now needs to be reproduced with a fresh session.

## Environment

| Item | Current value |
| --- | --- |
| Application | Thikra |
| Frontend | SvelteKit 2, Svelte 5 |
| Backend | FastAPI / Python / `httpx` |
| Prava browser SDK | `@prava-sdk/core` `0.1.2` |
| Application mode | `SANDBOX` |
| Prava API | `https://sandbox.api.prava.space` |
| Prava collection origin | `https://sandbox.collect.prava.space` |
| Public frontend origin | `https://thikratest.mukaeb.com` |
| Merchant URL sent by the diagnostic | `https://thikratest.mukaeb.com` |
| Merchant country sent to Prava | `SA` |
| Test user email | Tester-controlled `@gmail.com` address from `PRAVA_TEST_USER_EMAIL` (redacted) |
| Public transport | HTTPS tunnel to the local SvelteKit server |
| Publishable key | Configured; prefix `pk_test_` (full value redacted) |
| Secret key | Configured server-side; prefix `sk_test_` (full value redacted) |
| Prava health at 2026-08-02 03:35:22 UTC | `200`, `status: ok` |
| Browser secure context | Available |
| Browser WebAuthn API | Available |
| Platform authenticator | Browser/device-dependent; not detected in the in-app test browser |

Prava's documentation says sandbox passkeys use real WebAuthn prompts and that
the merchant's frontend origin must be present in its allowed-domains list. See
[Authentication & Environments](https://docs.prava.space/authentication) and
[Testing in Sandbox](https://docs.prava.space/api-reference/testing).

## Integration architecture

```mermaid
sequenceDiagram
    participant B as Browser at thikratest.mukaeb.com
    participant W as SvelteKit same-origin BFF
    participant A as Thikra FastAPI
    participant P as sandbox.api.prava.space
    participant C as sandbox.collect.prava.space
    participant V as Hosted Visa verification

    B->>W: POST /api/thikra/prava-test/session?integration_type=embedding
    W->>A: POST /thikra/prava-test/session
    A->>P: POST /v1/sessions with sk_test Bearer token
    P-->>A: 201 session_id, session_token, iframe_url
    A-->>B: Session metadata and pk_test publishable key
    B->>C: PravaSDK.collectPAN mounts returned iframe_url
    C-->>B: PRAVA_READY / PRAVA_CHANGE events
    B->>A: Poll session every 3 seconds
    A->>P: GET /v1/sessions/{id}/payment-result
    C->>V: Start secure card verification
    V--xC: Observed: verification service does not become ready
    C-->>B: Verification Unavailable; no passkey prompt
```

The browser never receives `PRAVA_SECRET_KEY`. Raw PAN, CVV, and expiry are
entered only in Prava's cross-origin collection surface.

## Backend session creation

The shared gateway is implemented in
`services/api/app/thikra/payments.py`. It sends the secret key as a Bearer token
and creates a single purchase context:

```python
amount = f"{request['maximum_amount_minor'] / 100:.2f}"
body = {
    "user_id": request["user_id"],
    "user_email": request["user_email"],
            "total_amount": amount,
            "currency": request["currency"],
            "integration_type": request.get("integration_type", "embedding"),
    "external_order_ref": request["idempotency_key"],
    "description": f"Scoped creative procurement for mandate {request['mandate_id']}",
    "purchase_context": [{
        "merchant_details": {
            "name": request["merchant"],
            "url": request["merchant_url"],
            "country_code_iso2": settings.thikra_merchant_country_code.upper(),
            "category_code": "7399",
            "category": "Business services",
        },
        "product_details": [{
            "description": "Bounded generative-media provider purchase",
            "unit_price": amount,
            "quantity": 1,
        }],
        "effective_until_minutes": 15,
    }],
}

callback_url = f"{settings.public_web_url}/payments"
if callback_url.startswith("https://"):
    body["callback_url"] = callback_url

response = await client.post(
    f"{settings.prava_backend_url}/v1/sessions",
    headers={
        "Authorization": f"Bearer {settings.prava_secret_key}",
        "Content-Type": "application/json",
    },
    json=body,
)
```

For the isolated diagnostic, FastAPI calls that gateway with this logical input:

```json
{
  "mandate_id": "prava-zero-dollar-diagnostic",
  "merchant": "Thikra Prava sandbox verification",
  "merchant_url": "https://thikratest.mukaeb.com",
  "integration_type": "embedding",
  "maximum_amount_minor": 0,
  "estimated_amount_minor": 0,
  "retry_reserve_minor": 0,
  "verification_only": true,
  "currency": "USD",
  "user_id": "thikra-prava-test-user",
  "user_email": "<tester-controlled address>@gmail.com",
  "idempotency_key": "prava-zero-test-<random UUID>"
}
```

The resulting Prava request has `total_amount: "0.00"` and
`unit_price: "0.00"`. The `callback_url` is
`https://thikratest.mukaeb.com/payments`. The response fields exposed to the
frontend are limited to:

```json
{
  "session_id": "<redacted>",
  "session_token": "<redacted; short-lived>",
  "iframe_url": "https://sandbox.collect.prava.space/<redacted>",
  "order_id": "<redacted>",
  "expires_at": "<redacted>",
  "publishable_key": "pk_test_<redacted>",
  "integration_type": "embedding",
  "amount": "0.00",
  "currency": "USD"
}
```

## Browser SDK mounting

The Svelte component dynamically imports the official package, creates one SDK
instance, and uses the session values returned by FastAPI:

```ts
const { PravaSDK } = await import('@prava-sdk/core');
sdk = new PravaSDK({ publishableKey });

await sdk.collectPAN({
  sessionToken: session.session_token,
  iframeUrl: session.iframe_url,
  container,
  onReady: markReady,
  onChange: (state) => {
    validation = state;
    onchange?.(state);
  },
  onSuccess: (result) => onsuccess?.(result),
  onError: (cause) => {
    error = errorMessage(cause);
    loading = false;
    onerror?.(error);
  }
});
```

The component destroys the SDK before remounting and again when Svelte unmounts
the component, so concurrent SDK instances do not share a session.

Inspection of installed package `0.1.2` confirms that it creates an iframe with:

```text
sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
allow="payment; publickey-credentials-get; publickey-credentials-create"
```

The package adds `origin=window.location.origin` to the returned `iframe_url`,
restricts its `postMessage` bridge to the returned iframe origin, and listens for
`PRAVA_READY`, `PRAVA_CHANGE`, `PRAVA_SUCCESS`, `PRAVA_ERROR`, resize, redirect,
transaction-complete, and dismissed events. It has no separate event confirming
that a passkey prompt appeared.

## Polling and credential handling

The diagnostic begins polling immediately after session creation and repeats
every three seconds. The browser calls its same-origin BFF; only FastAPI calls
Prava:

```python
response = await client.get(
    f"{settings.prava_backend_url}/v1/sessions/{session_id}/payment-result",
    headers={"Authorization": f"Bearer {settings.prava_secret_key}"},
    params={"_t": current_time_in_milliseconds},
)
```

Prava one-time fields (`token`, `dynamic_cvv`, `expiry_month`, and
`expiry_year`) are stripped before any result is persisted or returned to the
browser. The zero-dollar diagnostic reports only whether a credential was
generated; it never displays the credential. The regular payment path keeps
credentials in process memory only until outcome reporting.

Prava's current sandbox guide says a successful checkout first reaches
`awaiting_result`, after which the merchant reports `APPROVED` or `DECLINED`,
and only then does the final state become `completed` or `failed`. Our failure
occurs before `awaiting_result`, so merchant outcome reporting is not yet the
blocking stage.

## Same-origin BFF and tunnel handling

The frontend sends only same-origin `/api/...` requests. SvelteKit proxies them
to FastAPI at `http://127.0.0.1:43192`. State-changing browser requests are
accepted only when their `Origin` exactly matches the request origin or a
configured trusted origin. The tunnel origin is configured as:

```env
CSRF_TRUSTED_ORIGINS=https://thikratest.mukaeb.com
```

This fixed the earlier Thikra-side error `Cross-origin state changes are not
accepted.` It is separate from Prava's allowed-domain validation and separate
from the current hosted `Verification Unavailable` error.

## Embedded and hosted session isolation

Prava session links are single-use. Once `collectPAN()` mounts a returned URL in
the page, opening that same URL in a second tab returns **Session Already Used**.
The diagnostic therefore no longer shares one link between the two surfaces:

- **Start embedded $0 test** requests `integration_type=embedding`, mounts that
  session once, and polls its `session_id`.
- **Open hosted $0 test** or **Open fresh hosted flow** revokes the current
  diagnostic session, requests `integration_type=full_checkout`, opens the fresh
  URL without mounting it, and polls the new `session_id`.

This ensures the iframe and hosted flow never compete for the same session.

## Observed stage matrix

| Stage | Result |
| --- | --- |
| Public HTTPS page | Works |
| Thikra same-origin mutation validation | Works |
| Thikra FastAPI diagnostic | Ready, no configuration issues |
| `GET https://sandbox.api.prava.space/health` | Works (`status: ok`) |
| Sandbox key-prefix pairing | Works (`pk_test_` + `sk_test_`) |
| `POST /v1/sessions` | Historically HTTP 201; current retest returns Prava `500 INTERNAL_ERROR` for old and corrected request shapes |
| Session token and iframe URL returned | Works |
| Prava SDK iframe mount | Works |
| Hosted address/card form | Visible |
| Prava/Visa secure verification readiness | Fails / times out |
| OTP stage | Not reached in the failing attempt |
| Passkey creation or verification | Not shown |
| Payment result | Remains pending; no credential generated |

## Items requiring confirmation

### 1. Is the public origin allowlisted for this merchant?

Please confirm that the merchant account behind the redacted `pk_test_` and
`sk_test_` pair has this exact allowed domain:

```text
https://thikratest.mukaeb.com
```

Prava's documentation says both the SDK iframe and session middleware validate
the originating domain. We cannot verify the dashboard allowlist from the
application source. This is the highest-priority configuration check.

### 2. Corrected: explicit integration type

Thikra now sends `integration_type: "embedding"` for SDK-mounted sessions and
`integration_type: "full_checkout"` for newly created hosted sessions. The
hosted button never reuses the active iframe session.

### 3. Corrected: merchant URL and country

The diagnostic now sends `https://thikratest.mukaeb.com` as the merchant URL
and `SA` as `country_code_iso2`. An explicitly configured
`THIKRA_MERCHANT_URL` still takes precedence for non-diagnostic deployments.

### 4. Is a standard `$0.00` sandbox checkout supported?

The diagnostic intentionally sends a standard checkout with a zero total. It
does not send a `mandate_setup` block. Please confirm whether this is a
supported way to test initial card enrollment and passkey verification, or
whether the test should use:

- a non-zero sandbox amount;
- a mandate-setup session with `authorizeOnly: true`; or
- another Prava-recommended verification-only request shape.

### 5. Is the Visa verification sandbox provisioned and healthy for this account?

Please investigate the hosted warning `No ready session within 10000ms` and the
user-facing `Verification Unavailable` error. In particular, please confirm:

- Visa/FIDO sandbox provisioning for this merchant/key pair;
- whether Cloudflare-style HTTPS tunnel origins are supported;
- whether any callback domain, merchant URL, or relying-party domain must be
  separately allowlisted;
- whether the stable test user or previously attempted card/device binding is
  in a state that must be reset; and
- whether the account has reached a sandbox attempt limit.

## Requested trace data from Prava

Please tell us which identifiers are safe and useful to provide privately. On
the next reproduction we can send the exact `session_id`, `order_id`, UTC
timestamp, browser/version, and the `X-Response-ID` returned by Prava. The
current client does not retain `X-Response-ID`, although Prava's documentation
specifically recommends including it in support requests. We will not send the
secret key, session token, raw card data, or issued one-time credentials.

## Reproduction procedure

1. Open `https://thikratest.mukaeb.com/payments/prava-test` in a current Chrome,
   Safari, Firefox, or Edge browser with WebAuthn enabled.
2. Confirm the page reports server configuration ready, sandbox health `ok`, a
   secure context, and WebAuthn support.
3. Select **Start embedded $0 test** or **Open hosted $0 test**. Each path
   creates a session with the matching Prava integration type; old sessions are
   revoked when switching paths.
4. Complete the hosted shipping fields and use one of Prava's documented Visa
   sandbox cards.
5. Accept the terms and select **Pay Now**.
6. If prompted for sandbox OTP, enter `456789`.
7. Expected: Prava displays the real WebAuthn/passkey prompt and the payment
   result advances toward `awaiting_result`.
8. Actual: the hosted surface reports **Verification Unavailable** and no
   passkey prompt appears.
9. Repeat once using **Open fresh hosted flow**. This creates and polls a new
   `full_checkout` session because the iframe session cannot be reused.

The sandbox card values and OTP above come from Prava's official
[Test Cards](https://docs.prava.space/api-reference/test-cards) reference.

## Relevant source files

- `services/api/app/thikra/payments.py` — authenticated session creation,
  polling, revocation, and credential sanitization.
- `services/api/app/thikra/api.py` — isolated zero-dollar diagnostic endpoints.
- `apps/web/src/lib/components/PravaCardForm.svelte` — SDK lifecycle and event
  handlers.
- `apps/web/src/routes/payments/prava-test/+page.svelte` — diagnostic UI,
  WebAuthn checks, polling, and redacted observations.
- `apps/web/src/routes/api/[...path]/+server.ts` — same-origin BFF.
- `apps/web/src/lib/server/origin.ts` — exact-origin mutation validation.
- `apps/web/package.json` — pinned SDK version.

## Official Prava references

- [Authentication, key separation, environments, and domain allowlisting](https://docs.prava.space/authentication)
- [Create Session request and response](https://docs.prava.space/api-reference/create-session)
- [Sandbox behavior and full test flow](https://docs.prava.space/api-reference/testing)
- [Sandbox cards and OTP](https://docs.prava.space/api-reference/test-cards)
- [Prava integration quickstart](https://docs.prava.space/quickstart)
