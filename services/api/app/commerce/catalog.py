"""Curated, versioned service definitions with machine-readable JSON Schemas."""

from __future__ import annotations

COMMON_BRIEF_PROPERTIES = {
    "brief": {"type": "string", "minLength": 10, "maxLength": 8000},
    "language": {"type": "string", "default": "ar"},
    "requiredProviders": {"type": "array", "items": {"type": "string"}, "default": []},
    "forbiddenProviders": {"type": "array", "items": {"type": "string"}, "default": []},
    "requiredElements": {"type": "array", "items": {"type": "string"}, "default": []},
    "forbiddenElements": {"type": "array", "items": {"type": "string"}, "default": []},
    "claimConstraints": {"type": "array", "items": {"type": "string"}, "default": []},
    "humanReviewRequired": {"type": "boolean", "default": False},
    "maximumRetries": {"type": "integer", "minimum": 0, "maximum": 5, "default": 1},
}


def input_schema(*, modalities: list[str], duration: int | None = None) -> dict:
    properties = {
        **COMMON_BRIEF_PROPERTIES,
        "deliverableCount": {"type": "integer", "minimum": 1, "maximum": 12, "default": 1},
        "aspectRatio": {"type": "string", "enum": ["1:1", "4:5", "9:16", "16:9"]},
        "resolution": {"type": "string", "pattern": r"^\d{2,5}x\d{2,5}$"},
        "modalities": {"type": "array", "items": {"enum": modalities}, "default": modalities},
        "referenceAssets": {
            "type": "array",
            "items": {"type": "string", "format": "uri"},
            "maxItems": 10,
            "default": [],
        },
    }
    required = ["brief"]
    if duration is not None:
        properties["durationSeconds"] = {"type": "integer", "const": duration}
        required.append("durationSeconds")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def output_schema(media: list[tuple[str, str]]) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["deliverables", "verification", "provenance", "deliveryReceipt"],
        "properties": {
            "deliverables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "type", "contentType", "sha256", "downloadUrl"],
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"enum": [kind for kind, _ in media]},
                        "contentType": {"enum": [content for _, content in media]},
                        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                        "downloadUrl": {"type": "string", "format": "uri"},
                    },
                },
            },
            "verification": {"type": "object"},
            "provenance": {"type": "object"},
            "deliveryReceipt": {"type": "object"},
        },
    }


