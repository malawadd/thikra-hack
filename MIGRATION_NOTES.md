# Migration notes

The original `apps/web` Next.js 16 / React 19 application moved with Git history to `reference/next-web`. It is excluded from the active pnpm workspace and all root commands.

The replacement is a native SvelteKit application using Svelte 5 runes, TypeScript, Tailwind CSS 4, lucide-svelte, and adapter-node. Useful behaviors were ported—not wrapped—including provider visibility, progressive scenes, SSE, previews, errors, and readiness information. Browser API calls now use a streaming same-origin SvelteKit BFF. FastAPI stayed in place and its Genblaze catalog/pipelines/composer gained a separate Thikra business domain.

New work includes mandate compilation/versioning, provider scoring, Prava/payment state separation, SQLAlchemy/Alembic persistence, generation policy, deterministic verification, evidence metadata, audit hashes, evidence graph, redress cases, demo fixtures, containers, tests, and documentation.

An ignored `.next` cache can remain in an old checkout, but it is neither source nor a workspace/build input.

The external-commerce migration (`20260801_0002`) adds versioned services, principals, agents, developer applications, hashed API keys, quotes, commercial orders/events, fulfillment jobs, deliverables, Ed25519 receipts, webhooks, idempotency records, and disputes. It extends `payment_records` with a nullable commercial-order link, direction, and paid amount while preserving existing mandate-linked procurement records. Existing URLs, generation models, provider catalog, composer, storage adapter, and internal Studio workflow remain in place.
