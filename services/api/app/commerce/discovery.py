"""Public machine-readable discovery documents for the Agent Gateway."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.commerce.receipts import signing_key_document
from app.commerce.service import active_services, serialize_service
from app.config import settings
from app.thikra.database import get_db

router = APIRouter(tags=["Agent Discovery"])


def _urls() -> dict[str, str]:
    api = settings.thikra_api_base_url.rstrip("/")
    web = settings.public_web_url.rstrip("/")
    return {
        "api": api,
        "web": web,
        "rest": f"{api}/api/v1",
        "mcp": f"{api}/mcp",
        "openapi": f"{api}/openapi.json",
        "agent_card": f"{api}/.well-known/agent-card.json",
        "signing_keys": f"{api}/.well-known/thikra-signing-keys.json",
    }


@router.get("/.well-known/thikra-services.json")
def service_manifest(db: Session = Depends(get_db)):
    urls = _urls()
    return {
        "schema_version": "1.0",
        "platform": {
            "name": "Thikra",
            "description": "Verified creative services for human and software-agent buyers",
        },
        "base_url": urls["api"],
        "api_version": "v1",
        "authentication": [
            {
                "type": "http_bearer",
                "scheme": "Thikra API key",
                "prefixes": ["thikra_test_", "thikra_live_"],
            }
        ],
        "interfaces": {
            "rest": urls["rest"],
            "mcp_streamable_http": urls["mcp"],
            "openapi": urls["openapi"],
            "agent_card": urls["agent_card"],
        },
        "services": [serialize_service(item) for item in active_services(db)],
        "payments": {
            "provider": "Prava",
            "direction": "CUSTOMER_TO_THIKRA",
            "human_approval_required": True,
            "demo_payment_simulated": settings.app_mode.upper() == "DEMO",
        },
        "supported_currencies": [settings.thikra_default_currency],
        "delivery_modes": ["authenticated_signed_url", "webhook_notification", "polling", "sse"],
        "webhooks": {"supported": True, "signature": "HMAC-SHA256"},
        "public_signing_keys": urls["signing_keys"],
    }


@router.get("/.well-known/agent-card.json")
def agent_card():
    """A2A 1.0 Agent Card for Thikra's HTTP+JSON Agent Gateway.

    Thikra does not claim the optional A2A JSON-RPC task protocol; the declared
    HTTP+JSON interface is the documented, versioned commerce API.
    """
    urls = _urls()
    return {
        "name": "Thikra Agent-Accessible Creative Services",
        "description": "Quote, purchase, fulfill, and retrieve verified multimodal creative work.",
        "supportedInterfaces": [
            {
                "url": urls["rest"],
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }
        ],
        "provider": {"organization": "Thikra", "url": urls["web"]},
        "version": "1.0.0",
        "documentationUrl": f"{urls['web']}/developers",
        "capabilities": {
            "streaming": True,
            "pushNotifications": True,
            "extendedAgentCard": False,
        },
        "securitySchemes": {
            "thikraApiKey": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "Bearer-scoped Thikra API key",
            }
        },
        "security": [{"thikraApiKey": []}],
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": [
            "application/json",
            "image/png",
            "video/mp4",
            "audio/wav",
        ],
        "skills": [
            {
                "id": "verified-creative-commerce",
                "name": "Verified creative commerce",
                "description": "Discover services, obtain deterministic quotes, create paid orders, and retrieve verified deliverables and provenance.",
                "tags": [
                    "creative-services",
                    "quotation",
                    "payment-authorization",
                    "multimodal-generation",
                    "verification",
                    "provenance",
                    "disputes",
                ],
                "examples": [
                    "Create a verified 15-second Arabic vertical advertisement under a fixed budget."
                ],
                "inputModes": ["application/json", "text/plain"],
                "outputModes": ["application/json", "image/png", "video/mp4", "audio/wav"],
            }
        ],
        "extensions": [
            {
                "uri": "https://thikra.example/extensions/mcp",
                "required": False,
                "params": {"endpoint": urls["mcp"]},
            },
            {
                "uri": "https://thikra.example/extensions/rest",
                "required": True,
                "params": {"openapi": urls["openapi"]},
            },
        ],
    }


@router.get("/.well-known/ucp")
def ucp_profile():
    urls = _urls()
    extension = "space.thikra.creative.quote"
    return {
        "ucp": {
            "version": "2026-04-08",
            "services": {
                "space.thikra.rest": {"version": "1.0", "endpoint": urls["rest"]},
                "space.thikra.mcp": {"version": "1.0", "endpoint": urls["mcp"]},
            },
            "capabilities": {
                extension: [
                    {
                        "version": "1.0",
                        "spec": f"{urls['web']}/developers/rest",
                        "schema": f"{urls['api']}/.well-known/thikra-services.json",
                    }
                ]
            },
            "payment_handlers": {
                "space.prava.human_authorized_payment": [
                    {
                        "version": "1.0",
                        "config": {
                            "merchant": settings.thikra_merchant_name,
                            "human_approval_required": True,
                            "credential_transport": "server_only",
                        },
                    }
                ]
            },
        },
        "thikra_extension_note": (
            "UCP does not define quoted asynchronous creative fulfillment; "
            f"{extension} is a namespaced extension, not a standard checkout capability."
        ),
    }


@router.get("/.well-known/thikra-signing-keys.json")
def signing_keys():
    return signing_key_document()


@router.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt():
    urls = _urls()
    return "\n".join(
        [
            "# Thikra Agent-Accessible Creative Services",
            "Developer navigation only; this file grants no authorization.",
            f"Service manifest: {urls['api']}/.well-known/thikra-services.json",
            f"Agent Card: {urls['agent_card']}",
            f"UCP profile: {urls['api']}/.well-known/ucp",
            f"OpenAPI: {urls['openapi']}",
            f"MCP Streamable HTTP: {urls['mcp']}",
            f"Developer guide: {urls['web']}/developers",
        ]
    )
