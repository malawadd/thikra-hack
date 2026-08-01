"""Signed webhook subscriptions, SSRF defenses, and bounded retry records."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app.commerce.models import OrderEvent, WebhookDelivery, WebhookSubscription
from app.config import settings
from app.thikra.audit import canonical_json
from app.thikra.database import SessionLocal

SUPPORTED_EVENTS = {
    "quote.created",
    "quote.expired",
    "order.created",
    "order.payment_authorization_pending",
    "order.paid",
    "order.accepted",
    "order.fulfillment_started",
    "order.progress",
    "order.review_required",
    "order.ready",
    "order.delivered",
    "order.failed",
    "order.disputed",
    "order.refund_requested",
    "order.refunded",
}
_RETRY_TASKS: set[asyncio.Task] = set()


def _allowlist() -> set[str]:
    return {
        item.strip().lower()
        for item in settings.thikra_webhook_development_allowlist.split(",")
        if item.strip()
    }


def _unsafe_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def validate_callback_url(value: str, *, resolve_dns: bool = True) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if not host or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Webhook URL is malformed or contains forbidden credentials/fragment")
    allowlisted = host in _allowlist() and settings.app_mode.upper() == "DEMO"
    if parsed.scheme != "https" and not (allowlisted and parsed.scheme == "http"):
        raise ValueError("Webhook URL must use HTTPS")
    if host in {"localhost", "localhost.localdomain"} and not allowlisted:
        raise ValueError("Webhook URL cannot target localhost")
    try:
        if _unsafe_ip(host) and not allowlisted:
            raise ValueError("Webhook URL cannot target a private or special IP address")
        return value
    except ValueError as exc:
        if "does not appear" not in str(exc):
            raise
    if resolve_dns:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
        except socket.gaierror as exc:
            raise ValueError("Webhook hostname could not be resolved") from exc
        if not addresses or (any(_unsafe_ip(address) for address in addresses) and not allowlisted):
            raise ValueError("Webhook hostname resolves to a private or special IP address")
    return value


def webhook_secret(subscription_id: str) -> str:
    digest = hmac.new(
        settings.thikra_webhook_signing_secret.encode(), subscription_id.encode(), hashlib.sha256
    ).digest()
    return "whsec_" + digest.hex()


def webhook_secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def signature_headers(
    event_id: str, payload: dict, secret: str, timestamp: int | None = None
) -> dict:
    stamp = timestamp or int(datetime.now(UTC).timestamp())
    body = canonical_json(payload)
    signature = hmac.new(secret.encode(), f"{stamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "Thikra-Event-Id": event_id,
        "Thikra-Timestamp": str(stamp),
        "Thikra-Signature": f"v1={signature}",
        "Content-Type": "application/json",
    }


def verify_webhook_signature(
    *, payload: dict, secret: str, timestamp: str, signature: str, tolerance_seconds: int = 300
) -> bool:
    try:
        stamp = int(timestamp)
    except ValueError:
        return False
    if abs(int(datetime.now(UTC).timestamp()) - stamp) > tolerance_seconds:
        return False
    expected = signature_headers("ignored", payload, secret, stamp)["Thikra-Signature"]
    return hmac.compare_digest(expected, signature)


def event_payload(event: OrderEvent) -> dict:
    data = json.loads(event.payload_json)
    return {
        "id": event.id,
        "type": event.event_type,
        "created_at": event.created_at.isoformat(),
        "data": {"order_id": event.order_id, **data},
    }


async def _retry_after(event_id: str, delay: int) -> None:
    await asyncio.sleep(delay)
    await deliver_order_event(event_id)


async def deliver_order_event(event_id: str, *, force: bool = False) -> None:
    scheduled_delays: list[int] = []
    with SessionLocal() as db:
        event = db.get(OrderEvent, event_id)
        if event is None:
            return
        subscriptions = list(
            db.scalars(select(WebhookSubscription).where(WebhookSubscription.status == "ACTIVE"))
        )
        payload = event_payload(event)
        for subscription in subscriptions:
            if event.event_type not in json.loads(subscription.events_json):
                continue
            prior = list(
                db.scalars(
                    select(WebhookDelivery).where(
                        WebhookDelivery.subscription_id == subscription.id,
                        WebhookDelivery.event_id == event.id,
                    )
                )
            )
            if not force and any(item.delivered_at is not None for item in prior):
                continue
            attempt = len(prior) + 1
            if attempt > settings.thikra_webhook_max_attempts:
                subscription.status = "DISABLED"
                continue
            delivery = WebhookDelivery(
                subscription_id=subscription.id,
                event_id=event.id,
                attempt=attempt,
            )
            db.add(delivery)
            try:
                validate_callback_url(subscription.callback_url)
                async with httpx.AsyncClient(
                    timeout=settings.thikra_webhook_timeout_seconds, follow_redirects=False
                ) as client:
                    response = await client.post(
                        subscription.callback_url,
                        content=canonical_json(payload),
                        headers=signature_headers(
                            event.id, payload, webhook_secret(subscription.id)
                        ),
                    )
                delivery.status_code = response.status_code
                delivery.response_body_excerpt = response.text[:1000]
                if 200 <= response.status_code < 300:
                    delivery.delivered_at = datetime.now(UTC)
                else:
                    delay = retry_delay_seconds(attempt)
                    delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                    scheduled_delays.append(delay)
            except Exception as exc:
                delivery.response_body_excerpt = str(exc)[:1000]
                delay = retry_delay_seconds(attempt)
                delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                scheduled_delays.append(delay)
            if attempt >= settings.thikra_webhook_max_attempts and delivery.delivered_at is None:
                subscription.status = "DISABLED"
        db.commit()
    for delay in set(scheduled_delays):
        task = asyncio.create_task(_retry_after(event_id, delay))
        _RETRY_TASKS.add(task)
        task.add_done_callback(_RETRY_TASKS.discard)


def retry_delay_seconds(attempt: int) -> int:
    return min(3600, 2 ** max(1, attempt) * 5)
