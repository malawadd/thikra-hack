# Tunnel origin validation execution plan

1. Reproduce the public tunnel mutation rejection and identify the proxy-origin mismatch.
2. Replace direct URL equality with exact configured-origin validation while retaining CSRF rejection for untrusted domains.
3. Configure the local tunnel origin for SvelteKit and the FastAPI Prava callback URL.
4. Validate unit tests, type checking, lint, build, structural rules, and a real create/revoke request through the public tunnel.

## Validation result

- Public tunnel health returned HTTP 200.
- A `$0.00` Prava sandbox session created through the tunnel with HTTP 201 and was revoked successfully.
- An unconfigured sibling origin remained blocked with HTTP 403.
- Four frontend unit tests, Svelte type checking, ESLint, production build, and all 11 structural checks passed.
