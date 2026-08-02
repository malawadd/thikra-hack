# Prava zero-dollar diagnostics execution plan

1. Apply pending Alembic migrations before local API startup so existing payment and run records remain readable after schema changes.
2. Add sandbox-only Prava diagnostic endpoints that create one zero-dollar session, poll its result, and never return or persist one-time payment credentials.
3. Add a focused Svelte page for the zero-dollar flow with iframe lifecycle, validation, polling, WebAuthn readiness, sandbox OTP guidance, and a hosted-page fallback.
4. Document the diagnostic workflow and validate migrations, backend tests, frontend type checking, structural tests, and the running routes.

## Validation result

- Existing SQLite data migrated from `20260801_0001` to `20260801_0002` without deletion.
- Payments, Runs, diagnostics, and all three corresponding pages return HTTP 200.
- A real sandbox `$0.00` session was created, polled as pending before user interaction, and revoked successfully.
- The official iframe mounted with payment and WebAuthn permissions and no application console errors.
- Backend: 114 passed, 4 skipped. Frontend unit test: 1 passed. Typecheck, lint, build, and structural checks passed.
