# Composition (Stage C)

> **Thikra extension (2026-08-01):** `composer.py` exposes `probe_media()` so
> deterministic verification uses ffprobe without creating a second
> ffmpeg-family subprocess surface. Pillow inspects images; ffprobe inspects
> streams, resolution, frame rate, and duration. Composition behavior is
> unchanged.

> **Thikra Studio extension (2026-08-03):** `compose_studio()` accepts the graph's ordered selected visual and optional audio assets, performs the local download/mux work, uploads the result through `genblaze-s3`, and returns the original Genblaze `Asset`. The Studio executor calls it through `asyncio.to_thread`. This keeps all ffmpeg/ffprobe calls inside this module and does not introduce a response wrapper.

The final-MP4 step is the only non-Genblaze media surface in this
sample. It exists because the SDK does not yet ship a composition
primitive — `genblaze-ffmpeg`, `genblaze-compose`, and `genblaze-video`
all 404 on PyPI as of 2026-05-28. The composer falls back to the system
`ffmpeg` binary via `subprocess.run(...)`.

## Module

`services/api/app/repo/composer.py` — < 520 lines.

It lives under `repo/` because it's storage-adjacent: it downloads
source assets from B2 via the same `S3StorageBackend` instance the
pipelines use, and uploads the final MP4 back to the same prefix. The
ONLY Genblaze imports it has are type imports
(`Asset` from `genblaze_core.models.asset`, `Mp4Handler` from
`genblaze_core.media`) — no `Pipeline`, no `Provider`. The structural
test `test_pipelines_is_the_only_genblaze_provider_consumer` enforces
this.

## ffmpeg pipeline

1. **Concat per-scene visuals** into a visual-only `video.mp4` using the
   concat *filter* (not the demuxer), normalizing every input to a common
   30fps canvas. The standalone sample defaults to 1280×720; a commercial
   order passes its confirmed mandate resolution (for example, 720×1280) so
   vertical orders remain vertical:

   ```
   ffmpeg -y -i scene0.mp4 -loop 1 -t 8 -i scene1_still.png ... \
     -filter_complex
       "[0:v]scale=...,pad=...,setsar=1,fps=30[v0];
        [1:v]scale=...,pad=...,setsar=1,fps=30[v1];
        [v0][v1]...concat=n=N:v=1:a=0[outv]"
     -map [outv] -c:v libx264 -pix_fmt yuv420p -an video.mp4
   ```

   **Video is best-effort.** A scene whose Decart clip failed falls back to
   its Stage B1 keyframe still, fed as `-loop 1 -t <duration>` so the image
   becomes a clip of the scene's length. The per-input `scale`+`pad` makes
   real clips and stills concat cleanly despite differing source dimensions.
   A scene raises only if it has neither a clip nor a keyframe.

