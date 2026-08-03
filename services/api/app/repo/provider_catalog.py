"""Provider catalog — the SINGLE genblaze provider-import surface.

This is a kitchen-sink switchboard: every generative modality can be driven by
any configured Genblaze provider, chosen per-run. This module is the one place
provider classes are imported (AGENTS Rule 3); `pipelines.py` consumes the
catalog and never imports a provider class directly (it imports only
`genblaze_openai.chat` for the storyboard stage, which is a function).

Adding a provider = ONE `CatalogEntry` here (AGENTS Rule 4). Provider-specific
quirks are encoded as DATA on the entry so `pipelines.py` stays branch-free:

  * `make`           — factory; constructs the provider with the right key
                       kwarg (api_key / api_token / api_secret / auth_token)
                       and bakes in construction quirks (e.g. GMICloud music's
                       instrumental registry override).
  * `image_handoff`  — how a keyframe reaches a video provider:
                       "external_inputs" (the dominant pattern — Kling, Runway,
                       Luma, Replicate route the image from step inputs) or
                       "image_kwarg" (Decart's legacy `image=` path).
  * `snap_durations` — a video model's supported clip-length grid (Kling i2v
                       renders 5s/10s only); None means no constraint.

Slots ("chat"/"image"/"video"/"tts"/"music") map to the 5 pipeline roles, NOT
to `Modality` — `tts` and `music` are both `Modality.AUDIO`, so a slot key is
what disambiguates them.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from genblaze_core import Modality
from genblaze_core.providers.base import BaseProvider
from genblaze_core.providers.model_registry import ModelRegistry
from genblaze_decart import DecartImageProvider, DecartVideoProvider
from genblaze_elevenlabs import ElevenLabsTTSProvider
from genblaze_gmicloud import GMICloudAudioProvider, GMICloudVideoProvider
from genblaze_gmicloud.models.audio import build_audio_registry
from genblaze_google import ImagenProvider, VeoProvider
from genblaze_hume import HumeTTSProvider
from genblaze_lmnt import LMNTProvider
from genblaze_luma import LumaProvider
from genblaze_nvidia import NvidiaAudioProvider, NvidiaImageProvider, NvidiaVideoProvider
from genblaze_openai import DalleProvider, OpenAITTSProvider, SoraProvider
from genblaze_replicate import ReplicateProvider
from genblaze_runway import RunwayProvider

from app.config import settings
from app.repo.openai_sora_compat import fetch_sora_output, submit_sora

# Slot identifiers — the 5 switchboard roles. `chat` is special (driven by the
# standalone `genblaze_openai.chat()` function, not a Pipeline step), so its
# entries carry no `make` and no genblaze `Modality`.
CHAT, IMAGE, VIDEO, TTS, MUSIC = "chat", "image", "video", "tts", "music"

# GMICloud Kling i2v renders 5s OR 10s clips only — any other `duration` 400s.
KLING_GRID = (5.0, 10.0)
SORA_GRID = (4.0, 8.0, 12.0)

# Portable SDK transfer root; avoids the Genblaze Windows file-URI parser bug.
OPENAI_IMAGE_TRANSFER_ROOT = Path("/tmp")
class _ThikraSoraProvider(SoraProvider):
    """Bridge Genblaze 0.3.x to the current OpenAI Videos API parameter name."""

    def submit(self, step, config=None):
        return submit_sora(self, step)

    def fetch_output(self, prediction_id, step):
        return fetch_sora_output(self, prediction_id, step)

def _instrumental_music_registry() -> ModelRegistry:
    """Audio registry override making GMICloud MiniMax-Music an INSTRUMENTAL bed.

    MiniMax-Music requires a `lyrics` payload field that the default family
    allowlist drops (a bare prompt 400s with "lyrics (Required parameter is
    missing)"). We register a per-model override that admits `lyrics`/
    `is_instrumental` and defaults them to a vocal-free score.
    """
    reg = build_audio_registry()
    base = reg.get(settings.music_model)
    reg.register(
        replace(
            base,
            param_allowlist=(base.param_allowlist or frozenset()) | {"lyrics", "is_instrumental"},
            param_defaults={
                **dict(base.param_defaults),
                "lyrics": "[Inst]",
                "is_instrumental": True,
            },
        )
    )
    return reg


@dataclass(frozen=True)
class CatalogEntry:
    """One (slot, vendor) cell of the switchboard. See module docstring."""

    slot: str
    vendor: str
    env_key: str  # settings attribute gating availability
    default_model: str  # curated default (also the DTO fallback)
    suggested_models: tuple[str, ...]  # dropdown hints; free-text override allowed
    modality: Modality | None = None  # genblaze Modality for `.step()`; None for chat
    make: Callable[[str | None], BaseProvider] | None = None  # optional Studio secret override
    image_handoff: str | None = None  # "external_inputs" | "image_kwarg" (video only)
    snap_durations: tuple[float, ...] | None = None  # video clip-length grid
    supports_text_only: bool = False  # video generation without a reference image


# --- The catalog: {slot: {vendor: CatalogEntry}} ----------------------------
#
# Defaults for the FIRST-WAVE vendors reference `settings.*_model` so the
# existing env overrides (IMAGE_MODEL, TTS_MODEL, …) keep working. New-wave
# vendors carry literal defaults (validated against each provider's model
# family — see tests/test_provider_catalog.py). Override any model per-run
# from the UI (free text) regardless of what's listed here.

CATALOG: dict[str, dict[str, CatalogEntry]] = {
    # CHAT — the one modality not generalized: the storyboard needs OpenAI's
    # structured-output (`response_format=`) path, which is `chat()` (a
    # function, not a provider). Dispatched by `pipelines.generate_storyboard`.
    CHAT: {
        "openai": CatalogEntry(
            slot=CHAT,
            vendor="openai",
            env_key="openai_api_key",
            default_model=settings.chat_model,
            suggested_models=("gpt-4.1-nano", "gpt-4.1-mini", "gpt-4.1"),
        ),
    },
    # IMAGE — text-to-image generation (Stage B0 reference + B1 keyframes).
    # GMICloudImageProvider is EXCLUDED: its families are edit-only (genfill /
    # seededit / reve-edit) — no text-to-image generation surface.
    IMAGE: {
        "replicate": CatalogEntry(
            slot=IMAGE,
            vendor="replicate",
            env_key="replicate_api_token",
            default_model="black-forest-labs/flux-schnell",
            suggested_models=(
                "black-forest-labs/flux-schnell",
                "black-forest-labs/flux-dev",
                "google/imagen-4",
            ),
            modality=Modality.IMAGE,
            make=lambda secret=None: ReplicateProvider(api_token=secret or settings.replicate_api_token),
        ),
        "google": CatalogEntry(
            slot=IMAGE,
            vendor="google",
            env_key="google_api_key",
            default_model=settings.image_model,
            suggested_models=(
                "imagen-4.0-generate-001",
                "imagen-4.0-ultra-generate-001",
                "imagen-4.0-fast-generate-001",
            ),
            modality=Modality.IMAGE,
            make=lambda secret=None: ImagenProvider(api_key=secret or settings.google_api_key),
        ),
        "openai": CatalogEntry(
            slot=IMAGE,
            vendor="openai",
            env_key="openai_api_key",
            default_model="gpt-image-1-mini",
            suggested_models=("gpt-image-1-mini", "gpt-image-1", "dall-e-3"),
            modality=Modality.IMAGE,
            make=lambda secret=None: DalleProvider(
                api_key=secret or settings.openai_api_key,
                output_dir=OPENAI_IMAGE_TRANSFER_ROOT,
            ),
        ),
        "nvidia": CatalogEntry(
            slot=IMAGE,
            vendor="nvidia",
            env_key="nvidia_api_key",
            default_model="black-forest-labs/flux.1-schnell",
            suggested_models=(
                "black-forest-labs/flux.1-schnell",
                "stabilityai/stable-diffusion-3-5-large",
            ),
            modality=Modality.IMAGE,
            make=lambda secret=None: NvidiaImageProvider(api_key=secret or settings.nvidia_api_key),
        ),
        "decart": CatalogEntry(
            slot=IMAGE,
            vendor="decart",
            env_key="decart_api_key",
            default_model="lucy-pro-t2i",
            suggested_models=("lucy-pro-t2i",),
            modality=Modality.IMAGE,
            make=lambda secret=None: DecartImageProvider(api_key=secret or settings.decart_api_key),
        ),
    },
    # VIDEO — image-to-video (each keyframe → a clip). Handoff is
    # "external_inputs" for everyone except Decart's legacy `image=` path.
    VIDEO: {
        "replicate": CatalogEntry(
            slot=VIDEO,
            vendor="replicate",
            env_key="replicate_api_token",
            default_model="minimax/video-01",
            suggested_models=("minimax/video-01", "kwaivgi/kling-v2.1", "wan-video/wan-2.5-i2v"),
            modality=Modality.VIDEO,
            make=lambda secret=None: ReplicateProvider(api_token=secret or settings.replicate_api_token),
            image_handoff="external_inputs",
            supports_text_only=True,
        ),
        "gmicloud": CatalogEntry(
            slot=VIDEO,
            vendor="gmicloud",
            env_key="gmi_api_key",
            default_model=settings.gmi_video_model,
            suggested_models=("Kling-Image2Video-V2.1-Master", "pixverse-v5.6-i2v"),
            modality=Modality.VIDEO,
            make=lambda secret=None: GMICloudVideoProvider(api_key=secret or settings.gmi_api_key),
            image_handoff="external_inputs",
            snap_durations=KLING_GRID,
        ),
        "google": CatalogEntry(
            slot=VIDEO,
            vendor="google",
            env_key="google_api_key",
            default_model="veo-3.0-generate-001",
            suggested_models=(
                "veo-3.0-generate-001",
                "veo-3.0-fast-generate-001",
                "veo-2.0-generate-001",
            ),
            modality=Modality.VIDEO,
            make=lambda secret=None: VeoProvider(api_key=secret or settings.google_api_key),
            image_handoff="external_inputs",
        ),
        "openai": CatalogEntry(
            slot=VIDEO,
            vendor="openai",
            env_key="openai_api_key",
            default_model=settings.video_model,
            suggested_models=("sora-2", "sora-2-pro"),
            modality=Modality.VIDEO,
            make=lambda secret=None: _ThikraSoraProvider(
                api_key=secret or settings.openai_api_key,
                output_dir=OPENAI_IMAGE_TRANSFER_ROOT,
            ),
            image_handoff="external_inputs",
            snap_durations=SORA_GRID,
            supports_text_only=True,
        ),
        "runway": CatalogEntry(
            slot=VIDEO,
            vendor="runway",
            env_key="runway_api_secret",
            default_model="gen4_turbo",
            suggested_models=("gen4_turbo", "gen3a_turbo"),
            modality=Modality.VIDEO,
            make=lambda secret=None: RunwayProvider(api_secret=secret or settings.runway_api_secret),
            image_handoff="external_inputs",
        ),
        "luma": CatalogEntry(
            slot=VIDEO,
            vendor="luma",
            env_key="luma_api_key",
            default_model="ray-2",
            suggested_models=("ray-2", "ray-flash-2"),
            modality=Modality.VIDEO,
            make=lambda secret=None: LumaProvider(auth_token=secret or settings.luma_api_key),
            image_handoff="external_inputs",
            supports_text_only=True,
        ),
        "nvidia": CatalogEntry(
            slot=VIDEO,
            vendor="nvidia",
            env_key="nvidia_api_key",
            default_model="nvidia/cosmos-2.0-diffusion-video2world",
            suggested_models=("nvidia/cosmos-2.0-diffusion-video2world",),
            modality=Modality.VIDEO,
            make=lambda secret=None: NvidiaVideoProvider(api_key=secret or settings.nvidia_api_key),
            image_handoff="external_inputs",
        ),
        "decart": CatalogEntry(
            slot=VIDEO,
            vendor="decart",
            env_key="decart_api_key",
            # `lucy-pro-i2v`, NOT settings.video_model (`lucy-2.1`): Decart's
            # i2v family is `^lucy-.*(?:2v|...)`. NOTE: Decart retired hosted
            # image-to-video, so this path typically fails at runtime → the
            # composer degrades to the keyframe still. Kept for completeness.
            default_model="lucy-pro-i2v",
            suggested_models=("lucy-pro-i2v", "lucy-dev-i2v"),
            modality=Modality.VIDEO,
            make=lambda secret=None: DecartVideoProvider(api_key=secret or settings.decart_api_key),
            image_handoff="image_kwarg",
        ),
    },
    # TTS — text-to-speech narration (Modality.AUDIO, text input).
    TTS: {
        "openai": CatalogEntry(
            slot=TTS,
            vendor="openai",
            env_key="openai_api_key",
            default_model="gpt-4o-mini-tts",
            suggested_models=("gpt-4o-mini-tts", "tts-1", "tts-1-hd"),
            modality=Modality.AUDIO,
            make=lambda secret=None: OpenAITTSProvider(api_key=secret or settings.openai_api_key),
        ),
        "nvidia": CatalogEntry(
            slot=TTS,
            vendor="nvidia",
            env_key="nvidia_api_key",
            default_model=settings.tts_model,
            suggested_models=("nvidia/magpie-tts-multilingual",),
            modality=Modality.AUDIO,
            make=lambda secret=None: NvidiaAudioProvider(api_key=secret or settings.nvidia_api_key),
        ),
        "elevenlabs": CatalogEntry(
            slot=TTS,
            vendor="elevenlabs",
            env_key="elevenlabs_api_key",
            default_model="eleven_multilingual_v2",
            suggested_models=("eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_v3"),
            modality=Modality.AUDIO,
            make=lambda secret=None: ElevenLabsTTSProvider(api_key=secret or settings.elevenlabs_api_key),
        ),
        "lmnt": CatalogEntry(
            slot=TTS,
            vendor="lmnt",
            env_key="lmnt_api_key",
            default_model="aurora",
            suggested_models=("aurora", "blizzard"),
            modality=Modality.AUDIO,
            make=lambda secret=None: LMNTProvider(api_key=secret or settings.lmnt_api_key),
        ),
        "hume": CatalogEntry(
            slot=TTS,
            vendor="hume",
            env_key="hume_api_key",
            default_model="octave-2",
            suggested_models=("octave-2", "octave-1"),
            modality=Modality.AUDIO,
            make=lambda secret=None: HumeTTSProvider(api_key=secret or settings.hume_api_key),
        ),
    },
    # MUSIC — instrumental score bed (Modality.AUDIO). GMICloud needs the
    # instrumental registry override (baked into `make`).
    MUSIC: {
        "replicate": CatalogEntry(
            slot=MUSIC,
            vendor="replicate",
            env_key="replicate_api_token",
            default_model="meta/musicgen",
            suggested_models=("meta/musicgen", "ardianfe/music-gen-fn-200e"),
            modality=Modality.AUDIO,
            make=lambda secret=None: ReplicateProvider(api_token=secret or settings.replicate_api_token),
        ),
        "gmicloud": CatalogEntry(
            slot=MUSIC,
            vendor="gmicloud",
            env_key="gmi_api_key",
            default_model=settings.music_model,
            suggested_models=("minimax-music-2.5",),
            modality=Modality.AUDIO,
            make=lambda secret=None: GMICloudAudioProvider(
                api_key=secret or settings.gmi_api_key, models=_instrumental_music_registry()
            ),
        ),
    },
}


def entries_for(slot: str) -> list[CatalogEntry]:
    """All vendor entries registered for a slot (insertion order = UI order)."""
    return list(CATALOG[slot].values())


def resolve(slot: str, vendor: str) -> CatalogEntry:
    """Look up one entry. Raises `ValueError` on an unknown slot/vendor."""
    try:
        return CATALOG[slot][vendor]
    except KeyError as exc:
        raise ValueError(f"no provider for slot={slot!r} vendor={vendor!r}") from exc


def key_available(entry: CatalogEntry) -> bool:
    """Whether the API key gating this entry's vendor is configured."""
    return bool(getattr(settings, entry.env_key, ""))


