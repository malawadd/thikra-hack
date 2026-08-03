# Thikra Studio

Thikra Studio is the local, Windows-first creative workflow surface. It combines a ComfyUI-inspired typed node canvas with reversible agent proposals, explicit variant selection, reference-led look development, cost confirmation, and incremental Genblaze execution.

## Milestone scope

- Tauri 2 shell with a statically rendered Svelte 5/SvelteKit application.
- Local single-user SQLite projects; no sign-in, collaboration, or cloud project sync.
- Loopback-only FastAPI integration. Python and ffmpeg remain separate prerequisites.
- Generated production assets and Genblaze manifests remain in configured B2 storage. DEMO renders are clearly fixture-backed.
- Personal provider overrides are stored through the operating-system credential store and take precedence over environment configuration; plaintext is never stored in SQLite or returned by the API.
- Generation-node provider dropdowns show only vendors currently connected by an environment or personal credential. Their dependent model dropdowns are populated from that provider's curated catalog entry and refresh immediately when credentials change.

## Graph and revision contract

The semantic document is a versioned `{schema_version, nodes, edges}` DAG. Ports are typed as text, image, variant set, video, audio, or final media. The validator rejects cycles, unknown nodes/ports, incompatible edges, invalid variant counts, and unsupported provider choices before a run starts. Note and Group are intentionally non-executing.

Every semantic save creates an immutable `WorkflowRevision`. Restoring older content therefore creates another revision. Layout and viewport updates use a separate endpoint and do not affect semantic hashes or cache keys. Executions retain the exact revision ID they ran.

## Agent control model

The Look Director and workflow agent call the configured OpenAI chat integration with schema-constrained output. Requests may include four selected reference or generated images plus explicit normalized point/rectangle annotations. References inform a structured textual look description; image providers are not advertised as directly conditioned unless their catalog capability says so.

The response is a proposal made of `add_node`, `update_node`, `remove_node`, `connect`, and `disconnect` operations. The desktop previews and selectively applies these operations with dependency closure. The agent never writes or runs the graph. A stale base revision returns `409`.

## Execution and safety

The API estimates variant-multiplied costs, compares them with the locally confirmed project cap, and requires the exact fresh estimate hash on start. Estimated usage is charged per paid node attempt, so a later branch failure does not hide work the provider may have billed. Nodes persist queued, running, succeeded, failed, blocked, cancelled, or cached state. Independent branches continue after failures; downstream nodes receive an actionable blocked reason. Force rerun bypasses deterministic successful-output caching.

Failed and cancelled executions expose a two-step resume flow: first review a fresh estimate for only unresolved nodes, then explicitly confirm it. Resume creates a new execution linked through `resumed_from_execution_id` to the immutable failed run. Successful nodes are sourced from that exact parent execution. Provider results are checkpointed as durable asset descriptors before local asset persistence; if the API or SQLite write fails after generation, resume materializes that checkpoint as a cached node instead of buying the generation again.

The latest generated image variants are loaded from persisted project assets whenever a project opens, shown as thumbnails on the image node, and remain available through **Generated looks**. **Pin & prepare animation** saves the selector choice as a semantic revision, applies the selected video provider's valid duration grid, and estimates a targeted run beginning at the video node so image generation is not purchased again.

Long-running nodes emit a live heartbeat with elapsed time every five seconds. Provider step events add per-variant progress, while the initial node event identifies the selected provider, model, variant count, and timeout. Development reload watches only `services/api/app`, preventing SQLite and WAL writes from restarting active executions.

Imported PNG/JPEG/WebP, WAV/MP3, and MP4/WebM files are MIME and size checked, hashed, and copied below the configured Studio data directory. Paths are resolved before access. An import is uploaded with `genblaze-s3` only when a remote provider needs an external URL. Tauri exposes no shell or unrestricted filesystem capability.

## Commands

```powershell
pnpm setup:desktop
pnpm dev:desktop
pnpm build:desktop:renderer
pnpm build:desktop
pnpm test:desktop
pnpm test:desktop:e2e:windows
```

The Windows WebDriver smoke command requires `tauri-driver` and the matching Microsoft Edge WebDriver on `PATH`. Production signing, auto-update, a bundled Python/ffmpeg runtime, magic-link account connection, and Prava project authorization are later milestones.
