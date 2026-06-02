"""Structural invariants — enforced at CI time.

Storage is fully delegated to `genblaze-s3`; direct `boto3` / `botocore`
imports are forbidden anywhere in `app/`. Per-file line budgets keep the
sample legible. This sample has NO `_client` reach-through (unlike
`genblaze-gmicloud-pipeline`), so the allowlist is empty.
"""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
FORBIDDEN_MODULES = {"boto3", "botocore"}


def _py_files() -> list[Path]:
    return sorted(APP_ROOT.rglob("*.py"))


def test_no_direct_aws_client_imports() -> None:
    """No file under `app/` may import boto3 / botocore — storage is genblaze-s3."""
    offenders: list[str] = []
    for path in _py_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                        offenders.append(f"{path.relative_to(APP_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in FORBIDDEN_MODULES:
                    offenders.append(f"{path.relative_to(APP_ROOT)}:{node.lineno}")
    assert not offenders, f"Direct boto3/botocore imports found: {offenders}"


def test_pipelines_is_the_only_genblaze_provider_consumer() -> None:
    """`genblaze_openai` / `_decart` / `_nvidia` / `_gmicloud` may import
    only from `app/repo/pipelines.py`. `composer.py` imports `genblaze_core`
    types only (Asset, Manifest, Mp4Handler) — never Pipeline/Provider."""
    provider_roots = {
        "genblaze_openai", "genblaze_decart", "genblaze_nvidia", "genblaze_gmicloud",
    }
    offenders: list[str] = []
    allowed = {APP_ROOT / "repo" / "pipelines.py"}
    for path in _py_files():
        if path in allowed:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in provider_roots:
                    offenders.append(f"{path.relative_to(APP_ROOT)}:{node.lineno} imports {node.module}")
    assert not offenders, f"Provider imports outside pipelines.py: {offenders}"


def test_blocking_b2_endpoints_are_sync_def() -> None:
    """Endpoints that make blocking B2 calls must be sync `def`, never `async def`.

    Starlette runs sync handlers in a threadpool; a blocking B2 call inside an
    `async def` handler runs ON the event loop and a single stall freezes the
    whole API (this regression once wedged every endpoint, incl. built-in
    `/docs`). This guards against silently re-adding `async` to them. The SSE
    streamer stays `async def` deliberately — it `await`s and offloads ffmpeg
    via `asyncio.to_thread`, so it is intentionally excluded here.
    """
    must_be_sync = {
        "health", "list_run_assets", "list_files", "get_asset",
        "create_storyboard",  # blocking OpenAI chat + B2 put, up front
    }
    tree = ast.parse((APP_ROOT / "main.py").read_text())
    offenders = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in must_be_sync
    ]
    assert not offenders, (
        f"these blocking-B2 handlers must be sync `def` (Rule: I/O off the "
        f"event loop), found `async def`: {offenders}"
    )


def test_pipelines_line_budget() -> None:
    """`repo/pipelines.py` must stay below 460 lines.

    Bumped 320 → 400 for the Google Imagen swap (Stage B0/B1) + the
    Decart↔GMICloud video-provider fallback resolver that auto-swaps
    when a key is missing. Bumped 400 → 460 for two provider-contract
    fixes: `snap_scene_durations` (Kling i2v renders 5s/10s clips only)
    and `_instrumental_music_registry` (MiniMax-Music needs a `lyrics`/
    `is_instrumental` payload the default family allowlist drops).
    """
    lines = (APP_ROOT / "repo" / "pipelines.py").read_text().splitlines()
    assert len(lines) < 460, f"pipelines.py is {len(lines)} lines — budget is 460"


def test_composer_line_budget() -> None:
    """`repo/composer.py` must stay below 450 lines.

    Bumped 250 → 290 for ffmpeg timing logs + per-download size logs +
    timeout-specific exception handler. Bumped 290 → 380 for best-effort
    audio (per-input mix graph, silent-video `-an` path, presence helpers).
    Bumped 380 → 450 for best-effort *video*: a failed Decart clip falls
    back to the scene's Stage B1 keyframe still, so the composer now takes
    the keyframe run, branches video/still in `_group_scenes`, and concats
    via a resolution-normalizing filter (mixing real clips + looped stills).
    Bumped 450 → 520 for portable, best-effort *captions*: detect the
    `subtitles` (libass) filter, burn when present else mux a soft `mov_text`
    track, falling back to no captions — so an ffmpeg built without libass no
    longer fails Stage C. Composer is the highest-risk module; this resilience
    earns its lines.
    """
    lines = (APP_ROOT / "repo" / "composer.py").read_text().splitlines()
    assert len(lines) < 520, f"composer.py is {len(lines)} lines — budget is 520"


def test_main_line_budget() -> None:
    """`app/main.py` must stay below 460 lines — keeps the SSE plumbing tight.

    Bumped 360 → 400 for SSE frame outflow logging + endpoint input
    logging + health probe logging + presign-404 / list-files error
    logging. Bumped 400 → 430 for the best-effort B2 stage. Bumped 430 → 460
    for classified errors: Stage A + the SSE `error` frame now carry
    `{code, retryable, message, hint}` via `classify()` (the actual logic
    lives in `app/errors.py`), and `/health` reports `ffmpeg_present`.
    """
    lines = (APP_ROOT / "main.py").read_text().splitlines()
    # Bumped 460 → 480 for `manifest_uri` propagation in the
    # `compose.complete` SSE frame (Composition tile's Manifest dialog).
    # Bumped 480 → 500 for the `inline=1` manifest-proxy branch on
    # `/assets/{key}` (fetch() can't read B2's cross-origin presigned URL)
    # and the duration-snap call in the media stream handler.
    assert len(lines) < 500, f"main.py is {len(lines)} lines — budget is 500"
