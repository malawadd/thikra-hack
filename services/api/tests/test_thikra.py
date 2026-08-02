"""Thikra policy, persistence, payment, verification, and workflow tests."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.thikra.api as thikra_api
from app.commerce import models as commerce_models  # noqa: F401
from app.config import settings
from app.thikra.api import router
from app.thikra.audit import append_event, verify_chain
from app.thikra.database import Base, get_db
from app.thikra.models import AuditEvent, GenerationRun, Mandate, MandateVersion
from app.thikra.payments import PravaPaymentGateway, sanitize_payment_result
from app.thikra.schemas import BriefCreate, CreativeMandate
from app.thikra.service import (
    compile_brief,
    confirm_mandate,
    materialize_demo_delivery,
    provider_strategy,
    seed_database,
    workspace,
)
from app.thikra.state_machine import InvalidTransition, retry_allowed, transition
from app.thikra.storage import LocalEvidenceStorage
from app.thikra.verification import inspect_file


def brief_payload() -> dict:
    return {
        "campaign_name": "Noura Glow verification",
        "product": "Noura Glow skincare",
        "objective": "Create accountable vertical product advertisements.",
        "target_audience": "Young adults in Saudi Arabia",
        "language": "Arabic",
        "tone": "Warm and modern",
        "creative_brief": "Show the product without people or unsupported medical claims.",
        "deliverables": [
            {
                "modality": "combined_advertisement",
                "variants": 3,
                "duration_sec": 15,
                "aspect_ratio": "9:16",
                "resolution": "1080x1920",
                "file_format": "mp4",
            }
        ],
        "maximum_budget_minor": 2000,
        "currency": "USD",
        "deadline": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "maximum_retries": 2,
        "permitted_providers": [],
        "forbidden_providers": [],
        "human_approval_threshold_minor": 1000,
        "commercial_use_required": True,
        "likeness_restrictions": "No real-person or celebrity likenesses",
        "forbidden_elements": ["medical claims"],
        "required_elements": ["product visible"],
        "claim_constraints": ["No treatment claims"],
        "required_language": "Arabic",
        "attribution_requirements": ["provider and model"],
    }


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_mode", "DEMO")
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        yield session


@pytest.fixture
def client(db: Session):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


def test_mandate_validation_rejects_float_and_unknown_fields():
    request = BriefCreate.model_validate(brief_payload())
    assert request.maximum_budget_minor == 2000
    invalid = brief_payload() | {"maximum_budget_minor": 99}
    with pytest.raises(ValidationError):
        BriefCreate.model_validate(invalid)
    with pytest.raises(ValidationError):
        CreativeMandate.model_validate({"unexpected": True})


def test_mandate_versioning_and_confirmation(db: Session, client: TestClient):
    compiled = compile_brief(db, BriefCreate.model_validate(brief_payload()))
    mandate = compiled["mandate"]
    mandate["budget_cap_minor"] = 1900
    response = client.put(
        f"/thikra/mandates/{compiled['mandate_id']}",
        json={"mandate": mandate, "edit_summary": "Principal lowered the budget"},
    )
    assert response.status_code == 200
    confirmed = client.post(f"/thikra/mandates/{compiled['mandate_id']}/confirm")
    assert confirmed.status_code == 200
    model = db.get(Mandate, compiled["mandate_id"])
    assert model.current_version == 2
    versions = list(db.scalars(select(MandateVersion).where(MandateVersion.mandate_id == model.id)))
    assert [version.confirmed for version in versions] == [False, True]


def test_provider_scores_and_budget_arithmetic(db: Session):
    compiled = compile_brief(db, BriefCreate.model_validate(brief_payload()))
    confirm_mandate(db, compiled["mandate_id"])
    strategy = provider_strategy(db, compiled["mandate_id"])
    assert set(strategy["selection"]) == {"chat", "image", "video", "tts", "music"}
    assert all(isinstance(quote["estimated_cost_minor"], int) for quote in strategy["quotes"])
    assert strategy["quotes"][0]["estimated_cost"] is True


def test_permitted_providers_and_zero_retry_budget_are_enforced(db: Session):
    payload = brief_payload() | {
        "maximum_retries": 0,
        "permitted_providers": ["openai", "gmicloud"],
    }
    compiled = compile_brief(db, BriefCreate.model_validate(payload))
    assert compiled["mandate"]["retry_budget_minor"] == 0
    confirm_mandate(db, compiled["mandate_id"])
    strategy = provider_strategy(db, compiled["mandate_id"])
    assert {choice["vendor"] for choice in strategy["selection"].values()} <= {
        "openai",
        "gmicloud",
    }


def test_zero_value_sandbox_verification_can_request_fresh_session(
    db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class FakePravaGateway:
        def __init__(self):
            self.created: list[dict] = []
            self.revoked: list[str] = []

        async def create_authorization(self, request: dict) -> dict:
            self.created.append(request)
            suffix = len(self.created)
            return {
                "session_id": f"sandbox-session-{suffix}",
                "session_token": f"sandbox-token-{suffix}",
                "iframe_url": f"https://sandbox.collect.prava.space/session/{suffix}",
                "order_id": f"sandbox-order-{suffix}",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
            }

        async def get_authorization(self, session_id: str) -> dict:
            return {
                "session_id": session_id,
                "status": "completed",
                "transactions": [
                    {
                        "status": "completed",
                        "line_items": [
                            {
                                "txn_ref_id": "txn-zero",
                                "token": "sandbox-network-token",
                                "dynamic_cvv": "000",
                                "expiry_month": "12",
                                "expiry_year": "2027",
                            }
                        ],
                    }
                ],
            }

        async def revoke(self, session_id: str) -> dict:
            self.revoked.append(session_id)
            return {"success": True, "session_id": session_id}

    fake_gateway = FakePravaGateway()
    monkeypatch.setattr(settings, "app_mode", "SANDBOX")
    monkeypatch.setattr(thikra_api, "gateway", lambda: fake_gateway)
    compiled = compile_brief(db, BriefCreate.model_validate(brief_payload()))
    confirm_mandate(db, compiled["mandate_id"])
    base_request = {
        "mandate_id": compiled["mandate_id"],
        "merchant": "GMICloud creative services",
        "merchant_url": "https://console.gmicloud.ai",
        "maximum_amount_minor": 0,
        "estimated_amount_minor": 0,
        "retry_reserve_minor": 0,
        "verification_only": True,
        "currency": "USD",
    }
    first = client.post(
        "/thikra/payments/authorizations",
        json=base_request | {"idempotency_key": "zero-verification-1"},
    )
    assert first.status_code == 201
    assert first.json()["checkout"]["verification_only"] is True
    assert fake_gateway.created[0]["maximum_amount_minor"] == 0

    polled = client.post(f"/thikra/payments/{first.json()['id']}/poll", json={})
    assert polled.json()["payment"]["authorization_state"] == "VERIFIED"
    assert polled.json()["credential_available_server_side"] is False
    blocked_run = client.post(
        "/thikra/runs",
        json={
            "mandate_id": compiled["mandate_id"],
            "payment_id": first.json()["id"],
            "provider_selection": {},
            "idempotency_key": "zero-verification-run",
        },
    )
    assert blocked_run.status_code == 409

    assert client.post(f"/thikra/payments/{first.json()['id']}/revoke").status_code == 200
    second = client.post(
        "/thikra/payments/authorizations",
        json=base_request | {"idempotency_key": "zero-verification-2"},
    )
    assert second.status_code == 201
    assert second.json()["external_session_id"] != first.json()["external_session_id"]
    assert fake_gateway.revoked == [first.json()["external_session_id"]]


def test_prava_zero_dollar_diagnostics_never_expose_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class FakePravaGateway:
        def __init__(self):
            self.created: list[dict] = []

        async def health(self) -> dict:
            return {"status": "ok"}

        async def create_authorization(self, request: dict) -> dict:
            self.created.append(request)
            suffix = len(self.created)
            return {
                "session_id": f"sess_diagnostic_{suffix}",
                "session_token": f"sandbox-session-token-{suffix}",
                "iframe_url": f"https://sandbox.collect.prava.space/session/diagnostic-{suffix}",
                "order_id": f"ord_diagnostic_{suffix}",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
            }

        async def get_authorization(self, session_id: str) -> dict:
            return {
                "session_id": session_id,
                "status": "completed",
                "transactions": [
                    {
                        "status": "completed",
                        "line_items": [
                            {
                                "txn_ref_id": "txn-diagnostic",
                                "token": "one-time-network-token",
                                "dynamic_cvv": "123",
                                "expiry_month": "12",
                                "expiry_year": "2030",
                            }
                        ],
                    }
                ],
            }

        async def revoke(self, session_id: str) -> dict:
            return {"success": True, "session_id": session_id}

    fake_gateway = FakePravaGateway()
    monkeypatch.setattr(settings, "app_mode", "SANDBOX")
    monkeypatch.setattr(settings, "prava_backend_url", "https://sandbox.api.prava.space")
    monkeypatch.setattr(settings, "prava_secret_key", "sk_test_server_only")
    monkeypatch.setattr(settings, "prava_publishable_key", "pk_test_browser_safe")
    monkeypatch.setattr(settings, "prava_test_user_email", "tester@gmail.com")
    monkeypatch.setattr(settings, "public_web_url", "https://thikratest.mukaeb.com")
    monkeypatch.setattr(settings, "thikra_merchant_url", "")
    monkeypatch.setattr(thikra_api, "gateway", lambda: fake_gateway)

    diagnostics = client.get("/thikra/prava-test")
    assert diagnostics.status_code == 200
    assert diagnostics.json()["ready"] is True
    assert diagnostics.json()["merchant_url"] == "https://thikratest.mukaeb.com"
    assert diagnostics.json()["merchant_country_code"] == "SA"
    assert diagnostics.json()["test_user_email_configured"] is True

    created = client.post("/thikra/prava-test/session")
    assert created.status_code == 201
    assert fake_gateway.created[0]["maximum_amount_minor"] == 0
    assert fake_gateway.created[0]["verification_only"] is True
    assert fake_gateway.created[0]["user_email"] == "tester@gmail.com"
    assert fake_gateway.created[0]["integration_type"] == "embedding"
    assert fake_gateway.created[0]["merchant_url"] == "https://thikratest.mukaeb.com"
    assert created.json()["integration_type"] == "embedding"

    hosted = client.post(
        "/thikra/prava-test/session?integration_type=full_checkout"
    )
    assert hosted.status_code == 201
    assert hosted.json()["integration_type"] == "full_checkout"
    assert hosted.json()["session_id"] != created.json()["session_id"]
    assert fake_gateway.created[1]["integration_type"] == "full_checkout"

    polled = client.post("/thikra/prava-test/session/sess_diagnostic_1/poll")
    assert polled.status_code == 200
    assert polled.json()["credential_generated"] is True
    encoded = json.dumps(polled.json())
    assert "one-time-network-token" not in encoded
    assert "dynamic_cvv" not in encoded

    revoked = client.post("/thikra/prava-test/session/sess_diagnostic_1/revoke")
    assert revoked.json() == {"success": True, "session_id": "sess_diagnostic_1"}

    monkeypatch.setattr(settings, "prava_test_user_email", "tester@thikra.demo")
    invalid_email = client.get("/thikra/prava-test")
    assert invalid_email.json()["ready"] is False
    assert invalid_email.json()["test_user_email_configured"] is False
    assert "PRAVA_TEST_USER_EMAIL" in " ".join(invalid_email.json()["issues"])


def test_prava_session_body_uses_configured_country_and_integration_type(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "public_web_url", "https://thikratest.mukaeb.com")
    monkeypatch.setattr(settings, "thikra_merchant_country_code", "SA")
    body = PravaPaymentGateway()._build_session_body(
        {
            "mandate_id": "prava-zero-dollar-diagnostic",
            "merchant": "Thikra Prava sandbox verification",
            "merchant_url": "https://thikratest.mukaeb.com",
            "maximum_amount_minor": 0,
            "currency": "USD",
            "user_id": "thikra-prava-test-user",
            "user_email": "tester@gmail.com",
            "idempotency_key": "prava-zero-test-unit",
            "integration_type": "full_checkout",
        }
    )

    assert body["integration_type"] == "full_checkout"
    assert body["callback_url"] == "https://thikratest.mukaeb.com/payments"
    assert body["purchase_context"][0]["merchant_details"] == {
        "name": "Thikra Prava sandbox verification",
        "url": "https://thikratest.mukaeb.com",
        "country_code_iso2": "SA",
        "category_code": "7399",
        "category": "Business services",
    }


def test_state_machine_and_retry_limits():
    run = GenerationRun(
        workspace_id="w",
        brief_id="b",
        mandate_id="m",
        mandate_version=1,
        campaign_name="test",
        status="PLANNING",
        current_stage="Planning",
        currency="USD",
        budget_cap_minor=1000,
        authorized_minor=900,
        spent_minor=700,
        retry_reserved_minor=200,
        retry_count=0,
        maximum_retries=2,
        provider_selection_json="{}",
        idempotency_key="state-test",
    )
    transition(run, "GENERATING")
    assert run.status == "GENERATING"
    with pytest.raises(InvalidTransition):
        transition(run, "COMPLETED")
    assert retry_allowed(run, 200)[0] is True
    assert retry_allowed(run, 201) == (False, "Retry would exceed the authorized amount")
    run.retry_count = 2
    assert retry_allowed(run, 1) == (False, "Maximum retry count reached")


def test_payment_credentials_are_sanitized():
    payload = {
        "status": "completed",
        "transactions": [
            {
                "line_items": [
                    {
                        "txn_ref_id": "txn-1",
                        "token": "one-time-token",
                        "dynamic_cvv": "123",
                        "expiry_month": "12",
                        "expiry_year": "2030",
                        "merchant": "Provider",
                    }
                ]
            }
        ],
    }
    sanitized, credentials = sanitize_payment_result(payload)
    encoded = json.dumps(sanitized)
    assert "one-time-token" not in encoded and "dynamic_cvv" not in encoded
    assert credentials[0]["token"] == "one-time-token"


def test_audit_hash_chain_detects_tampering(db: Session):
    ws = workspace(db)
    append_event(
        db,
        workspace_id=ws.id,
        run_id=None,
        event_type="test.one",
        actor_type="SYSTEM",
        actor_id="test",
        payload={"n": 1},
    )
    append_event(
        db,
        workspace_id=ws.id,
        run_id=None,
        event_type="test.two",
        actor_type="SYSTEM",
        actor_id="test",
        payload={"n": 2},
    )
    db.commit()
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == ws.id)
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    assert verify_chain(events)
    events[-1].payload_json = '{"n":3}'
    assert not verify_chain(events)
    db.rollback()


def test_deterministic_verification_and_asset_hash(tmp_path: Path):
    video = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=90x160:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    checks = inspect_file(
        video,
        expected_resolution=(90, 160),
        expected_aspect_ratio=(9, 16),
        expected_duration_sec=1,
        require_audio=True,
    )
    assert checks and all(check["status"] == "PASS" for check in checks)
    assert (
        len(
            next(check for check in checks if check["check_name"] == "SHA-256")["evidence"][
                "sha256"
            ]
        )
        == 64
    )


def test_local_evidence_storage_isolated(tmp_path: Path):
    storage = LocalEvidenceStorage(tmp_path)
    key = "thikra/workspaces/w/runs/r/evidence/export.json"
    assert storage.put_json(key, {"amount_minor": 2000}) == f"local://{key}"
    assert json.loads((tmp_path / key).read_text(encoding="utf-8"))["amount_minor"] == 2000
    with pytest.raises(ValueError):
        storage.put_json("../escape.json", {})


def test_complete_demo_workflow_and_case(db: Session, client: TestClient):
    compiled = client.post("/thikra/briefs/compile", json=brief_payload()).json()
    assert client.post(f"/thikra/mandates/{compiled['mandate_id']}/confirm").status_code == 200
    strategy = client.post(
        "/thikra/providers/strategy", json={"mandate_id": compiled["mandate_id"]}
    ).json()
    payment = client.post(
        "/thikra/payments/authorizations",
        json={
            "mandate_id": compiled["mandate_id"],
            "merchant": "Demo provider",
            "merchant_url": "https://example.com",
            "maximum_amount_minor": 2000,
            "estimated_amount_minor": 540,
            "retry_reserve_minor": 500,
            "currency": "USD",
            "idempotency_key": f"payment-{compiled['mandate_id']}",
        },
    ).json()
    run_response = client.post(
        "/thikra/runs",
        json={
            "mandate_id": compiled["mandate_id"],
            "payment_id": payment["id"],
            "provider_selection": strategy["selection"],
            "idempotency_key": f"run-{compiled['mandate_id']}",
        },
    )
    assert run_response.status_code == 201
    run = run_response.json()
    assert run["status"] == "PLANNING" and len(run["scenes"]) == 3
    scene = run["scenes"][0]
    edited = client.put(
        f"/thikra/runs/{run['id']}/scenes/{scene['id']}",
        json={
            "prompt": scene["prompt"] + " Keep the Arabic label readable.",
            "narration": scene["narration"],
        },
    )
    assert edited.status_code == 200
    assert client.post(f"/thikra/runs/{run['id']}/start").json()["status"] == "GENERATING"
    materialize_demo_delivery(db, run["id"])
    reviewed = client.get(f"/thikra/runs/{run['id']}").json()
    assert reviewed["status"] == "HUMAN_REVIEW"
    assert any(check["status"] == "FAIL" for check in reviewed["verification"])
    retried = client.post(f"/thikra/runs/{run['id']}/retry", json={"component": "narration"}).json()
    assert retried["retry_count"] == 1
    approved = client.post(
        f"/thikra/runs/{run['id']}/approve", json={"note": "Principal approved"}
    ).json()
    assert approved["status"] == "COMPLETED" and approved["accepted"] is True
    case = client.post(
        "/thikra/cases",
        json={
            "run_id": run["id"],
            "reason": "Document the controlled narration failure",
            "severity": "LOW",
        },
    )
    assert case.status_code == 201
    resolved = client.patch(
        f"/thikra/cases/{case.json()['id']}",
        json={
            "status": "RESOLVED",
            "resolution": "Retried, reverified, and approved.",
        },
    )
    assert resolved.json()["status"] == "RESOLVED"


def test_prava_webhook_contract_is_not_fabricated(client: TestClient):
    response = client.post("/thikra/payments/webhooks/prava", content=b"{}")
    assert response.status_code == 501
    assert response.json()["code"] == "PRAVA_WEBHOOK_CONTRACT_UNDOCUMENTED"
