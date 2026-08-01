"""API-key authentication, idempotency fingerprints, and ownership context."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce.models import APIKey, DeveloperApplication, IdempotencyRecord
from app.config import settings
from app.thikra.audit import canonical_json


class AuthenticationError(ValueError):
    pass


class AuthorizationError(ValueError):
    pass


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class AuthContext:
    key_id: str
    application_id: str
    principal_id: str
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        if scope not in self.scopes and "*" not in self.scopes:
            raise AuthorizationError(f"API key is missing required scope: {scope}")


def hash_secret(secret: str) -> str:
    return hmac.new(
        settings.thikra_api_key_pepper.encode(), secret.encode(), hashlib.sha256
    ).hexdigest()


def issue_api_key(
    db: Session,
    application: DeveloperApplication,
    *,
    name: str,
    scopes: list[str],
    expires_at: datetime | None = None,
    environment: str | None = None,
) -> tuple[APIKey, str]:
    env = environment or ("live" if settings.app_mode.upper() == "PRODUCTION" else "test")
    secret = f"thikra_{env}_{secrets.token_urlsafe(32)}"
    key = APIKey(
        application_id=application.id,
        prefix=secret[:20],
        hashed_secret=hash_secret(secret),
        name=name,
        scopes_json=canonical_json(sorted(set(scopes))),
        expires_at=expires_at,
    )
    db.add(key)
    db.flush()
    return key, secret


def authenticate_api_key(db: Session, token: str, scope: str | None = None) -> AuthContext:
    if not token.startswith(("thikra_test_", "thikra_live_")):
        raise AuthenticationError("Bearer token is not a Thikra API key")
    candidates = list(db.scalars(select(APIKey).where(APIKey.prefix == token[:20])))
    digest = hash_secret(token)
    matched = next(
        (row for row in candidates if hmac.compare_digest(row.hashed_secret, digest)), None
    )
    now = datetime.now(UTC)
    if matched is None or matched.revoked_at is not None:
        raise AuthenticationError("API key is invalid or revoked")
    expires_at = matched.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise AuthenticationError("API key has expired")
    application = db.get(DeveloperApplication, matched.application_id)
    if application is None or application.status != "ACTIVE":
        raise AuthenticationError("Developer application is not active")
    matched.last_used_at = now
    scopes = frozenset(json.loads(matched.scopes_json))
    context = AuthContext(matched.id, application.id, application.owner_principal_id, scopes)
    if scope:
        context.require(scope)
    return context


def request_fingerprint(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def idempotent_result(
    db: Session,
    auth: AuthContext,
    operation: str,
    key: str,
    payload: object,
) -> dict | None:
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.application_id == auth.application_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        return None
    if not hmac.compare_digest(record.request_hash, request_fingerprint(payload)):
        raise IdempotencyConflict("Idempotency-Key was reused with a different request")
    return json.loads(record.response_json)


def remember_idempotent_result(
    db: Session,
    auth: AuthContext,
    operation: str,
    key: str,
    payload: object,
    resource_type: str,
    resource_id: str,
    response: dict,
) -> None:
    db.add(
        IdempotencyRecord(
            application_id=auth.application_id,
            operation=operation,
            key=key,
            request_hash=request_fingerprint(payload),
            resource_type=resource_type,
            resource_id=resource_id,
            response_json=canonical_json(response),
        )
    )
