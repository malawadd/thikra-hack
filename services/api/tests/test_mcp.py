"""MCP integration through a real Streamable HTTP client and ASGI transport."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents import gateway
from app.agents.mcp import mcp_app
from app.commerce.api import router as commerce_router
from app.commerce.service import seed_commerce
from app.config import settings
from app.thikra.database import Base, get_db
from app.thikra.service import seed_database

DEMO_KEY = "thikra_test_demo_local_only"


@pytest.fixture
def mcp_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_mode", "DEMO")
    monkeypatch.setattr(settings, "thikra_data_dir", str(tmp_path / "mcp-evidence"))
    monkeypatch.setattr(settings, "thikra_demo_api_key", DEMO_KEY)
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp.db'}")
    factory = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory() as db:
        seed_database(db)
        seed_commerce(db)
    monkeypatch.setattr(gateway, "SessionLocal", factory)
    application = FastAPI()
    application.include_router(commerce_router)
    application.mount("/mcp", mcp_app)

    def dependency():
        with factory() as db:
            yield db

    application.dependency_overrides[get_db] = dependency
    return application


async def _mcp_session(application: FastAPI):
    transport = httpx2.ASGITransport(app=application)
    return httpx2.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:43192",
        headers={"Authorization": f"Bearer {DEMO_KEY}"},
    )


async def _create_commercial_order(application: FastAPI) -> tuple[str, list[str]]:
    client = await _mcp_session(application)
    async with (
        client,
        streamable_http_client("http://127.0.0.1:43192/mcp/", http_client=client) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        tool_names = [tool.name for tool in tools.tools]
        services = await session.call_tool("thikra_list_services")
        assert services.is_error is False
        assert services.structured_content["total"] == 6
        quote = await session.call_tool(
            "thikra_request_quote",
            {
                "service_slug": "verified-vertical-ad",
                "input_payload": {
                    "brief": "Create a verified Arabic Noura Glow vertical advertisement without people or medical claims.",
                    "durationSeconds": 15,
                    "language": "ar",
                    "aspectRatio": "9:16",
                    "resolution": "1080x1920",
                    "maximumRetries": 2,
                },
                "buyer_agent": {
                    "name": "MCP buyer agent",
                    "framework": "official-python-mcp-client",
                    "external_agent_id": "mcp-test-agent",
                },
                "buyer_principal": {
                    "type": "HUMAN",
                    "display_name": "MCP buyer",
                    "email": "mcp-buyer@nouraglow.sa",
                },
                "maximum_budget_minor": 1000,
                "currency": "USD",
            },
        )
        assert quote.is_error is False, quote.content
        quote_id = quote.structured_content["id"]
        accepted = await session.call_tool("thikra_accept_quote", {"quote_id": quote_id})
        assert accepted.structured_content["status"] == "ACCEPTED"
        order = await session.call_tool(
            "thikra_create_order", {"quote_id": quote_id, "external_reference": "mcp-e2e"}
        )
        assert order.is_error is False, order.content
        order_id = order.structured_content["id"]
        status = await session.call_tool("thikra_get_order_status", {"order_id": order_id})
        assert status.structured_content["commercial_status"] == "QUOTED"
        return order_id, tool_names


async def _retrieve_and_dispute(application: FastAPI, order_id: str) -> None:
    client = await _mcp_session(application)
    async with (
        client,
        streamable_http_client("http://127.0.0.1:43192/mcp/", http_client=client) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        deliverables = await session.call_tool("thikra_get_deliverables", {"order_id": order_id})
        assert deliverables.is_error is False, deliverables.content
        assert len(deliverables.structured_content["deliverables"]) >= 3
        receipt = await session.call_tool("thikra_get_delivery_receipt", {"order_id": order_id})
        assert receipt.structured_content["receipt_hash"]
        dispute = await session.call_tool(
            "thikra_open_dispute",
            {
                "order_id": order_id,
                "reason_code": "BUYER_REVIEW",
                "description": "MCP buyer requests a review of the verified delivery.",
            },
        )
        assert dispute.is_error is False, dispute.content
        fetched = await session.call_tool(
            "thikra_get_dispute", {"dispute_id": dispute.structured_content["id"]}
        )
        assert fetched.structured_content["status"] == "OPEN"


@pytest.mark.asyncio
async def test_real_mcp_client_commercial_flow(
    mcp_environment: FastAPI, monkeypatch: pytest.MonkeyPatch
):
    async with mcp_app.router.lifespan_context(mcp_app):
        order_id, tool_names = await _create_commercial_order(mcp_environment)
        assert len(tool_names) == 20
        assert "thikra_request_quote" in tool_names
        assert "thikra_refresh_payment_authorization" in tool_names
        assert "thikra_wait_for_payment" in tool_names
        headers = {"Authorization": f"Bearer {DEMO_KEY}"}
        with TestClient(mcp_environment) as rest:
            authorization = rest.post(
                f"/api/v1/orders/{order_id}/payment-authorization",
                json={"user_id": "mcp-buyer", "user_email": "mcp-buyer@nouraglow.sa"},
                headers=headers | {"Idempotency-Key": "mcp-payment-auth"},
            )
            assert authorization.status_code == 201, authorization.text
            paid = rest.post(
                f"/api/v1/orders/{order_id}/payment/confirm-demo",
                json={"approved_by": "mcp-buyer", "acknowledge_simulation": True},
                headers=headers | {"Idempotency-Key": "mcp-payment-confirm"},
            )
            assert paid.status_code == 200, paid.text
            started = rest.post(
                f"/api/v1/orders/{order_id}/start",
                headers=headers | {"Idempotency-Key": "mcp-start-order"},
            )
            assert started.json()["status"] == "REVIEW_REQUIRED"
            retried = rest.post(
                f"/api/v1/orders/{order_id}/retry",
                json={"component": "Arabic narration", "reason": "Controlled demo failure"},
                headers=headers | {"Idempotency-Key": "mcp-retry-order"},
            )
            assert retried.json()["status"] == "DELIVERED"
        await _retrieve_and_dispute(mcp_environment, order_id)
        monkeypatch.setattr(settings, "app_mode", "SANDBOX")
        monkeypatch.setattr(settings, "thikra_api_base_url", "http://127.0.0.1:43192")
        monkeypatch.setattr(settings, "thikra_agent_test_fulfillment_enabled", True)
        monkeypatch.setattr(settings, "thikra_agent_test_max_quote_minor", 1000)
        started: list[str] = []

        async def fake_executor(test_order_id: str) -> None:
            started.append(test_order_id)

        monkeypatch.setattr(gateway, "execute_live_fulfillment", fake_executor)
        test_order_id, tool_names = await _create_commercial_order(mcp_environment)
        assert "thikra_start_test_fulfillment" in tool_names
        client = await _mcp_session(mcp_environment)
        async with (
            client,
            streamable_http_client("http://127.0.0.1:43192/mcp/", http_client=client) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "thikra_start_test_fulfillment", {"order_id": test_order_id}
            )
            assert result.is_error is False, result.content
            assert result.structured_content["payment"]["payment_state"] == (
                "TEST_BYPASSED_NO_CUSTOMER_PAYMENT"
            )
            assert result.structured_content["order"]["status"] == "FULFILLING"
        await asyncio.sleep(0)
        assert started == [test_order_id]


@pytest.mark.asyncio
async def test_wait_for_payment_returns_truthful_merchant_charge_stop(
    monkeypatch: pytest.MonkeyPatch,
):
    identity = gateway.GatewayIdentity("key", "application", "principal", ("payments:create",))
    calls = 0

    async def pending_then_authorized(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        state = "AWAITING_USER_APPROVAL" if calls == 1 else "MERCHANT_CHARGE_REQUIRED"
        return {"payment": {"payment_state": state}, "reconciliation": {"status": "completed"}}

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(gateway, "payment_status", pending_then_authorized)
    monkeypatch.setattr(gateway.asyncio, "sleep", no_sleep)
    result = await gateway.wait_for_payment(identity, "order-1", timeout_seconds=6)
    assert result["next_action"] == "MERCHANT_CHARGE_REQUIRED"
    assert result["wait_completed"] is True


@pytest.mark.asyncio
async def test_wait_for_payment_starts_completed_sandbox_checkout(
    monkeypatch: pytest.MonkeyPatch,
):
    identity = gateway.GatewayIdentity("key", "application", "principal", ("payments:create",))

    async def sandbox_settled(*_args, **_kwargs):
        return {
            "payment": {
                "payment_state": "SANDBOX_SETTLED_NO_REAL_FUNDS",
                "paid_amount_minor": 0,
                "sandbox_test_settlement": True,
                "customer_payment_collected": False,
            },
            "reconciliation": {"status": "completed"},
        }

    monkeypatch.setattr(gateway, "payment_status", sandbox_settled)
    result = await gateway.wait_for_payment(identity, "order-1", timeout_seconds=3)
    assert result["next_action"] == "START_FULFILLMENT"
    assert result["wait_completed"] is True
    assert result["sandbox_test_settlement"] is True
    assert result["customer_payment_collected"] is False


def test_paid_mcp_start_schedules_live_fulfillment(monkeypatch: pytest.MonkeyPatch):
    scheduled: list[str] = []
    monkeypatch.setattr(gateway, "_schedule_live_fulfillment", scheduled.append)
    identity = gateway.GatewayIdentity("key", "application", "principal", ("orders:create",))

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _model, _order_id):
            return object()

    monkeypatch.setattr(gateway, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(gateway, "start_order", lambda *_args: object())
    monkeypatch.setattr(
        gateway, "serialize_order", lambda *_args, **_kwargs: {"status": "FULFILLING"}
    )
    assert gateway.start_paid_order(identity, "order-1")["status"] == "FULFILLING"
    assert scheduled == ["order-1"]
