from __future__ import annotations

import sys
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.commerce import models as commerce_models  # noqa: F401
from app.studio import service, storage_connection
from app.thikra.database import Base
from app.thikra.models import Workspace


def _database() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Workspace(name="Packaged Test", environment="DEMO"))
    session.commit()
    return session


def test_generated_provider_asset_is_ingested_locally(tmp_path, monkeypatch) -> None:
    db = _database()
    project = service.create_project(
        db, name="Local", description="", budget=500, currency="USD", graph=None
    )
    source = tmp_path / "provider.png"
    source.write_bytes(b"provider-output")
    monkeypatch.setattr(service.settings, "thikra_data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(storage_connection, "studio_backend", lambda: None)

    [descriptor] = service._persist_asset_descriptors(
        db,
        project.id,
        "image",
        [{"url": str(source), "media_type": "image/png"}],
    )
    record = db.get(service.StudioAsset, descriptor["id"])
    assert record.local_path
    assert record.remote_url is None
    assert record.sha256 == "388a15c59263a6c447d148fc1085ff9b987cfa4bd8aa13e39dcf3d70d480b0ea"
    assert record.size == len(b"provider-output")


def test_storage_credentials_never_return_plaintext(monkeypatch) -> None:
    for field in ("b2_region", "b2_key_id", "b2_application_key", "b2_bucket_name"):
        monkeypatch.setattr(storage_connection.settings, field, "")
    values: dict[tuple[str, str], str] = {}
    fake_keyring = SimpleNamespace(
        get_password=lambda service_name, account: values.get((service_name, account)),
        set_password=lambda service_name, account, value: values.__setitem__(
            (service_name, account), value
        ),
        delete_password=lambda service_name, account: values.pop((service_name, account), None),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    storage_connection.set_storage_connection(
        region="us-west-004",
        key_id="key-id-1234",
        application_key="super-secret-value",
        bucket_name="studio-bucket",
    )
    status = storage_connection.storage_connection_status()
    assert status == {
        "mode": "b2",
        "configured": True,
        "source": "personal",
        "region": "us-west-004",
        "bucket_name": "studio-bucket",
        "key_id_hint": "…1234",
    }
    assert "super-secret-value" not in str(status)
    storage_connection.clear_storage_connection()
    assert storage_connection.storage_connection_status()["mode"] == "local"