SERVICE_DEFINITIONS = [
    {
        "slug": "arabic-product-image",
        "name": "Arabic Product Image",
        "short_description": "Verified product imagery designed for Arabic-language campaigns.",
        "long_description": "Generate one or more product-first images with Arabic creative constraints, durable provenance, and deterministic dimension checks.",
        "category": "IMAGE",
        "modalities": ["image"],
        "input_schema": input_schema(modalities=["image"]),
        "output_schema": output_schema([("image", "image/png")]),
        "base_price_minor": 225,
        "minimum_price_minor": 225,
        "maximum_price_minor": 900,
        "delivery_min": 60,
        "delivery_max": 180,
        "maximum_retries": 2,
        "verification": ["dimensions", "required objects", "forbidden objects", "prompt alignment"],
    },
    {
        "slug": "product-video-15s",
        "name": "Fifteen-Second Product Video",
        "short_description": "A verified 15-second product video with durable media evidence.",
        "long_description": "Generate a fixed-duration product video and verify duration, resolution, aspect ratio, visibility, and prohibited elements.",
        "category": "VIDEO",
        "modalities": ["image", "video"],
        "input_schema": input_schema(modalities=["image", "video"], duration=15),
        "output_schema": output_schema([("video", "video/mp4")]),
        "base_price_minor": 400,
        "minimum_price_minor": 400,
        "maximum_price_minor": 1000,
        "delivery_min": 180,
        "delivery_max": 600,
        "maximum_retries": 2,
        "verification": [
            "duration",
            "resolution",
            "aspect ratio",
            "product visibility",
            "prohibited elements",
        ],
    },
    {
        "slug": "arabic-voice-over",
        "name": "Arabic Voice-Over",
        "short_description": "Arabic narration with transcription and language verification.",
        "long_description": "Produce Arabic narration while checking language, required phrases, forbidden claims, transcription, and duration.",
        "category": "AUDIO",
        "modalities": ["voice"],
        "input_schema": input_schema(modalities=["voice"]),
        "output_schema": output_schema([("voice", "audio/wav")]),
        "base_price_minor": 125,
        "minimum_price_minor": 125,
        "maximum_price_minor": 650,
        "delivery_min": 60,
        "delivery_max": 240,
        "maximum_retries": 2,
        "verification": [
            "Arabic language",
            "transcription",
            "required phrases",
            "forbidden claims",
            "duration",
        ],
    },
    {
        "slug": "verified-vertical-ad",
        "name": "Complete Vertical Advertisement",
        "short_description": "A verified vertical advertisement with media, evidence, provenance, and a signed receipt.",
        "long_description": "Thikra's flagship service produces a 15-second 9:16 advertisement, thumbnail, Arabic narration transcript, generation manifest, verification report, and signed payment-to-delivery receipt.",
        "category": "MULTIMODAL",
        "modalities": ["image", "video", "voice", "music"],
        "input_schema": input_schema(modalities=["image", "video", "voice", "music"], duration=15),
        "output_schema": output_schema(
            [
                ("video", "video/mp4"),
                ("thumbnail", "image/png"),
                ("transcript", "text/plain"),
                ("manifest", "application/json"),
                ("verification_report", "application/json"),
            ]
        ),
        "base_price_minor": 500,
        "minimum_price_minor": 500,
        "maximum_price_minor": 1000,
        "delivery_min": 180,
        "delivery_max": 600,
        "maximum_retries": 2,
        "verification": [
            "duration",
            "resolution",
            "aspect ratio",
            "Arabic narration",
            "product visibility",
            "claims",
            "likeness",
            "provenance",
        ],
    },
    {
        "slug": "media-compliance-check",
        "name": "Existing Media Compliance Check",
        "short_description": "Verification and evidence for an existing image, video, or audio asset.",
        "long_description": "Inspect supplied media and return a verification report and evidence package. Generation occurs only if a separate correction is explicitly ordered.",
        "category": "VERIFICATION",
        "modalities": ["image", "video", "voice"],
        "input_schema": {
            **input_schema(modalities=["image", "video", "voice"]),
            "required": ["brief", "referenceAssets"],
        },
        "output_schema": output_schema([("verification_report", "application/json")]),
        "base_price_minor": 175,
        "minimum_price_minor": 175,
        "maximum_price_minor": 600,
        "delivery_min": 30,
        "delivery_max": 180,
        "maximum_retries": 0,
        "verification": [
            "technical integrity",
            "language",
            "claims",
            "rights",
            "policy constraints",
        ],
    },
    {
        "slug": "provenance-package",
        "name": "Provenance Package",
        "short_description": "Hashes, lineage, provider metadata, and payment-linked evidence.",
        "long_description": "Package asset hashes, parent-child lineage, provider metadata, generation manifests, and an available payment reference without exposing credentials.",
        "category": "PROVENANCE",
        "modalities": ["image", "video", "voice", "music"],
        "input_schema": {
            **input_schema(modalities=["image", "video", "voice", "music"]),
            "required": ["brief", "referenceAssets"],
        },
        "output_schema": output_schema([("manifest", "application/json")]),
        "base_price_minor": 100,
        "minimum_price_minor": 100,
        "maximum_price_minor": 400,
        "delivery_min": 15,
        "delivery_max": 120,
        "maximum_retries": 0,
        "verification": [
            "asset hashes",
            "parent-child lineage",
            "provider metadata",
            "manifest integrity",
            "payment reference",
        ],
    },
]
