# External buyer-agent demo

Run `pnpm demo:agent`. A cross-platform Node orchestrator starts an isolated DEMO API on a free port, and the separate `apps/agent-client` application:

1. Connects with the official MCP TypeScript client and discovers 17 tools/six services.
2. Selects `verified-vertical-ad`, requests a deterministic quote below USD 10, accepts it, and creates an order through the local SDK.
3. Creates bounded authorization and explicitly confirms only the simulated demo payment via `--approve-demo`.
4. Starts the existing fulfillment pipeline, observes the controlled verification failure, requests one bounded retry, and receives verified delivery.
5. Retrieves deliverables, verifies the Ed25519 receipt remotely and locally, and saves the final asset under ignored `apps/agent-client/artifacts/`.

Use `pnpm --filter @thikra/agent-client dev -- --openai` with `OPENAI_API_KEY` to normalize creative intent through the official OpenAI Responses API. The model never receives payment credentials or permission to change the fixed budget.
