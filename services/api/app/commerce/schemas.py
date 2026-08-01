"""Public commerce contracts shared by REST, MCP, and the SvelteKit client."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Money(StrictModel):
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, value: str) -> str:
        return value.upper()


class PrincipalDeclaration(StrictModel):
    type: Literal["HUMAN", "ORGANISATION", "UNKNOWN"] = "UNKNOWN"
    display_name: str = Field(min_length=1, max_length=240)
    email: EmailStr | None = None
    organisation: str | None = Field(default=None, max_length=240)
    external_reference: str | None = Field(default=None, max_length=240)


class AgentDeclaration(StrictModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=2000)
    developer_name: str | None = Field(default=None, max_length=240)
    operator_name: str | None = Field(default=None, max_length=240)
    framework: str | None = Field(default=None, max_length=120)
    model_name: str | None = Field(default=None, max_length=180)
    model_version: str | None = Field(default=None, max_length=80)
    external_agent_id: str | None = Field(default=None, max_length=240)
    agent_card_url: HttpUrl | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QuoteCreate(StrictModel):
    service: str = Field(min_length=2, max_length=120)
    input: dict[str, Any]
    buyer_principal: PrincipalDeclaration
    buyer_agent: AgentDeclaration
    maximum_budget: Money | None = None
    callback_url: HttpUrl | None = None


class OrderCreate(StrictModel):
    quote_id: str
    callback_url: HttpUrl | None = None
    external_reference: str | None = Field(default=None, max_length=240)


class PaymentAuthorizationCreate(StrictModel):
    user_email: EmailStr
    user_id: str = Field(min_length=1, max_length=255)


class DemoPaymentApproval(StrictModel):
    approved_by: str = Field(min_length=1, max_length=180)
    acknowledge_simulation: Literal[True]


class MerchantChargeReport(StrictModel):
    txn_ref_id: str = Field(min_length=1, max_length=240)
    status: Literal["APPROVED", "DECLINED"]
    amount_paid_minor: int = Field(ge=0)


class RetryCreate(StrictModel):
    component: str = Field(default="failed", min_length=1, max_length=120)
    reason: str = Field(default="Verification failure", max_length=500)


class DisputeCreate(StrictModel):
    reason_code: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=4, max_length=3000)
    deliverable_id: str | None = None


class ReceiptVerify(StrictModel):
    receipt_payload: dict[str, Any]
    receipt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str
    signing_key_id: str


class DeveloperApplicationCreate(StrictModel):
    name: str = Field(min_length=2, max_length=240)
    owner: PrincipalDeclaration
    redirect_uris: list[HttpUrl] = Field(default_factory=list)
    webhook_allowlist: list[HttpUrl] = Field(default_factory=list)


class APIKeyCreate(StrictModel):
    name: str = Field(min_length=2, max_length=160)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None


class WebhookSubscriptionCreate(StrictModel):
    callback_url: HttpUrl
    events: list[str] = Field(min_length=1)


class WebhookTestRequest(StrictModel):
    event_type: str = "order.progress"


class ServiceVersionCreate(StrictModel):
    name: str | None = Field(default=None, max_length=240)
    short_description: str | None = Field(default=None, max_length=500)
    long_description: str | None = None
    base_price_minor: int | None = Field(default=None, ge=0)
    minimum_price_minor: int | None = Field(default=None, ge=0)
    maximum_price_minor: int | None = Field(default=None, ge=0)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class ServiceStatusUpdate(StrictModel):
    status: Literal["ACTIVE", "PAUSED", "RETIRED"]
