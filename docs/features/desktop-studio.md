# Thikra Studio

Thikra Studio is the local, Windows-first creative workflow surface. **Generate** combines a ComfyUI-inspired typed node canvas with reversible agent proposals, explicit variant selection, reference-led look development, cost confirmation, and incremental Genblaze execution. **Edit** is a non-destructive multi-track video editor using the same project asset library.

## Milestone scope

- Tauri 2 shell with a statically rendered Svelte 5/SvelteKit application.
- Local single-user SQLite projects; no sign-in, collaboration, or cloud project sync.
- Loopback-only FastAPI integration. Source development uses separately installed Python and ffmpeg; Windows v0.1.1 packages both runtimes.
- Provider results are ingested into hashed local project storage immediately. Backblaze B2 is optional for cloud copies and required only for provider-readable local image handoff. Existing web/commerce B2 execution is unchanged.
- Personal provider overrides are stored through the operating-system credential store and take precedence over environment configuration; plaintext is never stored in SQLite or returned by the API.
- Generation-node provider dropdowns show only vendors currently connected by an environment or personal credential. Their dependent model dropdowns are populated from that provider's curated catalog entry and refresh immediately when credentials change.

## Multi-track editor

Each project can hold multiple independently revisioned sequences for alternate landscape, portrait, and square cuts. A sequence stores up to 16 ordered visual, text, caption, and audio tracks and 500 typed clips, with integer-millisecond timing and a five-minute cap. Compositing rows are shown front-to-back: the highest visual/text/caption row is the frontmost result, while audio rows remain organizational. Clips move horizontally with frame snapping and vertically between compatible layers; an occupied visual destination creates a new layer instead of overwriting media. Track handles reorder whole layers, with keyboard move controls retained.

The program monitor displays every active layer rather than choosing one visual. Images, videos, titles, and captions can be selected from the canvas or timeline, then moved, uniformly resized, or rotated with continuous pointer feedback and center/edge guides. Proxy videos remain synchronized and every active unmuted source is included in the preview mix. A pointer gesture is transient until release, when the complete change is committed as one revision; locked layers stay visible but cannot be manipulated.

Sequence schema version 2 makes the shared clip transform authoritative for media and text geometry. Immutable version-1 snapshots remain readable and are normalized in memory by copying legacy text positions into the transform; only a newly saved or restored edit creates a version-2 revision. Existing stored revisions and exports are never rewritten.

Sequence content is immutable: pointer gestures commit on pointer-up, field edits debounce, and every undo/redo or history restore creates a new `SequenceRevision`. Playhead, zoom, selection, and panels are separate view state. Opening an existing project seeds its first **Main edit** from the latest video while keeping all imports, generations, and exports in the asset bin. Moving, stacking, or transforming existing assets is local editing and never invokes a provider.

Imports now include PNG/JPEG/WebP, MP4/WebM/MOV, and WAV/MP3/M4A. Lazy analysis records duration, dimensions, frame rate, and audio presence, then creates hash/version-keyed thumbnails, waveforms, and maximum-720p editing proxies without modifying originals. Generation remains provider-funded and returns to the shared reviewable library; inserting or editing an existing asset has no provider charge.

Exports consume an exact sequence revision and preset, emit 30 fps H.264 `yuv420p` plus AAC 48 kHz stereo when audio exists, save MP4 and optional SRT locally, optionally copy them to connected B2 storage, and cache identical successful renders by revision/preset/input hashes. Stable render events report preparation, encoding, saving, cancellation, failure, and completion. Failed or interrupted renders can be retried without replacing earlier exports. The Tauri `Save As…` command validates a Studio asset ID, opens the native dialog itself, and streams only that loopback asset to the single user-approved path; no broad filesystem permission is granted.

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

A successful execution with a composed MP4 opens a playable **Final output** view immediately. The final result remains available after reload through **Final video · READY** in the project sidebar, with native playback controls, file size, and an external player action. Workflow completion therefore has an explicit visible delivery surface instead of leaving the output only in node metadata.

Long-running nodes emit a live heartbeat with elapsed time every five seconds. Provider step events add per-variant progress, while the initial node event identifies the selected provider, model, variant count, and timeout. Development reload watches only `services/api/app`, preventing SQLite and WAL writes from restarting active executions.

Imported files are MIME and size checked, hashed, and copied below the configured Studio data directory. Paths are resolved before access. An import is uploaded with `genblaze-s3` only when a remote provider needs an external URL. Tauri exposes no shell or unrestricted filesystem capability.

## Commands

```powershell
pnpm setup:desktop
pnpm dev:desktop
pnpm build:desktop:renderer
pnpm build:desktop
pnpm smoke:desktop:runtime
pnpm audit:desktop:bundle
pnpm test:desktop
pnpm test:desktop:e2e:windows
```

`pnpm build:desktop` first creates a PyInstaller 6.21 one-folder API, verifies the pinned GPL FFmpeg 8.1.2 archive and its `libx264`/`libass` configuration, stages checked Noto fonts and notices, then builds MSI and NSIS installers. At runtime Tauri selects the port, starts the API without a console, monitors readiness, offers restart/log diagnostics, and terminates the API plus owned FFmpeg descendants when the app closes. A second launch focuses the existing window.

The Windows WebDriver smoke command requires `tauri-driver` and the matching Microsoft Edge WebDriver on `PATH`. Production signing, auto-update, magic-link account connection, and Prava project authorization remain later milestones. v0.1.1 is unsigned, so SmartScreen may warn.
