"""Commercial-domain and full public REST flow tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.commerce import api as commerce_api
from app.commerce import payments as commerce_payments
from app.commerce.api import router
from app.commerce.models import APIKey, CommercialOrder, FulfillmentJob, Quote, ServiceOffer
from app.commerce.payments import _aware_utc
from app.commerce.pricing import quote_breakdown
from app.commerce.rate_limit import SlidingWindowRateLimiter
from app.commerce.receipts import sign_receipt, verify_receipt
from app.commerce.security import authenticate_api_key, hash_secret
from app.commerce.service import seed_commerce
from app.commerce.state_machine import InvalidOrderTransition, transition_order
from app.commerce.webhooks import (
    signature_headers,
    validate_callback_url,
    verify_webhook_signature,
)
from app.config import settings
from app.thikra.database import Base, get_db
from app.thikra.models import GenerationRun
from app.thikra.service import seed_database

DEMO_KEY = "thikra_test_demo_local_only"


@pytest.fixture
def commerce_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_mode", "DEMO")
    monkeypatch.setattr(settings, "thikra_data_dir", str(tmp_path / "evidence"))
    monkeypatch.setattr(settings, "thikra_demo_api_key", DEMO_KEY)
    engine = create_engine(f"sqlite:///{tmp_path / 'commerce.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        seed_database(session)
        seed_commerce(session)
        yield session


@pytest.fixture
def commerce_client(commerce_db: Session):
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_db] = lambda: commerce_db
    with TestClient(application) as client:
        yield client


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {DEMO_KEY}", "Idempotency-Key": key}


def test_sandbox_seed_upgrades_only_legacy_demo_key_for_test_fulfillment(
    commerce_db: Session, monkeypatch: pytest.MonkeyPatch
):
    legacy_key = commerce_db.scalar(select(APIKey))
    assert legacy_key is not None
    legacy_key.scopes_json = json.dumps(["orders:create"])
    commerce_db.commit()
    monkeypatch.setattr(settings, "app_mode", "SANDBOX")
    monkeypatch.setattr(settings, "thikra_agent_test_fulfillment_enabled", True)

    seed_commerce(commerce_db)

    assert "orders:test" in json.loads(legacy_key.scopes_json)
    authenticate_api_key(commerce_db, DEMO_KEY, "orders:test")


def _quote_payload() -> dict:
    return {
        "service": "verified-vertical-ad",
        "input": {
            "brief": "Create a verified Arabic vertical advertisement for Noura Glow without people or medical claims.",
            "durationSeconds": 15,
            "language": "ar",
            "aspectRatio": "9:16",
            "resolution": "1080x1920",
            "requiredElements": ["Noura Glow product visible"],
            "forbiddenElements": ["real people", "medical claims"],
            "maximumRetries": 2,
        },
        "buyer_principal": {
            "type": "HUMAN",
            "display_name": "Noura buyer",
            "email": "buyer@nouraglow.sa",
        },
        "buyer_agent": {
            "name": "External test buyer",
            "framework": "scripted-mcp-client",
            "model_name": "deterministic",
            "external_agent_id": "test-agent-1",
        },
        "maximum_budget": {"amount_minor": 1000, "currency": "USD"},
    }


def test_agent_gateway_sliding_window_rate_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "thikra_rate_limit_requests", 10)
    monkeypatch.setattr(settings, "thikra_quote_rate_limit_requests", 2)
    monkeypatch.setattr(settings, "thikra_rate_limit_window_seconds", 60)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/quotes",
            "raw_path": b"/api/v1/quotes",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer isolated-rate-limit-test")],
            "client": ("127.0.0.1", 5000),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    limiter = SlidingWindowRateLimiter()
    assert limiter.check(request, now=100.0).allowed is True
    second = limiter.check(request, now=101.0)
    assert second.allowed is True
    assert second.remaining == 0
    limited = limiter.check(request, now=102.0)
    assert limited.allowed is False
    assert limited.retry_after == 58
    assert limiter.check(request, now=161.0).allowed is True


def test_sqlite_payment_expiry_is_normalized_to_utc() -> None:
    normalized = _aware_utc(datetime(2026, 8, 2, 19, 54, 34))
    assert normalized.tzinfo is UTC


def test_seeded_catalog_and_deterministic_pricing(commerce_db: Session):
    offers = list(commerce_db.scalars(select(ServiceOffer)))
    assert len(offers) == 6
    flagship = next(offer for offer in offers if offer.slug == "verified-vertical-ad")
    assert flagship.status == "ACTIVE"
    assert flagship.maximum_price_minor == 1000
    breakdown = quote_breakdown(500, 2)
    assert breakdown["total_minor"] == sum(
        breakdown[key]
        for key in (
            "provider_generation_estimate_minor",
            "verification_fee_minor",
            "storage_fee_minor",
            "retry_reserve_minor",
            "platform_fee_minor",
            "fixed_fee_minor",
            "tax_minor",
        )
    )


def test_api_key_is_hashed_and_constant_time_authenticates(commerce_db: Session):
    key = commerce_db.scalar(select(APIKey))
    assert key.hashed_secret == hash_secret(DEMO_KEY)
    assert DEMO_KEY not in key.hashed_secret
    auth = authenticate_api_key(commerce_db, DEMO_KEY)
    assert auth.application_id == key.application_id
    with pytest.raises(ValueError):
        authenticate_api_key(commerce_db, f"{DEMO_KEY}-wrong")


def test_receipt_canonicalization_and_signature():
    left = {"order_id": "one", "money": {"currency": "USD", "amount_minor": 500}}
    right = {"money": {"amount_minor": 500, "currency": "USD"}, "order_id": "one"}
    digest, signature = sign_receipt(left)
    assert verify_receipt(right, digest, signature)
    assert not verify_receipt(right | {"order_id": "two"}, digest, signature)


def test_webhook_signature_and_ssrf_policy(monkeypatch: pytest.MonkeyPatch):
    payload = {"id": "event-1"}
    secret = "whsec_test"
    timestamp = int(datetime.now(UTC).timestamp())
    headers = signature_headers("event-1", payload, secret, timestamp=timestamp)
    assert verify_webhook_signature(
        payload=payload,
        secret=secret,
        timestamp=headers["Thikra-Timestamp"],
        signature=headers["Thikra-Signature"],
    )
    assert not verify_webhook_signature(
        payload=payload | {"changed": True},
        secret=secret,
        timestamp=headers["Thikra-Timestamp"],
        signature=headers["Thikra-Signature"],
    )
    with pytest.raises(ValueError):
        validate_callback_url("https://127.0.0.1/hook")
    with pytest.raises(ValueError):
        validate_callback_url("http://example.com/hook")
    monkeypatch.setattr(settings, "thikra_webhook_development_allowlist", "hooks.example.test")
    validate_callback_url("https://hooks.example.test/thikra", resolve_dns=False)


def test_quote_expiration_and_invalid_order_transition(
    commerce_db: Session, commerce_client: TestClient
):
    response = commerce_client.post(
        "/api/v1/quotes", json=_quote_payload(), headers=_headers("quote-expiry-001")
    )
    assert response.status_code == 201, response.text
    quote = commerce_db.get(Quote, response.json()["id"])
    quote.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    commerce_db.commit()
    expired = commerce_client.get(
        f"/api/v1/quotes/{quote.id}", headers={"Authorization": f"Bearer {DEMO_KEY}"}
    )
    assert expired.json()["status"] == "EXPIRED"
    with pytest.raises(InvalidOrderTransition):
        order = CommercialOrder(status="READY")
        transition_order(
            commerce_db, order, "PAYMENT_PENDING", actor_type="SYSTEM", actor_id="test"
        )


def test_complete_external_agent_rest_flow(commerce_client: TestClient, commerce_db: Session):
    services = commerce_client.get("/api/v1/services")
    assert services.status_code == 200
    assert services.json()["total"] == 6

    quote_response = commerce_client.post(
        "/api/v1/quotes", json=_quote_payload(), headers=_headers("quote-create-001")
    )
    assert quote_response.status_code == 201, quote_response.text
    quote = quote_response.json()
    assert quote["total_minor"] <= 1000
    assert quote["status"] == "ACTIVE"

    accept = commerce_client.post(
        f"/api/v1/quotes/{quote['id']}/accept", headers=_headers("quote-accept-001")
    )
    assert accept.status_code == 200, accept.text
    assert accept.json()["status"] == "ACCEPTED"

    created = commerce_client.post(
        "/api/v1/orders",
        json={"quote_id": quote["id"], "external_reference": "agent-demo-001"},
        headers=_headers("order-create-001"),
    )
    assert created.status_code == 201, created.text
    order = created.json()
    order_id = order["id"]
    assert order["status"] == "QUOTED"
    assert order["paid_total_minor"] == 0

    authorization = commerce_client.post(
        f"/api/v1/orders/{order_id}/payment-authorization",
        json={"user_id": "buyer-001", "user_email": "buyer@nouraglow.sa"},
        headers=_headers("payment-auth-001"),
    )
    assert authorization.status_code == 201, authorization.text
    assert authorization.json()["payment"]["authorization_state"] == "AUTHORIZATION_PENDING"
    assert authorization.json()["payment"]["payment_state"] == "AWAITING_USER_APPROVAL"
    assert authorization.json()["payment"]["simulated"] is True

    paid = commerce_client.post(
        f"/api/v1/orders/{order_id}/payment/confirm-demo",
        json={"approved_by": "buyer-001", "acknowledge_simulation": True},
        headers=_headers("payment-confirm-001"),
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["order"]["status"] == "PAID"
    assert paid.json()["payment"]["payment_state"] == "SIMULATED_PAID"

    started = commerce_client.post(
        f"/api/v1/orders/{order_id}/start", headers=_headers("fulfillment-start-001")
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "REVIEW_REQUIRED"
    job = commerce_db.scalar(select(FulfillmentJob).where(FulfillmentJob.order_id == order_id))
    run = commerce_db.get(GenerationRun, job.generation_run_id)
    assert json.loads(run.provider_selection_json)["video"]["vendor"] == "openai"

    retried = commerce_client.post(
        f"/api/v1/orders/{order_id}/retry",
        json={"component": "Arabic narration", "reason": "Controlled demo verification failure"},
        headers=_headers("fulfillment-retry-001"),
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "DELIVERED"

    deliverables = commerce_client.get(
        f"/api/v1/orders/{order_id}/deliverables",
        headers={"Authorization": f"Bearer {DEMO_KEY}"},
    )
    assert deliverables.status_code == 200, deliverables.text
    assert len(deliverables.json()["deliverables"]) >= 3
    assert all("download_url" in item for item in deliverables.json()["deliverables"])

    receipt_response = commerce_client.get(
        f"/api/v1/orders/{order_id}/delivery-receipt",
        headers={"Authorization": f"Bearer {DEMO_KEY}"},
    )
    assert receipt_response.status_code == 200, receipt_response.text
    receipt = receipt_response.json()
    verified = commerce_client.post(
        "/api/v1/delivery-receipts/verify",
        json={
            "receipt_payload": receipt["receipt_payload"],
            "receipt_hash": receipt["receipt_hash"],
            "signature": receipt["signature"],
            "signing_key_id": receipt["signing_key_id"],
        },
    )
    assert verified.status_code == 200
    assert verified.json()["valid"] is True

    events = commerce_client.get(
        f"/api/v1/orders/{order_id}/events",
        headers={"Authorization": f"Bearer {DEMO_KEY}"},
    ).json()["items"]
    assert events[-1]["event_type"] == "order.delivered"
    for previous, current in pairwise(events):
        assert current["previous_event_hash"] == previous["event_hash"]

    dispute = commerce_client.post(
        f"/api/v1/orders/{order_id}/disputes",
        json={
            "reason_code": "DELIVERY_MISMATCH",
            "description": "Buyer requests a human review of the delivered composition.",
        },
        headers=_headers("dispute-create-001"),
    )
    assert dispute.status_code == 201, dispute.text
    assert dispute.json()["status"] == "OPEN"


def test_sandbox_commercial_checkout_is_safe_and_can_be_replaced(
    commerce_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class FakePravaGateway:
        def __init__(self) -> None:
            self.created: list[dict] = []
            self.revoked: list[str] = []

        async def create_authorization(self, request: dict) -> dict:
            self.created.append(request)
            suffix = str(len(self.created))
            return {
                "session_id": f"prava-session-{suffix}",
                "session_token": f"must-not-leak-{suffix}",
                "iframe_url": f"https://sandbox.collect.prava.space/session/{suffix}",
                "order_id": f"prava-order-{suffix}",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
            }

        async def revoke(self, session_id: str) -> dict:
            self.revoked.append(session_id)
            return {"success": True, "session_id": session_id}

        async def get_authorization(self, session_id: str) -> dict:
            return {
                "session_id": session_id,
                "status": "awaiting_result",
                "transactions": [
                    {
                        "status": "awaiting_result",
                        "line_items": [
                            {
                                "txn_ref_id": "sandbox-transaction",
                                "status": "credentials_generated",
                                "token": "network-token-never-returned",
                                "dynamic_cvv": "123",
                                "expiry_month": "12",
                                "expiry_year": "2027",
                            }
                        ],
                    }
                ],
            }

        async def report_outcome(self, session_id: str, body: dict) -> dict:
            return {"session_id": session_id, "status": "approved", **body}

    fake = FakePravaGateway()
    monkeypatch.setattr(settings, "app_mode", "SANDBOX")
    monkeypatch.setattr(settings, "thikra_sandbox_auto_settle_prava", True)
    monkeypatch.setattr(commerce_payments, "gateway", lambda: fake)
    quote = commerce_client.post(
        "/api/v1/quotes", json=_quote_payload(), headers=_headers("sandbox-link-q")
    )
    assert quote.status_code == 201
    accepted = commerce_client.post(
        f"/api/v1/quotes/{quote.json()['id']}/accept", headers=_headers("sandbox-link-a")
    )
    assert accepted.status_code == 200
    order = commerce_client.post(
        "/api/v1/orders", json={"quote_id": quote.json()["id"]}, headers=_headers("sandbox-link-o")
    ).json()
    authorization = commerce_client.post(
        f"/api/v1/orders/{order['id']}/payment-authorization",
        json={"user_id": "sandbox-buyer", "user_email": "buyer@nouraglow.sa"},
        headers=_headers("sandbox-link-p"),
    )
    assert authorization.status_code == 201, authorization.text
    checkout = authorization.json()["checkout"]
    assert checkout["checkout_url"] == "https://sandbox.collect.prava.space/session/1"
    assert checkout["single_use"] is True
    assert "session_token" not in checkout
    assert "must-not-leak" not in authorization.text
    assert authorization.json()["payment"]["paid_amount_minor"] == 0

    refreshed = commerce_client.post(
        f"/api/v1/orders/{order['id']}/payment-authorization/refresh",
        json={"user_id": "sandbox-buyer", "user_email": "buyer@nouraglow.sa"},
        headers=_headers("sandbox-link-r"),
    )
    assert refreshed.status_code == 200, refreshed.text
    assert fake.revoked == ["prava-session-1"]
    assert refreshed.json()["checkout"]["checkout_url"].endswith("/2")
    blocked = commerce_client.post(
        f"/api/v1/orders/{order['id']}/start", headers=_headers("sandbox-link-start")
    )
    assert blocked.status_code == 409
    status = commerce_client.get(
        f"/api/v1/orders/{order['id']}/payment",
        headers={"Authorization": f"Bearer {DEMO_KEY}"},
    )
    assert status.status_code == 200, status.text
    assert status.json()["payment"]["payment_state"] == "SANDBOX_SETTLED_NO_REAL_FUNDS"
    assert status.json()["payment"]["sandbox_test_settlement"] is True
    assert status.json()["payment"]["fulfillment_authorized"] is True
    assert status.json()["payment"]["customer_payment_collected"] is False
    assert "network-token-never-returned" not in status.text


def test_commerce_provider_constraints_normalize_and_are_enforced(
    commerce_client: TestClient, commerce_db: Session
):
    payload = _quote_payload()
    payload["input"] |= {
        "requiredProviders": ["OpenAI", "GMI Cloud"],
        "forbiddenProviders": ["Replicate", "Prava"],
    }
    quote_response = commerce_client.post(
        "/api/v1/quotes", json=payload, headers=_headers("provider-constraint-quote")
    )
    assert quote_response.status_code == 201, quote_response.text
    quote = commerce_db.get(Quote, quote_response.json()["id"])
    stored_input = json.loads(quote.input_payload_json)
    assert stored_input["requiredProviders"] == ["gmicloud", "openai"]
    assert stored_input["forbiddenProviders"] == ["prava", "replicate"]

    commerce_client.post(
        f"/api/v1/quotes/{quote.id}/accept", headers=_headers("provider-constraint-accept")
    )
    order = commerce_client.post(
        "/api/v1/orders",
        json={"quote_id": quote.id},
        headers=_headers("provider-constraint-order"),
    ).json()
    authorized = commerce_client.post(
        f"/api/v1/orders/{order['id']}/payment-authorization",
        json={"user_id": "provider-policy", "user_email": "provider-policy@nouraglow.sa"},
        headers=_headers("provider-constraint-auth"),
    )
    assert authorized.status_code == 201, authorized.text
    paid = commerce_client.post(
        f"/api/v1/orders/{order['id']}/payment/confirm-demo",
        json={"approved_by": "provider-policy", "acknowledge_simulation": True},
        headers=_headers("provider-constraint-confirm"),
    )
    assert paid.status_code == 200, paid.text
    started = commerce_client.post(
        f"/api/v1/orders/{order['id']}/start", headers=_headers("provider-constraint-start")
    )
    assert started.status_code == 200, started.text
    job = commerce_db.scalar(select(FulfillmentJob).where(FulfillmentJob.order_id == order["id"]))
    run = commerce_db.get(GenerationRun, job.generation_run_id)
    selected = json.loads(run.provider_selection_json)
    assert {choice["vendor"] for choice in selected.values()} <= {"openai", "gmicloud"}
    assert selected["video"]["vendor"] == "openai"
    assert "replicate" not in {choice["vendor"] for choice in selected.values()}


def test_local_sandbox_test_fulfillment_starts_live_executor(
    commerce_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "app_mode", "SANDBOX")
    monkeypatch.setattr(settings, "thikra_api_base_url", "http://127.0.0.1:43192")
    monkeypatch.setattr(settings, "thikra_agent_test_fulfillment_enabled", True)
    monkeypatch.setattr(settings, "thikra_agent_test_max_quote_minor", 1000)
    started: list[str] = []

    async def fake_executor(order_id: str) -> None:
        started.append(order_id)

    monkeypatch.setattr(commerce_api, "execute_live_fulfillment", fake_executor)
    quote = commerce_client.post(
        "/api/v1/quotes", json=_quote_payload(), headers=_headers("test-fulfillment-quote")
    ).json()
    commerce_client.post(
        f"/api/v1/quotes/{quote['id']}/accept", headers=_headers("test-fulfillment-accept")
    )
    order = commerce_client.post(
        "/api/v1/orders",
        json={"quote_id": quote["id"], "external_reference": "local-test-fulfillment"},
        headers=_headers("test-fulfillment-order"),
    ).json()

    response = commerce_client.post(
        f"/api/v1/orders/{order['id']}/test-fulfillment",
        headers=_headers("test-fulfillment-start"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["payment"]["gateway"] == "TEST_BYPASS"
    assert body["payment"]["payment_state"] == "TEST_BYPASSED_NO_CUSTOMER_PAYMENT"
    assert body["payment"]["paid_amount_minor"] == 0
    assert body["order"]["status"] == "FULFILLING"
    assert started == [order["id"]]


def test_test_fulfillment_enforces_local_sandbox_scope_and_cap(
    commerce_client: TestClient, commerce_db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "app_mode", "SANDBOX")
    monkeypatch.setattr(settings, "thikra_api_base_url", "http://localhost:43192")
    monkeypatch.setattr(settings, "thikra_agent_test_fulfillment_enabled", False)
    quote = commerce_client.post(
        "/api/v1/quotes", json=_quote_payload(), headers=_headers("test-guard-quote")
    ).json()
    commerce_client.post(
        f"/api/v1/quotes/{quote['id']}/accept", headers=_headers("test-guard-accept")
    )
    order = commerce_client.post(
        "/api/v1/orders", json={"quote_id": quote["id"]}, headers=_headers("test-guard-order")
    ).json()
    disabled = commerce_client.post(
        f"/api/v1/orders/{order['id']}/test-fulfillment", headers=_headers("test-guard-disabled")
    )
    assert disabled.status_code == 409

    monkeypatch.setattr(settings, "thikra_agent_test_fulfillment_enabled", True)
    key = commerce_db.scalar(select(APIKey))
    key.scopes_json = '["orders:create"]'
    commerce_db.commit()
    forbidden = commerce_client.post(
        f"/api/v1/orders/{order['id']}/test-fulfillment", headers=_headers("test-guard-scope")
    )
    assert forbidden.status_code == 403

    key.scopes_json = '["orders:create", "orders:test"]'
    commerce_db.commit()
    monkeypatch.setattr(settings, "thikra_agent_test_max_quote_minor", 1)
    over_cap = commerce_client.post(
        f"/api/v1/orders/{order['id']}/test-fulfillment", headers=_headers("test-guard-cap")
    )
    assert over_cap.status_code == 409

    monkeypatch.setattr(settings, "thikra_agent_test_max_quote_minor", 1000)
    monkeypatch.setattr(settings, "thikra_api_base_url", "https://sandbox.thikra.example")
    non_local = commerce_client.post(
        f"/api/v1/orders/{order['id']}/test-fulfillment", headers=_headers("test-guard-host")
    )
    assert non_local.status_code == 409

    monkeypatch.setattr(settings, "thikra_api_base_url", "http://localhost:43192")
    monkeypatch.setattr(settings, "app_mode", "PRODUCTION")
    non_sandbox = commerce_client.post(
        f"/api/v1/orders/{order['id']}/test-fulfillment", headers=_headers("test-guard-mode")
    )
    assert non_sandbox.status_code == 409


def test_idempotency_conflict_is_rejected(commerce_client: TestClient):
    first = commerce_client.post(
        "/api/v1/quotes", json=_quote_payload(), headers=_headers("same-key-123")
    )
    assert first.status_code == 201
    changed = _quote_payload()
    changed["input"]["brief"] = (
        "A different valid brief that must not share the prior idempotent response."
    )
    conflict = commerce_client.post(
        "/api/v1/quotes", json=changed, headers=_headers("same-key-123")
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
