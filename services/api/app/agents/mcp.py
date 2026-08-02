"""Authenticated MCP Streamable HTTP adapter for the Agent Gateway."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from app.agents import gateway
from app.config import settings


class ThikraTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        identity = gateway.verify_gateway_token(token)
        if identity is None:
            return None
        return AccessToken(
            token=token,
            client_id=identity.application_id,
            subject=identity.principal_id,
            scopes=list(identity.scopes),
            resource=f"{settings.thikra_api_base_url}/mcp",
            claims={
                "key_id": identity.key_id,
                "application_id": identity.application_id,
                "principal_id": identity.principal_id,
                "scopes": list(identity.scopes),
            },
        )


def _identity() -> gateway.GatewayIdentity:
    token = get_access_token()
    if token is None or not token.claims:
        raise PermissionError("Authenticated API key context is required")
    claims = token.claims
    return gateway.GatewayIdentity(
        key_id=str(claims["key_id"]),
        application_id=str(claims["application_id"]),
        principal_id=str(claims["principal_id"]),
        scopes=tuple(str(scope) for scope in claims["scopes"]),
    )


mcp_server = MCPServer(
    name="thikra-agent-gateway",
    title="Thikra Agent Gateway",
    description="Discover, purchase, fulfill, and retrieve verified creative services.",
    instructions=(
        "Use the service catalog and deterministic quote before creating an order. "
        "For payment: create authorization, present checkout_url as a clickable link and render a QR code encoding that exact URL before the human opens it. "
        "then call thikra_wait_for_payment. If it returns SANDBOX_SETTLED_NO_REAL_FUNDS with "
        "next_action START_FULFILLMENT, immediately call thikra_start_order: that is a completed Sandbox "
        "test checkout with zero customer funds collected. In production, MERCHANT_CHARGE_REQUIRED remains blocked "
        "until a documented exact merchant charge is recorded. Generation is not delivery, "
        "and delivery is not buyer acceptance. Use test fulfillment only when the user "
        "explicitly requests a local Sandbox test: it creates real provider spend but no customer payment."
    ),
    version="1.0.0",
    token_verifier=ThikraTokenVerifier(),
    auth=AuthSettings(
        issuer_url=settings.thikra_api_base_url,
        service_documentation_url=f"{settings.public_web_url}/developers/mcp",
        resource_server_url=f"{settings.thikra_api_base_url}/mcp",
        required_scopes=[],
    ),
)


@mcp_server.tool(name="thikra_list_services")
def thikra_list_services() -> dict[str, Any]:
    """List active creative services, prices, delivery estimates, and verification."""
    return gateway.list_services()


@mcp_server.tool(name="thikra_get_service")
def thikra_get_service(service_slug: str) -> dict[str, Any]:
    """Get a complete service version and its input/output JSON Schemas."""
    return gateway.service_detail(service_slug)


@mcp_server.tool(name="thikra_request_quote")
async def thikra_request_quote(
    service_slug: str,
    input_payload: dict[str, Any],
    buyer_agent: dict[str, Any],
    buyer_principal: dict[str, Any],
    maximum_budget_minor: int | None = None,
    currency: str = "USD",
    callback_url: str | None = None,
) -> dict[str, Any]:
    """Request a persisted deterministic quote; this never initiates payment."""
    return await gateway.request_quote(
        _identity(),
        service=service_slug,
        input_payload=input_payload,
        buyer_agent=buyer_agent,
        buyer_principal=buyer_principal,
        maximum_budget_minor=maximum_budget_minor,
        currency=currency,
        callback_url=callback_url,
    )


@mcp_server.tool(name="thikra_get_quote")
def thikra_get_quote(quote_id: str) -> dict[str, Any]:
    """Read an owned quote and its expiry and price breakdown."""
    return gateway.get_quote(_identity(), quote_id)


@mcp_server.tool(name="thikra_accept_quote")
def thikra_accept_quote(quote_id: str) -> dict[str, Any]:
    """Accept an active quote without charging or creating an order automatically."""
    return gateway.accept_quote_by_id(_identity(), quote_id)


@mcp_server.tool(name="thikra_create_order")
def thikra_create_order(
    quote_id: str,
    callback_url: str | None = None,
    external_reference: str | None = None,
) -> dict[str, Any]:
    """Create a commercial order from an accepted, unexpired quote."""
    return gateway.create_order_from_quote(_identity(), quote_id, callback_url, external_reference)


@mcp_server.tool(name="thikra_get_order")
def thikra_get_order(order_id: str) -> dict[str, Any]:
    """Read an owned order with payment and fulfillment linkage."""
    return gateway.get_order(_identity(), order_id)


@mcp_server.tool(name="thikra_create_payment_authorization")
async def thikra_create_payment_authorization(
    order_id: str, user_id: str, user_email: str
) -> dict[str, Any]:
    """Create one hosted Prava checkout; show its URL/QR to a human on one device only."""
    return await gateway.create_authorization(_identity(), order_id, user_id, user_email)


@mcp_server.tool(name="thikra_refresh_payment_authorization")
async def thikra_refresh_payment_authorization(
    order_id: str, user_id: str, user_email: str
) -> dict[str, Any]:
    """Revoke an unused checkout and return one fresh link for the human or QR code."""
    return await gateway.refresh_authorization(_identity(), order_id, user_id, user_email)


@mcp_server.tool(name="thikra_get_payment_status")
async def thikra_get_payment_status(order_id: str) -> dict[str, Any]:
    """Reconcile authorization and payment states without exposing credentials."""
    return await gateway.payment_status(_identity(), order_id)


@mcp_server.tool(name="thikra_wait_for_payment")
async def thikra_wait_for_payment(order_id: str, timeout_seconds: int = 90) -> dict[str, Any]:
    """Poll Prava every three seconds and return sanitized payment status only."""
    return await gateway.wait_for_payment(_identity(), order_id, timeout_seconds)


@mcp_server.tool(name="thikra_start_order")
def thikra_start_order(order_id: str) -> dict[str, Any]:
    """Idempotently start fulfillment after an exact payment or completed Sandbox test checkout."""
    return gateway.start_paid_order(_identity(), order_id)


@mcp_server.tool(name="thikra_start_test_fulfillment")
async def thikra_start_test_fulfillment(order_id: str) -> dict[str, Any]:
    """Start local Sandbox generation without Prava; real provider spend may occur and no customer payment is collected."""
    return await gateway.start_test_fulfillment(_identity(), order_id)


@mcp_server.tool(name="thikra_get_order_status")
def thikra_get_order_status(order_id: str) -> dict[str, Any]:
    """Return commercial, payment, fulfillment, and latest-event state."""
    return gateway.order_status(_identity(), order_id)


@mcp_server.tool(name="thikra_get_order_events")
def thikra_get_order_events(order_id: str) -> dict[str, Any]:
    """Return the owned order's tamper-evident event timeline."""
    return gateway.order_events(_identity(), order_id)


