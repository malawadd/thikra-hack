"""Typed request and mandate schemas. Money is always integer minor units."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Mode = Literal["DEMO", "SANDBOX", "PRODUCTION"]


class Deliverable(BaseModel):
    modality: Literal["image", "video", "voice", "music", "combined_advertisement"]
    variants: int = Field(ge=1, le=12)
    duration_sec: int = Field(ge=1, le=180)
    aspect_ratio: str
    resolution: str
    file_format: str


class BriefCreate(BaseModel):
    campaign_name: str = Field(min_length=2, max_length=240)
    product: str = Field(min_length=2, max_length=240)
    objective: str = Field(min_length=4, max_length=2000)
    target_audience: str = Field(min_length=2, max_length=500)
    language: str = Field(min_length=2, max_length=80)
    tone: str = Field(min_length=2, max_length=160)
    creative_brief: str = Field(min_length=10, max_length=8000)
    deliverables: list[Deliverable] = Field(min_length=1)
    maximum_budget_minor: int = Field(ge=100, le=1_000_000)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    deadline: datetime
    maximum_retries: int = Field(ge=0, le=5)
    permitted_providers: list[str] = Field(default_factory=list)
    forbidden_providers: list[str] = Field(default_factory=list)
    human_approval_threshold_minor: int = Field(ge=0)
    commercial_use_required: bool = True
    likeness_restrictions: str = "No real-person or celebrity likenesses"
    forbidden_elements: list[str] = Field(default_factory=list)
    required_elements: list[str] = Field(default_factory=list)
    claim_constraints: list[str] = Field(default_factory=list)
    required_language: str
    attribution_requirements: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class CreativeMandate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate_id: str
    version: int
    principal_id: str
    objective: str
    deliverables: list[Deliverable]
    target_audience: str
    language: str
    tone: str
    budget_cap_minor: int
    currency: str
    deadline: datetime
    allowed_modalities: list[str]
    allowed_providers: list[str]
    forbidden_providers: list[str]
    allowed_models: list[str]
    forbidden_models: list[str]
    required_aspect_ratio: str
    required_resolution: str
    required_duration_sec: int
    commercial_use_required: bool
    likeness_policy: str
    forbidden_elements: list[str]
    required_elements: list[str]
    claim_constraints: list[str]
    attribution_requirements: list[str]
    maximum_retries: int
    retry_budget_minor: int
    human_review_triggers: list[str]
    created_at: datetime
    expires_at: datetime


class MandateEdit(BaseModel):
    mandate: CreativeMandate
    edit_summary: str = Field(min_length=3, max_length=500)


class ProviderStrategyRequest(BaseModel):
    mandate_id: str
    mode: Literal["automatic", "manual", "per_modality"] = "automatic"
    selections: dict[str, dict[str, str | None]] = Field(default_factory=dict)


class AuthorizationCreate(BaseModel):
    mandate_id: str
    run_id: str | None = None
    merchant: str = Field(min_length=2, max_length=180)
    merchant_url: str
    maximum_amount_minor: int = Field(ge=0)
    estimated_amount_minor: int = Field(ge=0)
    retry_reserve_minor: int = Field(ge=0)
    verification_only: bool = False
    currency: str = "USD"
    user_id: str = "demo-user"
    user_email: EmailStr = "brand.manager@thikra.demo"
    idempotency_key: str = Field(min_length=8, max_length=180)


class PaymentReport(BaseModel):
    txn_ref_id: str
    txn_status: Literal["APPROVED", "DECLINED"]
    amount_paid_minor: int | None = Field(default=None, ge=0)


class RunLaunch(BaseModel):
    mandate_id: str
    payment_id: str
    provider_selection: dict[str, dict[str, str | None]]
    idempotency_key: str = Field(min_length=8, max_length=180)


class RunDecision(BaseModel):
    note: str = Field(default="", max_length=2000)


class RetryRequest(BaseModel):
    component: str = "failed"
    reason: str = Field(default="Verification failure", max_length=500)


class SceneEdit(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)
    narration: str = Field(min_length=1, max_length=2000)


class CaseCreate(BaseModel):
    run_id: str
    reason: str = Field(min_length=4, max_length=3000)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    owner: str = "Trust operations"


class CaseUpdate(BaseModel):
    status: Literal["OPEN", "INVESTIGATING", "WAITING", "RESOLVED"]
    owner: str | None = None
    resolution: str | None = None


class CaseNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    author: str = "Brand manager"
