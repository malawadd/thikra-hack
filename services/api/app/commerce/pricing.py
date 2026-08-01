"""Deterministic provider-aware pricing; language models never set prices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from app.config import settings
from app.repo import provider_catalog


@dataclass(frozen=True)
class PriceEstimate:
    amount_minor: int
    currency: str
    confidence: str
    source: str


class ProviderPricingAdapter(Protocol):
    async def estimate(
        self, provider_id: str, model_id: str, modality: str, request: dict
    ) -> PriceEstimate: ...


class StaticDevelopmentPricingAdapter:
    RATES: ClassVar[dict[str, int]] = {
        "image": 85,
        "video": 240,
        "voice": 55,
        "music": 45,
        "verification": 75,
    }

    async def estimate(
        self, provider_id: str, model_id: str, modality: str, request: dict
    ) -> PriceEstimate:
        count = max(1, int(request.get("deliverable_count", request.get("variants", 1))))
        duration = max(1, int(request.get("durationSeconds", request.get("duration_seconds", 1))))
        rate = self.RATES.get(modality, 50)
        if modality in {"video", "voice", "music"}:
            amount = rate * count * max(1, (duration + 14) // 15)
        else:
            amount = rate * count
        known = any(
            entry["vendor"] == provider_id
            for entries in provider_catalog.matrix().values()
            for entry in entries
        )
        return PriceEstimate(
            amount_minor=amount,
            currency=settings.thikra_default_currency,
            confidence="MEDIUM" if known else "LOW",
            source=f"static-development:{provider_id}:{model_id or 'default'}",
        )


def quote_breakdown(provider_estimate_minor: int, maximum_retries: int) -> dict[str, int]:
    verification = settings.thikra_verification_fee_minor
    storage = settings.thikra_storage_fee_minor
    retry_reserve = min(provider_estimate_minor // 2, maximum_retries * 75)
    margin_base = provider_estimate_minor + verification + storage + retry_reserve
    platform = (margin_base * settings.thikra_platform_margin_bps + 9999) // 10000
    fixed_fee = 0
    tax = 0
    total = margin_base + platform + fixed_fee + tax
    return {
        "provider_generation_estimate_minor": provider_estimate_minor,
        "verification_fee_minor": verification,
        "storage_fee_minor": storage,
        "retry_reserve_minor": retry_reserve,
        "platform_fee_minor": platform,
        "fixed_fee_minor": fixed_fee,
        "tax_minor": tax,
        "total_minor": total,
    }