2. **Mix per-scene narration WAVs + ducked music** into `audio.m4a`:

   ```
   ffmpeg ... -filter_complex
       "[0:a]adelay=0|0[v0];
        [1:a]adelay=8000|8000[v1];
        ...;
        [N:a]volume=-18dB[mus];
        [v0][v1]...[mus]amix=inputs=N+1:duration=longest:dropout_transition=0[mixed];
        [mixed]atrim=duration=<mandate duration>[aout]"
       -map [aout] -c:a aac -b:a 192k audio.m4a
   ```

   `adelay` shifts each narration to its scene start; `volume=-18dB`
   ducks the music bed underneath; `amix` combines everything.
   `dropout_transition=0` stops the surviving tracks from being re-leveled
   when a shorter one (a narration) ends. Note **no `apad`**: padding to an
   unbounded length would make `amix=longest` never terminate — the delayed
   narrations stay finite and `longest` bounds the mix. `atrim` then caps the
   mixed audio at the confirmed mandate duration, so a long narration cannot
   silently turn a four-second video order into a longer delivery.

   **Audio is best-effort.** The graph is built only from tracks that
   exist: ffmpeg input indices track *added* inputs (not scene index), so a
   scene missing narration doesn't desync the graph. Music ducks to -18 dB
   only when narration is present (`0dB` when it's the lone track). If no
   narration and no music survived, this step is skipped entirely and the
   final video is silent (step 3 emits `-an` instead of mapping `1:a`).
   `compose_final` returns `(Asset, notices)` so the API can tell the UI
   which track fell back.

3. **Finalize into `final.mp4`** — captions + audio mux. **Captions are
   best-effort and portable**: the `subtitles` (libass) filter only exists in
   some ffmpeg builds, so `_finalize_with_captions` probes `ffmpeg -filters`
   once (`_available_filters`) and degrades:

   ```
   # libass present → burn into the picture (re-encodes video):
   ffmpeg ... -filter_complex "[0:v]subtitles='captions.srt'[vout]"
              -map [vout] -map 1:a -c:v libx264 -c:a copy ... final.mp4

   # no libass → mux a soft mov_text subtitle track (video stream-copied):
   ffmpeg ... -i captions.srt -map 0:v -map 1:a -map 2:s
              -c:v copy -c:a copy -c:s mov_text ... final.mp4

   # either step fails → finalize with no captions (video+audio only).
   ```

   The SRT cues come from `Scene.caption` per scene. Each non-burned path
   emits a `notice` so the UI states the degradation (and suggests installing
   an ffmpeg with libass). Captions never fail the run — video + audio are the
   essential product. `+faststart` moves the moov atom to the front so the
   browser can start playback before the download finishes.

4. **Embed the Stage B2 Manifest** via
   `Mp4Handler().embed(final_path, b2_run.manifest)`. Best-effort —
   logged and skipped on failure rather than aborting the whole compose.
   `Mp4Handler` IS importable from `genblaze_core.media` in 0.3.2
   (the plan flagged this as uncertain; confirmed at build time).

5. **Upload to B2** at `explainers/<run-id>/final.mp4` via
   `backend().put(...)`, then synthesize an `Asset` whose `url` is the
   **durable** B2 URL (`backend().get_durable_url(key)`) — never a presigned
   one (a 1h TTL would silently 403 saved links). The frontend routes it
   through `GET /assets/{key}` for a fresh presigned redirect at playback.
   No `boto3` import; storage stays delegated.

## Why subprocess, not `ffmpeg-python`

- One fewer dependency layer; `ffmpeg-python` is a thin wrapper that
  mostly hands strings to `subprocess.run` anyway.
- Matches how production media pipelines actually ship — calling the
  binary is the deployable shape.
- Explicit `timeout=300` + `check=True` makes failure modes obvious.

## Why off the event loop

`subprocess.run` is blocking. The streaming endpoint's SSE generator is
`async def`, and it dispatches the composer with
`await asyncio.to_thread(compose_final, b2_result, b1_result, spec)` so a long
ffmpeg invocation never starves the FastAPI event loop or wedges the
SSE stream that's emitting `stage.start` / `compose.complete` frames.

Stages B1 and B2 themselves are streamed via `Pipeline.astream()` — the
async variant landed in genblaze-core 0.3.2 — so provider HTTP
round-trips don't block the loop between events either. The whole SSE
pipeline (B1 → B2 → C) is non-blocking end-to-end.

The composer classifies B2 outputs by media type rather than assuming a
fixed `(video, narration)` completion position. This preserves a completed
provider clip when a concurrent sibling fails or is omitted; the first audio
track for each scene is narration and a remaining trailing audio track is
optional music.

## Failure mode

If ffmpeg isn't on `PATH`, `compose_final` raises immediately with a
hint pointing at `infra/README.md`. If ffmpeg runs but a stage exits
nonzero, the `RuntimeError` includes the captured `stderr` for fast
diagnosis. All source assets are durable in B2 either way: each
keyframe, clip, narration, music WAV, and the Stage B2 Manifest are all
already written before Stage C starts. Recovery is a fresh
`/runs/media/stream` call — there is no compose-only retry endpoint in
the current shape (composition lives only inside the streaming flow).

## Documented SDK gap

A hypothetical `genblaze-compose` would let this whole module
disappear:

```python
# Speculative future shape
from genblaze_compose import Composer
asset = Composer(backend=backend(), prefix="explainers")\
    .concat([scene.video_asset for scene in scenes])\
    .mix(narrations=[s.tts_asset for s in scenes], music=music_asset, duck_db=-18)\
    .captions([(s.caption, s.duration_sec) for s in scenes])\
    .embed_manifest(b2_run.manifest)\
    .export(f"{run_id}/final.mp4")
```

The composer should return an `Asset`, automatically embed the source
Manifest, and run ffmpeg out-of-process behind a public-facing
`Pipeline`-like surface so cancellation, tracing, and step caching come
for free.
