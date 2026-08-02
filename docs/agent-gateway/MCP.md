# MCP Agent Gateway

Thikra mounts authenticated MCP Streamable HTTP at `/mcp/` using the maintained Python MCP server. The parent FastAPI lifespan starts and stops the MCP session manager.

The 20 tools are: `thikra_list_services`, `thikra_get_service`, `thikra_request_quote`, `thikra_get_quote`, `thikra_accept_quote`, `thikra_create_order`, `thikra_get_order`, `thikra_create_payment_authorization`, `thikra_refresh_payment_authorization`, `thikra_get_payment_status`, `thikra_wait_for_payment`, `thikra_start_order`, `thikra_start_test_fulfillment`, `thikra_get_order_status`, `thikra_get_order_events`, `thikra_get_deliverables`, `thikra_get_delivery_receipt`, `thikra_request_retry`, `thikra_open_dispute`, and `thikra_get_dispute`.

MCP imports only the shared Agent Gateway facade. Transport authentication uses the same scoped API keys as REST. Payment credentials, B2 credentials, private reasoning, and permanent asset URLs are never tool output. `tests/test_mcp.py` negotiates and calls the server through the official MCP client over Streamable HTTP.

## Prava Sandbox checkout from an agent

For a paid order, an agent creates the quote and order, calls
`thikra_create_payment_authorization`, presents the returned `checkout_url` as
a clickable link **and** a QR code encoding that exact URL, and then calls
`thikra_wait_for_payment`. The
checkout is single-use: open the desktop link **or** scan the same URL on a
phone, never both. The phone flow can use its passkey.

If the link was opened, expired, or needs to move to another device, call
`thikra_refresh_payment_authorization`. It revokes the abandoned session before
returning exactly one fresh hosted URL, which an agent can show as both the
clickable link and QR code.

The wait tool polls every three seconds and returns sanitized status only. With
the intentional Sandbox test-settlement option enabled, a completed checkout
returns `SANDBOX_SETTLED_NO_REAL_FUNDS` and `next_action:
START_FULFILLMENT`. The agent must call `thikra_start_order` immediately; this
authorizes provider work for the test while recording zero customer funds
collected. In production, completion instead returns
`MERCHANT_CHARGE_REQUIRED`, and fulfillment remains blocked until an exact,
documented merchant charge is recorded and reported to Prava.

## Use locally from Codex Desktop

Start the local API with `pnpm dev`, issue a key from **Developer Applications**,
and opt into the `orders:test` scope only when you intend to test live generation
without Prava. Then run:

```text
pnpm codex:connect
```

The command prompts for the key with hidden input, saves it as the Windows User
environment variable `THIKRA_API_KEY`, and creates or updates only Thikra's MCP
entry in `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.thikra]
url = "http://localhost:43192/mcp/"
bearer_token_env_var = "THIKRA_API_KEY"
default_tools_approval_mode = "writes"
```

Restart Codex, use `/mcp` to confirm the server is connected, then ask:

> Use Thikra to create a verified 15-second Arabic vertical ad under USD 5.
> Use local Sandbox test fulfillment; do not use Prava. Ask before starting
> the generation.

`thikra_start_test_fulfillment` is intentionally unavailable unless all of the
following are true: the API is local loopback, `APP_MODE=SANDBOX`,
`THIKRA_AGENT_TEST_FULFILLMENT_ENABLED=true`, the caller has `orders:test`, and
the accepted quote is at or below `THIKRA_AGENT_TEST_MAX_QUOTE_MINOR` (USD 5 by
default). It starts real configured provider work and creates real assets,
verification, and a signed receipt, but the receipt is labelled
`TEST_BYPASSED_NO_CUSTOMER_PAYMENT` and records zero customer payment.
After verification finds no failed checks, this local test flow auto-completes
delivery; paid orders continue through their normal human-review path.

Run `pnpm codex:update` to replace an expired or rotated API key. Run
`pnpm codex:disconnect` to remove Thikra's MCP entry and its User environment
variable; then restart Codex Desktop.
