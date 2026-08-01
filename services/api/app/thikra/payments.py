"""Prava and explicitly simulated demo payment gateways.

The official skill v1.1.0 documents no webhook signature or refund API. Those
operations therefore remain unsupported instead of being fabricated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from app.config import settings

# One-time network credentials are deliberately process-memory only. They are
# removed after the documented report-status call and never serialized.
EPHEMERAL_CREDENTIALS: dict[str, list[dict]] = {}


class PaymentGateway(Protocol):
    async def create_authorization(self, request: dict) -> dict: ...
    async def get_authorization(self, session_id: str) -> dict: ...
    async def report_outcome(self, session_id: str, body: dict) -> dict: ...
    async def revoke(self, session_id: str) -> dict: ...


class DemoPaymentGateway:
    async def create_authorization(self, request: dict) -> dict:
        now = datetime.now(UTC)
        token = request["idempotency_key"].replace(" ", "-")[:32]
        return {
            "session_id": f"demo_sess_{token}",
            "session_token": None,
            "iframe_url": None,
            "order_id": f"demo_order_{token}",
            "expires_at": (now + timedelta(minutes=15)).isoformat(),
            "status": "authorized",
            "simulated": True,
        }

    async def get_authorization(self, session_id: str) -> dict:
        return {"session_id": session_id, "status": "completed", "simulated": True}

    async def report_outcome(self, session_id: str, body: dict) -> dict:
        return {"session_id": session_id, "status": "simulated_reported", **body}

    async def revoke(self, session_id: str) -> dict:
        return {"session_id": session_id, "success": True, "simulated": True}


class PravaPaymentGateway:
    def _headers(self) -> dict[str, str]:
        if not settings.prava_secret_key:
            raise RuntimeError("PRAVA_SECRET_KEY is not configured")
        return {"Authorization": f"Bearer {settings.prava_secret_key}"}

    async def create_authorization(self, request: dict) -> dict:
        amount = f"{request['maximum_amount_minor'] / 100:.2f}"
        body = {
            "user_id": request["user_id"],
            "user_email": request["user_email"],
            "total_amount": amount,
            "currency": request["currency"],
            "external_order_ref": request["idempotency_key"],
            "description": f"Scoped creative procurement for mandate {request['mandate_id']}",
            "purchase_context": [
                {
                    "merchant_details": {
                        "name": request["merchant"],
                        "url": request["merchant_url"],
                        "country_code_iso2": "US",
                        "category_code": "7399",
                        "category": "Business services",
                    },
                    "product_details": [
                        {
                            "description": "Bounded generative-media provider purchase",
                            "unit_price": amount,
                            "quantity": 1,
                        }
                    ],
                    "effective_until_minutes": 15,
                }
            ],
        }
        # Prava accepts callback URLs only over HTTPS. Local sandbox runs use
        # an HTTP dev server, so omit the optional callback and keep the user
        # in the embedded iframe; deployed HTTPS environments still receive it.
        callback_url = f"{settings.public_web_url}/payments"
        if callback_url.startswith("https://"):
            body["callback_url"] = callback_url
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{settings.prava_backend_url}/v1/sessions",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=body,
            )
        response.raise_for_status()
        return response.json()

    async def get_authorization(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{settings.prava_backend_url}/v1/sessions/{session_id}/payment-result",
                headers=self._headers(),
                params={"_t": int(datetime.now(UTC).timestamp() * 1000)},
            )
        response.raise_for_status()
        return response.json()

    async def report_outcome(self, session_id: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{settings.prava_backend_url}/v1/sessions/{session_id}/report-status",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=body,
            )
        response.raise_for_status()
        return response.json()

    async def revoke(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{settings.prava_backend_url}/v1/sessions/{session_id}/revoke",
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()


def gateway() -> PaymentGateway:
    return DemoPaymentGateway() if settings.app_mode.upper() == "DEMO" else PravaPaymentGateway()


def sanitize_payment_result(result: dict) -> tuple[dict, list[dict]]:
    """Strip one-time credentials before persistence or browser responses."""
    sanitized = {**result}
    credentials: list[dict] = []
    transactions = []
    for transaction in result.get("transactions", []):
        clean_txn = {k: v for k, v in transaction.items() if k != "line_items"}
        clean_items = []
        for item in transaction.get("line_items", []):
            credential = {
                key: item.get(key)
                for key in ("txn_ref_id", "token", "dynamic_cvv", "expiry_month", "expiry_year")
            }
            credentials.append(credential)
            clean_items.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"token", "dynamic_cvv", "expiry_month", "expiry_year"}
                }
            )
        clean_txn["line_items"] = clean_items
        transactions.append(clean_txn)
    sanitized["transactions"] = transactions
    return sanitized, credentials
