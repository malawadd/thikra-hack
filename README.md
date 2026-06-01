# genblaze-gen-media-multi-provider-sample

> One prompt → narrated, scored, captioned MP4. OpenAI + NVIDIA + Decart +
> GMICloud, orchestrated by [Genblaze][genblaze]. Backblaze B2 is the
> sole asset store; the sample contains zero direct `boto3` calls.

The sample shows what Genblaze enables functionally: a developer types one
sentence ("a kid's introduction to how solar panels work") and the library
coordinates a six-step media workflow end-to-end across four providers. No
bespoke retry logic, no per-provider auth/poll glue, no `boto3` import —
every Pipeline step is one `.step()` call, and every asset lands in B2
via `genblaze-s3`.

## Why this sample exists

Phase-1 acceptance for new generative-media providers in the Genblaze
ecosystem is "a multi-provider sample app works against the published
wheel." This sample wires:

| Stage     | Genblaze surface                | Model default                      | Output                |
|-----------|---------------------------------|------------------------------------|-----------------------|
| A — plan  | `genblaze_openai.chat()` (function) | `gpt-4.1-nano`                | `StoryboardSpec` JSON |
| B1 — image | `DalleProvider` (`.step()`)     | `gpt-image-1`                      | one PNG per scene     |
| B2 — video | `DecartVideoProvider` (`.step()`) | `lucy-pro`                        | one MP4 per scene     |
| B2 — TTS  | `NvidiaAudioProvider` (`.step()`) | `nvidia/magpie-tts-multilingual`  | one WAV per scene     |
| B2 — music | `GMICloudAudioProvider` (`.step()`) | `minimax-music-2.5`             | one WAV for the run   |
| C — compose | (ffmpeg fallback)             | —                                  | final MP4 → B2        |

Stages B1 and B2 are linked Pipelines sharing one slug
(`genblaze-gen-media-multi-provider-sample`); B2's Manifest records its
`parent_run_id` so the cross-stage lineage is durable in B2.

> **Note on Stage A.** `genblaze-openai` 0.3.0 ships `chat()` as a
> standalone function (not a `BaseProvider`), so the storyboard step
> cannot ride `Pipeline.step()`. We call `chat(..., response_format=StoryboardSpec)`
> directly and persist the resulting JSON to B2 by hand. Stages B1 and B2
> remain proper Pipelines. The function-vs-class asymmetry is filed as
> Genblaze SDK feedback — see `docs/features/prompt-to-storyboard.md`.

## Quickstart

### 1. Provision accounts

- **Backblaze B2** — create a bucket + Application Key
  ([signup](https://www.backblaze.com/sign-up/cloud-storage)). Region
  format: `us-west-004` / `eu-central-003` / etc.
- **OpenAI** — API key for `chat()` (Stage A) + `DalleProvider` (Stage B1)
  ([platform.openai.com](https://platform.openai.com/)).
- **NVIDIA NIM** — API key for TTS ([build.nvidia.com](https://build.nvidia.com/)).
- **Decart** — API key for video ([decart.ai](https://decart.ai/)).
- **GMICloud** — API key for music ([gmicloud.ai](https://www.gmicloud.ai/)).

### 2. Install ffmpeg

The composer shells out to `ffmpeg`; install it before running:

```bash
# macOS
brew install ffmpeg
# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg
```

See `infra/README.md` for B2 bucket + lifecycle suggestions.

### 3. Configure

```bash
cp .env.example .env
# Fill in B2_* + provider keys. Replace <region> in B2_REGION with the
# region the bucket was created in (e.g. us-west-004). genblaze-s3
# derives the S3 endpoint from the region — no B2_ENDPOINT needed.
```

### 4. Install + run

From the sample root:

```bash
pnpm setup    # one-shot: pnpm install + creates services/api/.venv and pip-installs requirements.txt
pnpm dev      # starts FastAPI (:8000) and Next.js (:3000) together via concurrently
```

That's it — no separate terminals. Output is prefixed `[web]` / `[api]` so
you can follow both streams in one log. Ctrl+C stops both.

Re-runs after the first time skip `setup` and just `pnpm dev`. Other
scripts: `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm check:structure`.

Open <http://localhost:3000>, type one sentence, click **Generate
explainer**. The storyboard renders inline; expand "Review & refine" to
edit scenes before kicking off media generation. Live pipeline events
stream as the run progresses, and per-scene keyframes / clips / narrations
appear in the scene strip as they land.

## Architecture

```
apps/web (Next.js, App Router, React 19)
    │
    │  /api/proxy/...
    ▼
services/api (FastAPI)
    │
    │  app/main.py    ──► app/repo/pipelines.py  ──►  genblaze-{core,s3,openai,nvidia,decart,gmicloud}
    │                     app/repo/composer.py   ──►  system ffmpeg (only non-Genblaze adapter)
    │
    ▼
Backblaze B2 (bucket: $B2_BUCKET_NAME, prefix: explainers/<run-id>/...)
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the layer diagram, Stage A/B1/B2/C
handoffs, ethos constraints, and the SSE wire format.

## Repository layout

```
genblaze-gen-media-multi-provider-sample/
├── apps/web/                  # Next.js single-page UI
├── services/api/              # FastAPI + Genblaze (boto3 forbidden)
│   ├── app/repo/pipelines.py  # The ONLY file that imports genblaze provider classes + chat()
│   ├── app/repo/composer.py   # ffmpeg composer (only non-Genblaze media surface)
│   └── tests/                 # Structural + smoke + composer tests
├── docs/                      # Feature docs + workflows
├── infra/                     # B2 bucket + ffmpeg setup notes
└── .env.example               # Parent-standard env var names
```

## Key features

- **One prompt → one MP4.** Default path is a single textarea and one CTA.
- **Mixed Genblaze surfaces, one delivery.** Stage A is `chat()` (function),
  Stages B1/B2 are `Pipeline.step()` (class), Stage C is ffmpeg. The
  pipeline layer hides the asymmetry from the API surface.
- **Structured planning via `response_format=StoryboardSpec`.** The
  storyboard JSON schema is enforced upstream by OpenAI.
- **Per-scene fan-out at `max_concurrency=3`.** Genblaze handles the
  parallelism in Stages B1 and B2; the sample provides no executor.
- **Live SSE pipeline progress + per-scene strip.** Stage B1 + B2 events
  flow through the FastAPI proxy unmodified; keyframes / clips /
  narrations appear in `SceneStrip` as soon as they land in B2.
- **Optional progressive guidance.** Edit any scene's prompts before
  media generation runs — opt-in via a disclosure panel.
- **Provenance for free.** Stages B1 + B2 share one slug; B2's Manifest
  records `parent_run_id` to capture lineage. The final MP4 has the
  Stage B2 Manifest embedded via `Mp4Handler`.

## Doc index

- [`AGENTS.md`](AGENTS.md) — hard rules for AI assistants working on this app.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — layers, ethos constraints, SSE wire format.
- [`docs/app-workflows.md`](docs/app-workflows.md) — one-prompt-to-MP4 sequence diagram.
- [`docs/features/prompt-to-storyboard.md`](docs/features/prompt-to-storyboard.md) — the `chat(response_format=…)` idiom.
- [`docs/features/media-generation.md`](docs/features/media-generation.md) — keyframes, image→video, TTS, music.
- [`docs/features/composition.md`](docs/features/composition.md) — ffmpeg fallback + SDK gap.
- [`docs/features/progressive-guidance.md`](docs/features/progressive-guidance.md) — optional refine flow.

[genblaze]: https://github.com/backblaze-labs/genblaze