@mcp_server.tool(name="thikra_get_deliverables")
def thikra_get_deliverables(order_id: str) -> dict[str, Any]:
    """Return verified deliverables with short-lived signed URLs."""
    return gateway.deliverables(_identity(), order_id)


@mcp_server.tool(name="thikra_get_delivery_receipt")
def thikra_get_delivery_receipt(order_id: str) -> dict[str, Any]:
    """Return the signed payment-to-delivery receipt for an owned order."""
    return gateway.delivery_receipt(_identity(), order_id)


@mcp_server.tool(name="thikra_request_retry")
def thikra_request_retry(
    order_id: str,
    component: str = "failed",
    reason: str = "Verification failure",
) -> dict[str, Any]:
    """Retry a failed component within the service's retry and budget constraints."""
    return gateway.request_retry(_identity(), order_id, component, reason)


@mcp_server.tool(name="thikra_open_dispute")
def thikra_open_dispute(
    order_id: str,
    reason_code: str,
    description: str,
    deliverable_id: str | None = None,
) -> dict[str, Any]:
    """Open a persisted dispute linked to the existing redress-case system."""
    return gateway.open_dispute(_identity(), order_id, reason_code, description, deliverable_id)


@mcp_server.tool(name="thikra_get_dispute")
def thikra_get_dispute(dispute_id: str) -> dict[str, Any]:
    """Read an owned dispute and its real resolution state."""
    return gateway.get_dispute(_identity(), dispute_id)


mcp_app = mcp_server.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    max_request_body_size=1_048_576,
)
