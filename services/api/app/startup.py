"""Application initialization and non-secret resolved configuration logging."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.mcp import mcp_app
from app.commerce.receipts import signing_key_document
from app.commerce.service import seed_commerce
from app.config import settings
from app.studio.editor import interrupt_stale_renders
from app.studio.service import interrupt_incomplete_executions
from app.thikra.database import SessionLocal, initialize_database
from app.thikra.payments import validate_prava_configuration
from app.thikra.service import seed_database


def initialize_application(logger: logging.Logger) -> None:
    if settings.app_mode.upper() in {"SANDBOX", "PRODUCTION"}:
        validate_prava_configuration()
    if settings.app_mode.upper() == "PRODUCTION":
        required = {
            "SESSION_SECRET": settings.session_secret != "demo-only-change-me",
            "PRAVA_SECRET_KEY": bool(settings.prava_secret_key),
            "PRAVA_PUBLISHABLE_KEY": bool(settings.prava_publishable_key),
            "B2_BUCKET_NAME": bool(settings.b2_bucket_name),
            "THIKRA_API_KEY_PEPPER": settings.thikra_api_key_pepper
            != "demo-api-key-pepper-change-me",
            "THIKRA_RECEIPT_SIGNING_PRIVATE_KEY": bool(settings.thikra_receipt_signing_private_key),
            "THIKRA_WEBHOOK_SIGNING_SECRET": settings.thikra_webhook_signing_secret
            != "demo-webhook-secret-change-me",
        }
        missing = [name for name, valid in required.items() if not valid]
        if missing:
            raise RuntimeError(f"Production configuration is incomplete: {', '.join(missing)}")
    initialize_database()
    signing_key_document()
    with SessionLocal() as db:
        seed_database(db)
        seed_commerce(db)
        interrupted = interrupt_incomplete_executions(db)
        interrupted_renders = interrupt_stale_renders(db)
    logger.info(
        "api startup",
        extra={
            "app_mode": settings.app_mode.upper(),
            "b2_region": settings.b2_region,
            "b2_bucket": settings.b2_bucket_name,
            "chat_model": settings.chat_model,
            "image_model": settings.image_model,
            "video_provider": settings.video_provider,
            "video_model": settings.video_model,
            "gmi_video_model": settings.gmi_video_model,
            "tts_model": settings.tts_model,
            "music_model": settings.music_model,
            "cors_origins": settings.cors_origins,
            "studio_executions_interrupted": interrupted,
            "studio_renders_interrupted": interrupted_renders,
            "providers_configured": {
                "openai": bool(settings.openai_api_key),
                "replicate": bool(settings.replicate_api_token),
                "google": bool(settings.google_api_key),
                "nvidia": bool(settings.nvidia_api_key),
                "decart": bool(settings.decart_api_key),
                "gmi": bool(settings.gmi_api_key),
                "runway": bool(settings.runway_api_secret),
                "luma": bool(settings.luma_api_key),
                "elevenlabs": bool(settings.elevenlabs_api_key),
                "lmnt": bool(settings.lmnt_api_key),
                "hume": bool(settings.hume_api_key),
            },
        },
    )


@asynccontextmanager
async def application_lifespan(_app: FastAPI):
    initialize_application(logging.getLogger("api.main"))
    async with mcp_app.router.lifespan_context(mcp_app):
        yield
