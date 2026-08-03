"""Credential-Manager-backed optional B2 connection for local Studio projects."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache

from genblaze_s3 import S3StorageBackend

from app.config import settings

KEYRING_SERVICE = "thikra-studio-storage"
KEYRING_ACCOUNT = "backblaze-b2"


@dataclass(frozen=True)
class B2Configuration:
    region: str
    key_id: str
    application_key: str
    bucket_name: str
    source: str

    @property
    def configured(self) -> bool:
        return all((self.region, self.key_id, self.application_key, self.bucket_name))


def _personal() -> B2Configuration | None:
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if not value:
            return None
        payload = json.loads(value)
        candidate = B2Configuration(
            region=str(payload.get("region", "")).strip(),
            key_id=str(payload.get("key_id", "")).strip(),
            application_key=str(payload.get("application_key", "")).strip(),
            bucket_name=str(payload.get("bucket_name", "")).strip(),
            source="personal",
        )
        return candidate if candidate.configured else None
    except Exception:
        return None


def effective_b2_configuration() -> B2Configuration | None:
    personal = _personal()
    if personal:
        return personal
    environment = B2Configuration(
        region=settings.b2_region.strip(),
        key_id=settings.b2_key_id.strip(),
        application_key=settings.b2_application_key.strip(),
        bucket_name=settings.b2_bucket_name.strip(),
        source="environment",
    )
    return environment if environment.configured else None


def storage_connection_status() -> dict:
    configuration = effective_b2_configuration()
    return {
        "mode": "b2" if configuration else "local",
        "configured": bool(configuration),
        "source": configuration.source if configuration else "none",
        "region": configuration.region if configuration else "",
        "bucket_name": configuration.bucket_name if configuration else "",
        "key_id_hint": f"…{configuration.key_id[-4:]}" if configuration else "",
    }


def set_storage_connection(
    *, region: str, key_id: str, application_key: str, bucket_name: str
) -> None:
    configuration = B2Configuration(
        region=region.strip(),
        key_id=key_id.strip(),
        application_key=application_key.strip(),
        bucket_name=bucket_name.strip(),
        source="personal",
    )
    if not configuration.configured:
        raise ValueError("Region, key ID, application key, and bucket name are required")
    import keyring

    payload = asdict(configuration)
    payload.pop("source", None)
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, json.dumps(payload))
    _clear_backend_cache()


def clear_storage_connection() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        pass
    _clear_backend_cache()


@lru_cache(maxsize=1)
def studio_backend() -> S3StorageBackend | None:
    configuration = effective_b2_configuration()
    if configuration is None:
        return None
    return S3StorageBackend.for_backblaze(
        configuration.bucket_name,
        region=configuration.region,
        key_id=configuration.key_id,
        app_key=configuration.application_key,
        auto_lifecycle=True,
    )


def studio_presign_asset_url(key_or_url: str, *, expires_in: int = 900) -> str:
    storage = studio_backend()
    if storage is None:
        raise RuntimeError(
            "This provider must fetch a local reference. Connect Backblaze B2 in Studio settings first."
        )
    if key_or_url.startswith("http"):
        key = storage.key_from_url(key_or_url)
        if key is None:
            raise ValueError(f"Unrecognized Studio B2 asset URL: {key_or_url}")
    else:
        key = key_or_url
    return storage.get_url(key, expires_in=expires_in)


def _clear_backend_cache() -> None:
    studio_backend.cache_clear()
