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


def test_genblaze_provider_imports_confined() -> None:
    """Provider CLASS imports live only in `provider_catalog.py` (the catalog is
    the single provider-import surface) and `pipelines.py` (which imports just
    `genblaze_openai.chat`, the standalone storyboard function). `composer.py`
    imports `genblaze_core` types only (Asset/Manifest/Mp4Handler) — never a
    Pipeline/Provider; core/s3 are storage layers, not in `provider_roots`."""
    provider_roots = {
        "genblaze_openai",
        "genblaze_google",
        "genblaze_decart",
        "genblaze_nvidia",
        "genblaze_gmicloud",
        "genblaze_replicate",
        "genblaze_runway",
        "genblaze_luma",
        "genblaze_elevenlabs",
        "genblaze_lmnt",
        "genblaze_hume",
    }
    offenders: list[str] = []
    allowed = {
        APP_ROOT / "repo" / "provider_catalog.py",
        APP_ROOT / "repo" / "pipelines.py",
    }
    for path in _py_files():
        if path in allowed:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in provider_roots:
                    offenders.append(
                        f"{path.relative_to(APP_ROOT)}:{node.lineno} imports {node.module}"
                    )
    assert not offenders, f"Provider imports outside the catalog/pipelines: {offenders}"


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
        "health",
        "list_run_assets",
        "list_files",
        "get_asset",
        "create_storyboard",  # blocking OpenAI chat + B2 put, up front
        "get_providers",  # trivial dict build — no event loop needed
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
    earns its lines. Bumped 520 → 570 for media-type track classification:
    concurrent B2 results must preserve completed video and distinguish speech
    narration from optional music without relying on fixed step positions.
    """
    lines = (APP_ROOT / "repo" / "composer.py").read_text().splitlines()
    assert len(lines) < 570, f"composer.py is {len(lines)} lines — budget is 570"


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
    # Bumped 500 → 580 for the switchboard: GET /providers, the per-modality
    # selection resolver (`_resolve_choice`), and threading the resolved
    # provider entries into the three build_* calls + startup/health key dicts.
    assert len(lines) < 580, f"main.py is {len(lines)} lines — budget is 580"


def test_mcp_is_a_transport_over_the_gateway_facade() -> None:
    source = (APP_ROOT / "agents" / "mcp.py").read_text()
    forbidden = ["app.thikra.database", "app.commerce.models", "sqlalchemy", "SessionLocal"]
    assert not [name for name in forbidden if name in source]
    assert "from app.agents import gateway" in source
    assert "get_access_token" in source


def test_commerce_secrets_are_not_persistence_columns_or_mcp_outputs() -> None:
    models = (APP_ROOT / "commerce" / "models.py").read_text()
    mcp = (APP_ROOT / "agents" / "mcp.py").read_text()
    assert "hashed_secret" in models
    assert "plaintext_api_key" not in models
    assert "prava_secret_key" not in models
    assert "b2_application_key" not in models
    assert "session_token" not in mcp
    assert "private_key" not in mcp


def test_commercial_fulfillment_and_private_order_guards_are_explicit() -> None:
    fulfillment = (APP_ROOT / "commerce" / "fulfillment.py").read_text()
    api = (APP_ROOT / "commerce" / "api.py").read_text()
    assert 'order.status != "PAID"' in fulfillment
    assert "verification failures remain" in fulfillment
    assert fulfillment.index("_create_delivery_package(db, order, job, run)") < fulfillment.index(
        '"DELIVERED"'
    )
    assert "require_order_owner(db, auth, item)" in api


def test_active_svelte_pages_have_no_coming_soon_copy() -> None:
    web_routes = APP_ROOT.parents[2] / "apps" / "web" / "src" / "routes"
    offenders = [
        path
        for path in web_routes.rglob("*.svelte")
        if "coming soon" in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []


def test_provider_catalog_line_budget() -> None:
    """`repo/provider_catalog.py` must stay below 400 lines.

    The catalog is intentionally flat DATA (one CatalogEntry per (slot,
    vendor)). If it outgrows this it's a sign a quirk is becoming a framework
    — push provider-construction logic into `make()` rather than new fields.
    """
    lines = (APP_ROOT / "repo" / "provider_catalog.py").read_text().splitlines()
    assert len(lines) < 400, f"provider_catalog.py is {len(lines)} lines — budget is 400"
