"""Canonical Ed25519 payment-to-delivery receipts."""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.config import settings
from app.thikra.audit import canonical_json

DEVELOPMENT_SEED = hashlib.sha256(b"thikra-development-receipt-key-v1").digest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def private_key() -> Ed25519PrivateKey:
    configured = settings.thikra_receipt_signing_private_key.strip()
    if not configured:
        if settings.app_mode.upper() == "PRODUCTION":
            raise RuntimeError("THIKRA_RECEIPT_SIGNING_PRIVATE_KEY is required in production")
        return Ed25519PrivateKey.from_private_bytes(DEVELOPMENT_SEED)
    if configured.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(configured.encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError("Receipt signing key must be Ed25519")
        return key
    raw = _decode(configured)
    if settings.app_mode.upper() == "PRODUCTION" and raw == DEVELOPMENT_SEED:
        raise RuntimeError("Development receipt signing key is forbidden in production")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_bytes() -> bytes:
    derived = (
        private_key()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    configured = settings.thikra_receipt_signing_public_key.strip()
    if configured and not hmac.compare_digest(_decode(configured), derived):
        raise RuntimeError("Configured receipt public key does not match the private key")
    return derived


def sign_receipt(payload: dict) -> tuple[str, str]:
    encoded = canonical_json(payload).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return digest, _b64url(private_key().sign(encoded))


def verify_receipt(payload: dict, receipt_hash: str, signature: str) -> bool:
    encoded = canonical_json(payload).encode()
    if not hmac.compare_digest(hashlib.sha256(encoded).hexdigest(), receipt_hash):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes()).verify(_decode(signature), encoded)
    except (ValueError, TypeError):
        return False
    return True


def signing_key_document() -> dict:
    return {
        "keys": [
            {
                "kid": settings.thikra_receipt_signing_key_id,
                "kty": "OKP",
                "crv": "Ed25519",
                "alg": "EdDSA",
                "use": "sig",
                "x": _b64url(public_key_bytes()),
                "development_only": not bool(settings.thikra_receipt_signing_private_key),
            }
        ]
    }
