"""MCP integration through a real Streamable HTTP client and ASGI transport."""

from __future__ import annotations

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
async def test_real_mcp_client_commercial_flow(mcp_environment: FastAPI):
    async with mcp_app.router.lifespan_context(mcp_app):
        order_id, tool_names = await _create_commercial_order(mcp_environment)
        assert len(tool_names) == 17
        assert "thikra_request_quote" in tool_names
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
