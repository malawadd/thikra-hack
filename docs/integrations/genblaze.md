# Genblaze integration

The original provider switchboard is preserved. `provider_catalog.py` supplies one `CatalogEntry` per slot/vendor and is the only provider-class import surface. Thikra reads `catalog.matrix()`, records quote scores, and stores automatic/manual selection per run.

Outside DEMO, `orchestration.py` executes the existing path: OpenAI structured storyboard, B0 reference, B1 keyframes, B2 video/TTS/music, then ffmpeg composition. Confirmed scene edits replace image/narration prompts before paid calls. Genblaze manifests retain pipeline lineage; SQL adds workspace, brief, mandate version, run, payment, budget, verification, provider decision, and asset context.

B0/B1 retain preflight. B2 retains the documented best-effort policy. Provider credentials remain server-side and missing keys appear unavailable.
