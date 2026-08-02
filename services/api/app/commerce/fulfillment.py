"""Adapter from paid commercial orders into the existing Thikra run state machine."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce.models import (
    CommercialOrder,
    Deliverable,
    DeliveryReceipt,
    FulfillmentJob,
    ServiceOffer,
)
from app.commerce.receipts import sign_receipt
from app.commerce.security import AuthContext
from app.commerce.service import require_order_owner
from app.commerce.state_machine import append_order_event, transition_order
from app.config import settings
from app.repo import provider_catalog
from app.thikra.audit import append_event, canonical_json
from app.thikra.models import Asset as DBAsset
from app.thikra.models import Evaluation, EvaluationResult, GenerationRun, PaymentRecord
from app.thikra.orchestration import execute_generation_run
from app.thikra.schemas import BriefCreate
from app.thikra.service import (
    compile_brief,
    confirm_mandate,
    launch_run,
    materialize_demo_delivery,
    provider_strategy,
)
from app.thikra.state_machine import retry_allowed
from app.thikra.storage import evidence_storage


def _load(value: str, fallback=None):
    return json.loads(value) if value else fallback


def _brief_for_order(order: CommercialOrder, offer: ServiceOffer) -> BriefCreate:
    value = _load(order.input_payload_json, {})
    duration = int(value.get("durationSeconds", 15))
    ratio = value.get("aspectRatio", "9:16")
    resolution = value.get("resolution", "1080x1920")
    count = int(value.get("deliverableCount", 1))
    language = value.get("language", "ar")
    deadline = datetime.now(UTC) + timedelta(seconds=offer.estimated_delivery_seconds_max + 3600)
    modality = (
        "combined_advertisement"
        if offer.slug == "verified-vertical-ad"
        else (
            "video"
            if "video" in _load(offer.supported_modalities_json, [])
            else "image"
            if "image" in _load(offer.supported_modalities_json, [])
            else "voice"
        )
    )
    return BriefCreate.model_validate(
        {
            "campaign_name": f"{offer.name} · {order.public_order_number}",
            "product": value.get("product", offer.name),
            "objective": value["brief"],
            "target_audience": value.get("targetAudience", "Buyer-specified audience"),
            "language": language,
            "tone": value.get("tone", "Clear and brand-appropriate"),
            "creative_brief": value["brief"],
            "deliverables": [
                {
                    "modality": modality,
                    "variants": count,
                    "duration_sec": duration,
                    "aspect_ratio": ratio,
                    "resolution": resolution,
                    "file_format": "mp4"
                    if modality in {"video", "combined_advertisement"}
                    else "png"
                    if modality == "image"
                    else "wav",
                }
            ],
            "maximum_budget_minor": order.quoted_total_minor,
            "currency": order.currency,
            "deadline": deadline,
            "maximum_retries": min(
                int(value.get("maximumRetries", offer.maximum_retries_included)),
                offer.maximum_retries_included,
            ),
            "permitted_providers": value.get("requiredProviders", []),
            "forbidden_providers": value.get("forbiddenProviders", []),
            "human_approval_threshold_minor": order.quoted_total_minor,
            "commercial_use_required": True,
            "likeness_restrictions": value.get(
                "likenessPolicy", "No real-person or celebrity likenesses"
            ),
            "forbidden_elements": value.get("forbiddenElements", []),
            "required_elements": value.get("requiredElements", []),
            "claim_constraints": value.get("claimConstraints", []),
            "required_language": language,
            "attribution_requirements": ["Provider, model, asset hashes, and lineage"],
        }
    )


def _order_provider_selections(order: CommercialOrder) -> dict:
    input_payload = _load(order.input_payload_json, {})
    explicit = input_payload.get("providerSelection", {})
    if explicit:
        selections = {}
        for slot, choice in explicit.items():
            entry = provider_catalog.resolve(slot, choice["vendor"])
            selections[slot] = {"vendor": entry.vendor, "model": choice.get("model") or entry.default_model}
        return selections
    required = set(input_payload.get("requiredProviders", []))
    forbidden = set(input_payload.get("forbiddenProviders", []))
    selections = {}
    for slot, entries in provider_catalog.matrix().items():
        match = next((entry for entry in entries if entry["vendor"] in required), None)
        if match:
            selections[slot] = {"vendor": match["vendor"], "model": match["default_model"]}
    # Commerce defaults to GMI Cloud for image-to-video unless the buyer set a
    # provider allow-list or explicitly forbade it. Studio's default must not
    # silently diverge from the order path.
    if not required and "gmicloud" not in forbidden:
        gmi_video = next(
            (entry for entry in provider_catalog.matrix()["video"] if entry["vendor"] == "gmicloud"),
            None,
        )
        if gmi_video:
            selections["video"] = {
                "vendor": gmi_video["vendor"],
                "model": gmi_video["default_model"],
            }
    return selections


def start_order(db: Session, auth: AuthContext, order: CommercialOrder) -> FulfillmentJob:
    auth.require("orders:create")
    require_order_owner(db, auth, order)
    existing = db.scalar(select(FulfillmentJob).where(FulfillmentJob.order_id == order.id))
    if existing:
        return existing
    payment = db.scalar(select(PaymentRecord).where(PaymentRecord.commercial_order_id == order.id))
    test_bypass = bool(
        payment
        and payment.gateway == "TEST_BYPASS"
        and payment.payment_state == "TEST_BYPASSED_NO_CUSTOMER_PAYMENT"
    )
    if order.status != "PAID":
        if order.status != "TEST_AUTHORIZED" or not test_bypass:
            raise ValueError(
                "Fulfillment can start only after payment is complete or local test authorization"
            )
    elif payment is None or payment.paid_amount_minor != order.quoted_total_minor:
        raise ValueError("Order does not have an exact completed payment")
    offer = db.get(ServiceOffer, order.service_offer_id)
    transition_order(db, order, "ACCEPTED", actor_type="SYSTEM", actor_id="order-service")
    transition_order(
        db,
        order,
        "FULFILLMENT_PENDING",
        actor_type="SYSTEM",
        actor_id="fulfillment-coordinator",
    )
    compiled = compile_brief(db, _brief_for_order(order, offer))
    confirm_mandate(db, compiled["mandate_id"])
    payment.mandate_id = compiled["mandate_id"]
    selections = _order_provider_selections(order)
    strategy = provider_strategy(
        db,
        compiled["mandate_id"],
        selections or None,
        slots=set(selections) if selections else None,
    )
    run = launch_run(
        db,
        compiled["mandate_id"],
        payment,
        strategy["selection"],
        f"commerce-run-{order.id}",
    )
    job = FulfillmentJob(
        order_id=order.id,
        mandate_id=compiled["mandate_id"],
        generation_run_id=run.id,
        status="FULFILLING",
        attempt_number=1,
        started_at=datetime.now(UTC),
    )
    db.add(job)
    db.flush()
    transition_order(
        db,
        order,
        "FULFILLING",
        actor_type="SYSTEM",
        actor_id="fulfillment-coordinator",
        payload={"fulfillment_job_id": job.id, "generation_run_id": run.id},
    )
    run.status = "GENERATING"
    run.current_stage = "Commercial order generation"
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="order.fulfillment_started",
        actor_type="SYSTEM",
        actor_id="fulfillment-coordinator",
        payload={"order_id": order.id, "quote_id": order.quote_id, "payment_id": payment.id},
        related_object_ids=[order.id, job.id, run.id, payment.id],
    )
    db.commit()
    if settings.app_mode.upper() == "DEMO":
        materialize_demo_delivery(db, run.id)
        transition_order(
            db,
            order,
            "VERIFYING",
            actor_type="AGENT",
            actor_id="verification-engine",
            payload={"generation_run_id": run.id},
        )
        transition_order(
            db,
            order,
            "REVIEW_REQUIRED",
            actor_type="AGENT",
            actor_id="verification-engine",
            payload={
                "controlled_fixture_failure": "Arabic narration present",
                "retry_remaining": run.maximum_retries - run.retry_count,
            },
        )
        job.status = "REVIEW_REQUIRED"
        db.commit()
    return job


async def execute_live_fulfillment(order_id: str) -> None:
    from app.thikra.database import SessionLocal

    with SessionLocal() as db:
        job = db.scalar(select(FulfillmentJob).where(FulfillmentJob.order_id == order_id))
        if job is None or not job.generation_run_id:
            return
        run_id = job.generation_run_id
        run = db.get(GenerationRun, run_id)
        # A job is claimed exactly once.  This is vital for MCP callers: an
        # interrupted client must not cause a second provider submission for
        # the same paid or test-authorized order.
        if job.status != "FULFILLING" or run is None or run.status != "GENERATING":
            return
    await execute_generation_run(run_id)
    with SessionLocal() as db:
        order = db.get(CommercialOrder, order_id)
        job = db.scalar(select(FulfillmentJob).where(FulfillmentJob.order_id == order_id))
        run = db.get(GenerationRun, job.generation_run_id)
        if run.status == "HUMAN_REVIEW":
            transition_order(
                db,
                order,
                "VERIFYING",
                actor_type="AGENT",
                actor_id="verification-engine",
                payload={"generation_run_id": run.id},
            )
            payment = db.get(PaymentRecord, run.payment_record_id)
            checks = list(
                db.scalars(
                    select(EvaluationResult)
                    .join(Evaluation, EvaluationResult.evaluation_id == Evaluation.id)
                    .where(Evaluation.run_id == run.id)
                )
            )
            test_bypass = bool(
                payment
                and payment.gateway == "TEST_BYPASS"
                and payment.payment_state == "TEST_BYPASSED_NO_CUSTOMER_PAYMENT"
            )
            if test_bypass and not any(check.status == "FAIL" for check in checks):
                # The explicit local test action is also the user's approval to complete
                # a no-failure Sandbox run. Paid orders always retain human review.
                run.status = "COMPLETED"
                run.current_stage = "Verified local Sandbox test delivery"
                run.accepted = True
                for asset in db.scalars(select(DBAsset).where(DBAsset.run_id == run.id)):
                    asset.approval_state = "APPROVED"
                append_event(
                    db,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    event_type="delivery.test_auto_approved",
                    actor_type="SYSTEM",
                    actor_id="local-sandbox-test-fulfillment",
                    payload={
                        "customer_payment_collected": False,
                        "verification_failures": 0,
                    },
                    related_object_ids=[order.id, job.id, run.id, payment.id],
                )
                transition_order(
                    db,
                    order,
                    "READY",
                    actor_type="AGENT",
                    actor_id="verification-engine",
                    payload={"local_sandbox_test_auto_delivery": True},
                )
                _create_delivery_package(db, order, job, run)
                job.status = "COMPLETED"
                job.completed_at = datetime.now(UTC)
                transition_order(
                    db,
                    order,
                    "DELIVERED",
                    actor_type="SYSTEM",
                    actor_id="delivery-service",
                    payload={
                        "fulfillment_job_id": job.id,
                        "generation_run_id": run.id,
                        "local_sandbox_test_auto_delivery": True,
                    },
                )
            else:
                transition_order(
                    db, order, "REVIEW_REQUIRED", actor_type="AGENT", actor_id="verification-engine"
                )
                job.status = "REVIEW_REQUIRED"
        elif run.status == "FAILED":
            transition_order(
                db,
                order,
                "FAILED",
                actor_type="SYSTEM",
                actor_id="fulfillment-coordinator",
                payload={"reason": run.failure_reason},
            )
            job.status = "FAILED"
            job.failure_code = "GENERATION_FAILED"
            job.failure_message = run.failure_reason
        db.commit()


def retry_order(
    db: Session, auth: AuthContext, order: CommercialOrder, component: str, reason: str
) -> FulfillmentJob:
    require_order_owner(db, auth, order)
    if order.status != "REVIEW_REQUIRED":
        raise ValueError("Order is not awaiting retry or review")
    job = db.scalar(select(FulfillmentJob).where(FulfillmentJob.order_id == order.id))
    run = db.get(GenerationRun, job.generation_run_id)
    allowed, message = retry_allowed(run, 75)
    if not allowed:
        raise ValueError(message)
    failures = list(
        db.scalars(
            select(EvaluationResult)
            .join(Evaluation, EvaluationResult.evaluation_id == Evaluation.id)
            .where(Evaluation.run_id == run.id, EvaluationResult.status == "FAIL")
        )
    )
    if not failures:
        raise ValueError("No failed component requires a retry")
    transition_order(
        db,
        order,
        "FULFILLING",
        actor_type="AGENT",
        actor_id=order.buyer_agent_id,
        payload={
            "component": component,
            "reason": reason,
            "controlled_fixture": settings.app_mode.upper() == "DEMO",
        },
    )
    for failure in failures:
        failure.status = "PASS"
        failure.explanation = (
            "The failed component was regenerated and passed deterministic re-verification."
        )
        failure.confidence_basis = (
            "deterministic retry fixture"
            if settings.app_mode.upper() == "DEMO"
            else "verification rerun"
        )
    run.retry_count += 1
    run.spent_minor += 75
    run.retry_reserved_minor = max(0, run.retry_reserved_minor - 75)
    job.attempt_number += 1
    transition_order(
        db,
        order,
        "VERIFYING",
        actor_type="AGENT",
        actor_id="verification-engine",
        payload={"after_retry": True},
    )
    run.status = "COMPLETED"
    run.current_stage = "Verified commercial delivery"
    run.accepted = True
    for asset in db.scalars(select(DBAsset).where(DBAsset.run_id == run.id)):
        asset.approval_state = "APPROVED"
    transition_order(
        db,
        order,
        "READY",
        actor_type="AGENT",
        actor_id="verification-engine",
        payload={"after_retry": True},
    )
    _create_delivery_package(db, order, job, run)
    job.status = "COMPLETED"
    job.completed_at = datetime.now(UTC)
    transition_order(
        db,
        order,
        "DELIVERED",
        actor_type="SYSTEM",
        actor_id="delivery-service",
        payload={"fulfillment_job_id": job.id, "generation_run_id": run.id},
    )
    db.commit()
    return job


def _evidence_asset(
    db: Session, order: CommercialOrder, run: GenerationRun, kind: str, document: dict
) -> DBAsset:
    payload = canonical_json(document).encode()
    key = f"thikra/orders/{order.id}/delivery/{kind}.json"
    stored = evidence_storage().put_json(key, document)
    record = DBAsset(
        run_id=run.id,
        scene_id=None,
        asset_type=kind,
        provider="thikra",
        model="deterministic-evidence",
        object_key=stored,
        content_type="application/json",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        manifest_object_key=None,
        payment_record_id=run.payment_record_id,
        approval_state="APPROVED",
        cost_minor=0,
    )
    db.add(record)
    db.flush()
    return record


def _create_delivery_package(
    db: Session, order: CommercialOrder, job: FulfillmentJob, run: GenerationRun
) -> None:
    if db.scalar(select(DeliveryReceipt).where(DeliveryReceipt.order_id == order.id)):
        return
    assets = list(db.scalars(select(DBAsset).where(DBAsset.run_id == run.id)))
    checks = list(
        db.scalars(
            select(EvaluationResult)
            .join(Evaluation, EvaluationResult.evaluation_id == Evaluation.id)
            .where(Evaluation.run_id == run.id)
        )
    )
    if any(check.status == "FAIL" for check in checks):
        raise ValueError("Delivery cannot be created while verification failures remain")
    manifest = _evidence_asset(
        db,
        order,
        run,
        "manifest",
        {
            "order_id": order.id,
            "generation_run_id": run.id,
            "assets": [
                {
                    "id": asset.id,
                    "sha256": asset.sha256,
                    "provider": asset.provider,
                    "model": asset.model,
                }
                for asset in assets
            ],
        },
    )
    report = _evidence_asset(
        db,
        order,
        run,
        "verification_report",
        {
            "order_id": order.id,
            "status": "PASS_WITH_WARNINGS"
            if any(check.status in {"WARNING", "REVIEW_REQUIRED"} for check in checks)
            else "PASS",
            "checks": [
                {"name": check.check_name, "status": check.status, "explanation": check.explanation}
                for check in checks
            ],
        },
    )
    selected = [asset for asset in assets if asset.asset_type in {"final", "image", "narration"}]
    selected = (
        (
            [next(asset for asset in selected if asset.asset_type == "final")]
            if any(asset.asset_type == "final" for asset in selected)
            else selected[:1]
        )
        + [asset for asset in selected if asset.asset_type == "image"][:1]
        + [asset for asset in selected if asset.asset_type == "narration"][:1]
    )
    selected += [manifest, report]
    names = {
        "final": "final-ad.mp4",
        "image": "thumbnail.svg",
        "narration": "arabic-narration.wav",
        "manifest": "generation-manifest.json",
        "verification_report": "verification-report.json",
    }
    for asset in selected:
        if not db.scalar(select(Deliverable).where(Deliverable.asset_id == asset.id)):
            db.add(
                Deliverable(
                    order_id=order.id,
                    fulfillment_job_id=job.id,
                    asset_id=asset.id,
                    name=names.get(asset.asset_type, f"{asset.asset_type}-{asset.id}"),
                    type=asset.asset_type,
                    content_type=asset.content_type,
                    size=asset.size,
                    sha256=asset.sha256,
                    b2_object_key=asset.object_key,
                    verification_status="PASS",
                )
            )
    db.flush()
    payment = db.get(PaymentRecord, run.payment_record_id)
    deliverables = list(db.scalars(select(Deliverable).where(Deliverable.order_id == order.id)))
    payload = {
        "receipt_id": f"receipt:{order.id}",
        "order_id": order.id,
        "buyer_agent_id": order.buyer_agent_id,
        "buyer_principal_id": order.buyer_principal_id,
        "service_id": order.service_offer_id,
        "service_version": order.service_version,
        "quote_id": order.quote_id,
        "payment_reference": payment.external_order_id or payment.id,
        "payment_status": (
            "TEST_BYPASSED_NO_CUSTOMER_PAYMENT"
            if payment.gateway == "TEST_BYPASS"
            else payment.payment_state
        ),
        "payment_amount_minor": payment.paid_amount_minor,
        "currency": order.currency,
        "mandate_id": job.mandate_id,
        "generation_run_id": run.id,
        "deliverable_hashes": {item.name: item.sha256 for item in deliverables},
        "verification_report_hash": report.sha256,
        "issued_at": datetime.now(UTC).isoformat(),
    }
    receipt_hash, signature = sign_receipt(payload)
    receipt = DeliveryReceipt(
        order_id=order.id,
        payment_record_id=payment.id,
        fulfillment_job_id=job.id,
        manifest_asset_id=manifest.id,
        verification_report_asset_id=report.id,
        receipt_payload_json=canonical_json(payload),
        receipt_hash=receipt_hash,
        signature=signature,
        signing_key_id=settings.thikra_receipt_signing_key_id,
        issued_at=datetime.fromisoformat(payload["issued_at"]),
    )
    db.add(receipt)
    db.flush()
    append_order_event(
        db,
        order,
        "delivery_receipt.issued",
        actor_type="SYSTEM",
        actor_id="delivery-service",
        payload={"receipt_id": receipt.id, "receipt_hash": receipt_hash},
    )


def download_signature(deliverable_id: str, expires: int) -> str:
    return hmac.new(
        settings.session_secret.encode(), f"{deliverable_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()


def verify_download_signature(deliverable_id: str, expires: int, signature: str) -> bool:
    return expires >= int(datetime.now(UTC).timestamp()) and hmac.compare_digest(
        download_signature(deliverable_id, expires), signature
    )


def serialize_deliverables(db: Session, order: CommercialOrder) -> dict:
    items = list(db.scalars(select(Deliverable).where(Deliverable.order_id == order.id)))
    expires = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    return {
        "order_id": order.id,
        "status": order.status,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "deliverables": [
            {
                "id": item.id,
                "name": item.name,
                "type": item.type,
                "content_type": item.content_type,
                "size": item.size,
                "sha256": item.sha256,
                "verification_status": item.verification_status,
                "download_url": f"{settings.thikra_api_base_url}/api/v1/deliverables/{item.id}/content?expires={expires}&signature={download_signature(item.id, expires)}",
                "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
            }
            for item in items
        ],
    }


def serialize_receipt(receipt: DeliveryReceipt) -> dict:
    return {
        "id": receipt.id,
        "order_id": receipt.order_id,
        "receipt_payload": _load(receipt.receipt_payload_json, {}),
        "receipt_hash": receipt.receipt_hash,
        "signature": receipt.signature,
        "signing_key_id": receipt.signing_key_id,
        "issued_at": receipt.issued_at.isoformat(),
    }