def matrix() -> dict[str, list[dict]]:
    """Serialise the catalog for `GET /providers` — drives the UI dropdowns."""
    return {
        slot: [
            {
                "vendor": e.vendor,
                "default_model": e.default_model,
                "suggested_models": list(e.suggested_models),
                "modality": e.modality.name.lower() if e.modality else "text",
                "key_available": key_available(e),
                "supports_seed": e.slot in {IMAGE, VIDEO},
                "supports_reference_input": bool(e.image_handoff),
                "supports_text_only": e.supports_text_only,
                "duration_grid": list(e.snap_durations) if e.snap_durations else None,
            }
            for e in entries.values()
        ]
        for slot, entries in CATALOG.items()
    }


def transcribe_audio(path: Path, secret: str, language: str | None = None) -> list[dict]:
    """OpenAI transcription adapter; provider HTTP stays in the catalog boundary."""
    import httpx

    data: list[tuple[str, str]] = [
        ("model", "whisper-1"),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
    ]
    if language:
        data.append(("language", language))
    with path.open("rb") as source:
        response = httpx.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {secret}"},
            data=data,
            files={"file": (path.name, source, "audio/wav")},
            timeout=300,
        )
    response.raise_for_status()
    result = response.json()
    return [
        {
            "id": f"cue-{index + 1}",
            "start_ms": round(float(segment["start"]) * 1000),
            "end_ms": round(float(segment["end"]) * 1000),
            "text": str(segment["text"]).strip(),
        }
        for index, segment in enumerate(result.get("segments", []))
        if str(segment.get("text", "")).strip()
    ]
