"""Application services for briefs, routing, runs, verification, assets, and cases."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.repo import provider_catalog as catalog
from app.repo.pipelines import compile_mandate_proposal
from app.thikra.audit import append_event, canonical_json, verify_chain
from app.thikra.models import (
    Asset as DBAsset,
)
from app.thikra.models import (
    AssetRelation,
    AuditEvent,
    CaseNote,
    CreativeBrief,
    Evaluation,
    EvaluationResult,
    GenerationRun,
    IntegrationHealth,
    Mandate,
    MandateVersion,
    PaymentEvent,
    PaymentRecord,
    ProviderDecision,
    ProviderQuote,
    RedressCase,
    Scene,
    User,
    Workspace,
)
from app.thikra.schemas import BriefCreate, CreativeMandate
from app.thikra.state_machine import retry_allowed

DEMO_CAMPAIGN = "Noura Glow - Saudi launch"


def _load(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def workspace(db: Session) -> Workspace:
    item = db.scalar(select(Workspace).order_by(Workspace.created_at).limit(1))
    if item is None:
        raise RuntimeError("Thikra demo workspace was not initialized")
    return item


def principal(db: Session) -> User:
    item = db.scalar(select(User).order_by(User.created_at).limit(1))
    if item is None:
        raise RuntimeError("Thikra demo principal was not initialized")
    return item


def mandate_schema(version: MandateVersion) -> dict:
    return _load(version.schema_json, {})


def serialize_run(db: Session, run: GenerationRun, *, detail: bool = False) -> dict:
    payment = db.get(PaymentRecord, run.payment_record_id) if run.payment_record_id else None
    scenes = list(db.scalars(select(Scene).where(Scene.run_id == run.id).order_by(Scene.position)))
    events = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.run_id == run.id).order_by(AuditEvent.created_at)
        )
    )
    data = {
        "id": run.id,
        "campaign_name": run.campaign_name,
        "status": run.status,
        "current_stage": run.current_stage,
        "budget_cap_minor": run.budget_cap_minor,
        "authorized_minor": run.authorized_minor,
        "spent_minor": run.spent_minor,
        "retry_reserved_minor": run.retry_reserved_minor,
        "remaining_minor": max(0, run.authorized_minor - run.spent_minor),
        "currency": run.currency,
        "retry_count": run.retry_count,
        "maximum_retries": run.maximum_retries,
        "accepted": run.accepted,
        "human_escalation": run.human_escalation,
        "failure_reason": run.failure_reason,
        "provider_selection": _load(run.provider_selection_json, {}),
        "payment_state": payment.payment_state if payment else "NOT_REQUIRED",
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
        "latest_event": events[-1].event_type if events else "run.created",
    }
    if not detail:
        return data
    mandate = db.get(Mandate, run.mandate_id)
    version = db.scalar(
        select(MandateVersion).where(
            MandateVersion.mandate_id == run.mandate_id,
            MandateVersion.version == run.mandate_version,
        )
    )
    checks = list(
        db.scalars(
            select(EvaluationResult)
            .join(Evaluation, EvaluationResult.evaluation_id == Evaluation.id)
            .where(Evaluation.run_id == run.id)
            .order_by(EvaluationResult.created_at)
        )
    )
    run_assets = list(db.scalars(select(DBAsset).where(DBAsset.run_id == run.id)))
    data.update(
        {
            "brief_id": run.brief_id,
            "mandate_id": run.mandate_id,
            "mandate": mandate_schema(version) if version else {},
            "mandate_status": mandate.status if mandate else "UNKNOWN",
            "payment_id": payment.id if payment else None,
            "scenes": [serialize_scene(db, scene) for scene in scenes],
            "assets": [serialize_asset(db, asset) for asset in run_assets],
            "verification": [serialize_check(check) for check in checks],
            "timeline": [serialize_event(event) for event in events],
            "actions": action_policy(run, checks),
        }
    )
    return data


def serialize_scene(db: Session, scene: Scene) -> dict:
    assets = list(db.scalars(select(DBAsset).where(DBAsset.scene_id == scene.id)))
    return {
        "id": scene.id,
        "position": scene.position,
        "prompt": scene.prompt,
        "narration": scene.narration,
        "status": scene.status,
        "provider": scene.provider,
        "model": scene.model,
        "cost_minor": scene.cost_minor,
        "retry_count": scene.retry_count,
        "verification_state": scene.verification_state,
        "assets": [serialize_asset(db, asset) for asset in assets],
    }


def serialize_asset(db: Session, asset: DBAsset) -> dict:
    parents = list(
        db.scalars(
            select(AssetRelation.parent_asset_id).where(AssetRelation.child_asset_id == asset.id)
        )
    )
    children = list(
        db.scalars(
            select(AssetRelation.child_asset_id).where(AssetRelation.parent_asset_id == asset.id)
        )
    )
    return {
        "id": asset.id,
        "run_id": asset.run_id,
        "scene_id": asset.scene_id,
        "type": asset.asset_type,
        "provider": asset.provider,
        "model": asset.model,
        "object_key": asset.object_key,
        "content_type": asset.content_type,
        "size": asset.size,
        "sha256": asset.sha256,
        "manifest_object_key": asset.manifest_object_key,
        "payment_record_id": asset.payment_record_id,
        "approval_state": asset.approval_state,
        "cost_minor": asset.cost_minor,
        "parent_asset_ids": parents,
        "child_asset_ids": children,
        "preview_url": f"/api/thikra/assets/{asset.id}/content",
        "download_url": f"/api/thikra/assets/{asset.id}/download",
        "created_at": _iso(asset.created_at),
    }


def serialize_check(check: EvaluationResult) -> dict:
    return {
        "id": check.id,
        "check_name": check.check_name,
        "status": check.status,
        "explanation": check.explanation,
        "evidence": _load(check.evidence_json, {}),
        "confidence_basis": check.confidence_basis,
    }


def serialize_event(event: AuditEvent) -> dict:
    return {
        "event_id": event.id,
        "workspace_id": event.workspace_id,
        "run_id": event.run_id,
        "event_type": event.event_type,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "timestamp": _iso(event.created_at),
        "payload": _load(event.payload_json, {}),
        "related_object_ids": _load(event.related_object_ids_json, []),
        "previous_event_hash": event.previous_event_hash,
        "event_hash": event.event_hash,
    }


def serialize_payment(db: Session, payment: PaymentRecord, *, detail: bool = False) -> dict:
    data = {
        "id": payment.id,
        "run_id": payment.run_id,
        "mandate_id": payment.mandate_id,
        "gateway": payment.gateway,
        "environment": payment.environment,
        "external_session_id": payment.external_session_id,
        "external_order_id": payment.external_order_id,
        "merchant": payment.merchant,
        "currency": payment.currency,
        "maximum_amount_minor": payment.maximum_amount_minor,
        "invoked_amount_minor": payment.invoked_amount_minor,
        "authorization_state": payment.authorization_state,
        "payment_state": payment.payment_state,
        "redress_state": payment.redress_state,
        "expires_at": _iso(payment.expires_at),
        "created_at": _iso(payment.created_at),
    }
    if detail:
        events = list(
            db.scalars(
                select(PaymentEvent)
                .where(PaymentEvent.payment_id == payment.id)
                .order_by(PaymentEvent.created_at)
            )
        )
        assets = list(db.scalars(select(DBAsset).where(DBAsset.payment_record_id == payment.id)))
        data["events"] = [
            {
                "id": event.id,
                "type": event.event_type,
                "payload": _load(event.sanitized_payload_json, {}),
                "timestamp": _iso(event.created_at),
            }
            for event in events
        ]
        data["asset_ids"] = [asset.id for asset in assets]
    return data


def serialize_case(db: Session, case: RedressCase, *, detail: bool = False) -> dict:
    data = {
        "id": case.id,
        "run_id": case.run_id,
        "mandate_id": case.mandate_id,
        "payment_id": case.payment_id,
        "reason": case.reason,
        "severity": case.severity,
        "recommended_next_action": case.recommended_next_action,
        "owner": case.owner,
        "status": case.status,
        "resolution": case.resolution,
        "refund_reference": case.refund_reference,
        "created_at": _iso(case.created_at),
        "updated_at": _iso(case.updated_at),
    }
    if detail:
        notes = list(
            db.scalars(
                select(CaseNote).where(CaseNote.case_id == case.id).order_by(CaseNote.created_at)
            )
        )
        data["evidence_snapshot"] = _load(case.evidence_snapshot_json, {})
        data["notes"] = [
            {
                "id": note.id,
                "author": note.author,
                "body": note.body,
                "created_at": _iso(note.created_at),
            }
            for note in notes
        ]
    return data


def action_policy(run: GenerationRun, checks: list[EvaluationResult]) -> dict:
    failures = any(check.status == "FAIL" for check in checks)
    can_retry, reason = retry_allowed(run, 115)
    review = run.status == "HUMAN_REVIEW"
    return {
        "approve": {
            "enabled": review and not failures,
            "reason": "Resolve failed checks first" if failures else "",
        },
        "reject": {"enabled": review, "reason": ""},
        "retry_component": {"enabled": review and failures and can_retry, "reason": reason},
        "retry_run": {"enabled": review and can_retry, "reason": reason},
        "open_case": {"enabled": run.status not in {"CANCELLED"}, "reason": ""},
        "cancel": {"enabled": run.status not in {"COMPLETED", "CANCELLED"}, "reason": ""},
    }


def compile_brief(db: Session, request: BriefCreate) -> dict:
    ws = workspace(db)
    user = principal(db)
    brief = CreativeBrief(
        workspace_id=ws.id,
        principal_id=user.id,
        campaign_name=request.campaign_name,
        objective=request.objective,
        source_json=canonical_json(request.model_dump(mode="json")),
    )
    db.add(brief)
    db.flush()
    mandate = Mandate(
        workspace_id=ws.id,
        brief_id=brief.id,
        principal_id=user.id,
        current_version=1,
        status="PROPOSED",
    )
    db.add(mandate)
    db.flush()
    first = request.deliverables[0]
    created = datetime.now(UTC)
    proposal = None
    if settings.app_mode.upper() != "DEMO":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required to compile mandates outside demo mode")
        proposal = compile_mandate_proposal(request.model_dump(mode="json"))
    schema = CreativeMandate(
        mandate_id=mandate.id,
        version=1,
        principal_id=user.id,
        objective=proposal.objective if proposal else request.objective,
        deliverables=request.deliverables,
        target_audience=proposal.target_audience if proposal else request.target_audience,
        language=proposal.language if proposal else request.language,
        tone=proposal.tone if proposal else request.tone,
        budget_cap_minor=request.maximum_budget_minor,
        currency=request.currency,
        deadline=request.deadline,
        allowed_modalities=[item.modality for item in request.deliverables],
        allowed_providers=request.permitted_providers,
        forbidden_providers=request.forbidden_providers,
        allowed_models=[],
        forbidden_models=[],
        required_aspect_ratio=first.aspect_ratio,
        required_resolution=first.resolution,
        required_duration_sec=first.duration_sec,
        commercial_use_required=request.commercial_use_required,
        likeness_policy=request.likeness_restrictions,
        forbidden_elements=proposal.forbidden_elements if proposal else request.forbidden_elements,
        required_elements=proposal.required_elements if proposal else request.required_elements,
        claim_constraints=proposal.claim_constraints if proposal else request.claim_constraints,
        attribution_requirements=request.attribution_requirements,
        maximum_retries=request.maximum_retries,
        retry_budget_minor=(
            0 if request.maximum_retries == 0 else min(request.maximum_budget_minor // 4, 500)
        ),
        human_review_triggers=[
            "User approval required before final delivery",
            f"Spend at or above {request.human_approval_threshold_minor} minor units",
            "Any rights, likeness, or claims uncertainty",
            *(proposal.human_review_triggers if proposal else []),
        ],
        created_at=created,
        expires_at=request.deadline,
    )
    version = MandateVersion(
        mandate_id=mandate.id,
        version=1,
        schema_json=canonical_json(schema.model_dump(mode="json")),
        edit_summary="Compiled from the submitted creative brief",
    )
    db.add(version)
    append_event(
        db,
        workspace_id=ws.id,
        run_id=None,
        event_type="brief.created",
        actor_type="USER",
        actor_id=user.id,
        payload={"campaign": request.campaign_name},
        related_object_ids=[brief.id],
    )
    append_event(
        db,
        workspace_id=ws.id,
        run_id=None,
        event_type="mandate.compiled",
        actor_type="AGENT",
        actor_id="mandate-compiler",
        payload={
            "version": 1,
            "compiler": "deterministic-demo"
            if settings.app_mode == "DEMO"
            else "structured-policy",
        },
        related_object_ids=[mandate.id, version.id],
    )
    db.commit()
    return {
        "brief_id": brief.id,
        "mandate_id": mandate.id,
        "version_id": version.id,
        "mandate": schema.model_dump(mode="json"),
    }


def confirm_mandate(db: Session, mandate_id: str) -> dict:
    mandate = db.get(Mandate, mandate_id)
    if mandate is None:
        raise LookupError("Mandate not found")
    version = db.scalar(
        select(MandateVersion).where(
            MandateVersion.mandate_id == mandate.id,
            MandateVersion.version == mandate.current_version,
        )
    )
    mandate.status = "CONFIRMED"
    version.confirmed = True
    append_event(
        db,
        workspace_id=mandate.workspace_id,
        run_id=None,
        event_type="mandate.confirmed",
        actor_type="USER",
        actor_id=mandate.principal_id,
        payload={"version": mandate.current_version},
        related_object_ids=[mandate.id],
    )
    db.commit()
    return {"status": mandate.status, "mandate": mandate_schema(version)}


def provider_strategy(
    db: Session,
    mandate_id: str,
    selections: dict | None = None,
    *,
    slots: set[str] | None = None,
) -> dict:
    mandate = db.get(Mandate, mandate_id)
    if mandate is None:
        raise LookupError("Mandate not found")
    version = db.scalar(
        select(MandateVersion).where(
            MandateVersion.mandate_id == mandate.id,
            MandateVersion.version == mandate.current_version,
        )
    )
    policy = mandate_schema(version)
    forbidden = set(policy.get("forbidden_providers", []))
    allowed = set(policy.get("allowed_providers", []))
    matrix = catalog.matrix()
    quotes: list[dict] = []
    selection: dict[str, dict[str, str]] = {}
    base_cost = {"chat": 2, "image": 18, "video": 105, "tts": 8, "music": 15}
    latency = {"chat": 4, "image": 18, "video": 95, "tts": 7, "music": 35}
    for slot, entries in matrix.items():
        if slots is not None and slot not in slots:
            continue
        candidates = []
        for position, entry in enumerate(entries):
            configured = entry["key_available"] or settings.app_mode == "DEMO"
            compliant = entry["vendor"] not in forbidden and (
                not allowed or entry["vendor"] in allowed
            )
            quality = max(55, 91 - position * 5)
            reliability = max(70, 96 - position * 3)
            cost = base_cost[slot] + position * 3
            score = (
                quality
                + reliability
                + (20 if configured else -80)
                + (25 if compliant else -200)
                - cost
            )
            item = {
                **entry,
                "configured": configured,
                "estimated_cost_minor": cost,
                "estimated_cost": True,
                "estimated_latency_sec": latency[slot] + position * 4,
                "quality_prior": quality,
                "reliability_percent": reliability,
                "commercial_use_note": "Provider terms must be verified for the selected plan",
                "supports_seed": slot in {"image", "video"},
                "supports_reference_input": slot == "video",
                "supports_async_generation": slot in {"image", "video", "music"},
                "supports_provenance_metadata": True,
                "score": score,
                "compliant": compliant,
            }
            candidates.append(item)
            db.add(
                ProviderQuote(
                    mandate_id=mandate.id,
                    provider=entry["vendor"],
                    model=entry["default_model"],
                    modality=slot,
                    estimated_cost_minor=cost,
                    estimated_latency_sec=item["estimated_latency_sec"],
                    score=score,
                    explanation="Transparent weighted score: capability + quality + reliability + configuration - cost - rights uncertainty.",
                )
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        manual = (selections or {}).get(slot)
        if manual:
            chosen = next((c for c in candidates if c["vendor"] == manual.get("vendor")), None)
            if chosen is None:
                raise ValueError(f"Selected provider '{manual.get('vendor')}' is unavailable for {slot}")
            if not chosen["compliant"]:
                raise ValueError(f"Selected provider '{chosen['vendor']}' conflicts with the mandate for {slot}")
            if not chosen["configured"]:
                raise ValueError(f"Selected provider '{chosen['vendor']}' is not configured for {slot}")
        else:
            # A configured preferred video vendor is the product default for
            # unconstrained briefs. It still yields to the mandate: an
            # allow-list or forbidden-provider policy simply falls back to the
            # highest-scoring compliant candidate.
            preferred_vendor = settings.video_provider if slot == "video" else None
            chosen = next(
                (
                    c
                    for c in candidates
                    if c["vendor"] == preferred_vendor and c["configured"] and c["compliant"]
                ),
                None,
            )
            if chosen is None:
                chosen = next(
                    (c for c in candidates if c["configured"] and c["compliant"]), None
                )
            if chosen is None:
                raise ValueError(f"No configured mandate-compliant provider is available for {slot}")
        selection[slot] = {
            "vendor": chosen["vendor"],
            "model": (manual or {}).get("model") or chosen["default_model"],
        }
        quotes.extend(candidates)
    decision = ProviderDecision(
        mandate_id=mandate.id,
        selection_json=canonical_json(selection),
        candidate_scores_json=canonical_json(quotes),
        explanation="Selected the highest-scoring configured, mandate-compliant provider for each modality. Costs are estimates.",
        manual_override=bool(selections),
    )
    db.add(decision)
    append_event(
        db,
        workspace_id=mandate.workspace_id,
        run_id=None,
        event_type="provider.selected",
        actor_type="AGENT",
        actor_id="provider-router",
        payload={"selection": selection, "estimated": True},
        related_object_ids=[mandate.id, decision.id],
    )
    db.commit()
    return {
        "decision_id": decision.id,
        "selection": selection,
        "quotes": quotes,
        "explanation": decision.explanation,
    }


def launch_run(
    db: Session, mandate_id: str, payment: PaymentRecord, selection: dict, key: str
) -> GenerationRun:
    existing = db.scalar(select(GenerationRun).where(GenerationRun.idempotency_key == key))
    if existing:
        return existing
    mandate = db.get(Mandate, mandate_id)
    if mandate is None or mandate.status != "CONFIRMED":
        raise ValueError("The mandate must be confirmed before launch")
    version = db.scalar(
        select(MandateVersion).where(
            MandateVersion.mandate_id == mandate.id,
            MandateVersion.version == mandate.current_version,
        )
    )
    schema = mandate_schema(version)
    brief = db.get(CreativeBrief, mandate.brief_id)
    run = GenerationRun(
        workspace_id=mandate.workspace_id,
        brief_id=mandate.brief_id,
        mandate_id=mandate.id,
        mandate_version=mandate.current_version,
        payment_record_id=payment.id,
        campaign_name=brief.campaign_name,
        status="PLANNING",
        current_stage="Storyboard review",
        currency=schema["currency"],
        budget_cap_minor=schema["budget_cap_minor"],
        authorized_minor=payment.maximum_amount_minor,
        spent_minor=0,
        retry_reserved_minor=schema["retry_budget_minor"],
        maximum_retries=schema["maximum_retries"],
        provider_selection_json=canonical_json(selection),
        idempotency_key=key,
    )
    db.add(run)
    db.flush()
    payment.run_id = run.id
    video = selection.get("video", {"vendor": "replicate", "model": "minimax/video-01"})
    brief_source = json.loads(brief.source_json)
    creative_brief = str(brief_source.get("creative_brief") or brief.objective).strip()
    required_elements = [str(item) for item in brief_source.get("required_elements", [])]
    prompt_suffix = (
        " Required elements: " + "; ".join(required_elements) + "."
        if required_elements
        else ""
    )
    narration = str(brief_source.get("objective") or brief.objective).strip()
    required_duration = int(schema["required_duration_sec"])
    # A short single-clip order must not silently expand into the legacy
    # three-scene explainer shape.  Longer products keep its three 5-second
    # beats; the local smoke path uses one provider clip at the quoted length.
    scene_count = 1 if required_duration <= 5 else 3
    for position in range(1, scene_count + 1):
        prompt = (
            creative_brief
            + prompt_suffix
            + " Composition requirement: show every required element together "
            "in one clear, uncropped frame. Do not isolate, crop out, or replace "
            "the primary subject or any required element with a close-up."
        )
        if scene_count > 1:
            prompt += f" Story beat {position} of {scene_count}."
        db.add(
            Scene(
                run_id=run.id,
                position=position,
                prompt=prompt,
                narration=narration,
                status="PLANNED",
                provider=video["vendor"],
                model=video["model"],
                verification_state="PENDING",
            )
        )
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="storyboard.generated",
        actor_type="AGENT",
        actor_id="thikra-orchestrator",
        payload={"mode": settings.app_mode, "selection": selection, "stage": "storyboard_review"},
        related_object_ids=[run.id, mandate.id, payment.id],
    )
    db.commit()
    return run


def materialize_demo_delivery(db: Session, run_id: str) -> None:
    run = db.get(GenerationRun, run_id)
    if run is None or db.scalar(select(func.count(DBAsset.id)).where(DBAsset.run_id == run_id)):
        return
    selection = _load(run.provider_selection_json, {})
    video = selection.get("video", {"vendor": "replicate", "model": "minimax/video-01"})
    scenes = list(db.scalars(select(Scene).where(Scene.run_id == run.id).order_by(Scene.position)))
    assets: list[DBAsset] = []
    for index, scene in enumerate(scenes, start=1):
        scene.status = "COMPLETED"
        scene.provider = video["vendor"]
        scene.model = video["model"]
        scene.cost_minor = 145
        scene.verification_state = "FAIL" if index == 2 else "PASS"
        keyframe = DBAsset(
            run_id=run.id,
            scene_id=scene.id,
            asset_type="image",
            provider="replicate",
            model="black-forest-labs/flux-schnell",
            object_key=f"demo://thikra/workspaces/{run.workspace_id}/runs/{run.id}/scenes/{scene.id}/keyframe.svg",
            content_type="image/svg+xml",
            size=2450,
            sha256=hashlib.sha256(f"{run.id}:keyframe:{index}".encode()).hexdigest(),
            manifest_object_key=f"demo://thikra/workspaces/{run.workspace_id}/runs/{run.id}/manifests/stage-b1.json",
            payment_record_id=run.payment_record_id,
            approval_state="PENDING",
            cost_minor=18,
        )
        db.add(keyframe)
        db.flush()
        assets.append(keyframe)
        if index != 2:
            audio = DBAsset(
                run_id=run.id,
                scene_id=scene.id,
                asset_type="narration",
                provider="openai",
                model="gpt-4o-mini-tts",
                object_key=f"demo://thikra/workspaces/{run.workspace_id}/runs/{run.id}/scenes/{scene.id}/narration.wav",
                content_type="audio/wav",
                size=48200,
                sha256=hashlib.sha256(f"{run.id}:audio:{index}".encode()).hexdigest(),
                manifest_object_key=f"demo://thikra/workspaces/{run.workspace_id}/runs/{run.id}/manifests/stage-b2.json",
                payment_record_id=run.payment_record_id,
                approval_state="PENDING",
                cost_minor=8,
            )
            db.add(audio)
            assets.append(audio)
    final = DBAsset(
        run_id=run.id,
        scene_id=None,
        asset_type="final",
        provider="ffmpeg",
        model="composition",
        object_key=f"demo://thikra/workspaces/{run.workspace_id}/runs/{run.id}/final/noura-glow.mp4",
        content_type="video/mp4",
        size=4_820_000,
        sha256=hashlib.sha256(f"{run.id}:final".encode()).hexdigest(),
        manifest_object_key=f"demo://thikra/workspaces/{run.workspace_id}/runs/{run.id}/manifests/final.json",
        payment_record_id=run.payment_record_id,
        approval_state="PENDING",
        cost_minor=0,
    )
    db.add(final)
    db.flush()
    for asset in assets:
        db.add(
            AssetRelation(
                parent_asset_id=asset.id, child_asset_id=final.id, relation_type="COMPOSED_INTO"
            )
        )
    evaluation = Evaluation(
        run_id=run.id, asset_id=final.id, layer="LAYERED", status="REVIEW_REQUIRED"
    )
    db.add(evaluation)
    db.flush()
    checks = [
        (
            "File integrity and SHA-256",
            "PASS",
            "The fixture is readable and its recorded hash is stable.",
            "deterministic",
        ),
        (
            "Vertical 9:16 aspect ratio",
            "PASS",
            "All three scene canvases match the mandate.",
            "deterministic metadata",
        ),
        (
            "Arabic narration present",
            "FAIL",
            "Scene 2 has no narration track; retry this component.",
            "audio stream presence",
        ),
        (
            "No medical claims",
            "PASS",
            "Captions and narration contain no treatment or efficacy claim.",
            "rule-based text scan",
        ),
        (
            "Real-person likeness risk",
            "NOT_APPLICABLE",
            "The controlled fixture contains product-only abstract imagery.",
            "fixture declaration",
        ),
        (
            "Commercial-use rights",
            "WARNING",
            "Provider terms are recorded but require account-plan confirmation.",
            "provider metadata",
        ),
        (
            "Final human approval",
            "REVIEW_REQUIRED",
            "The mandate explicitly requires principal approval.",
            "mandate policy",
        ),
    ]
    for name, status, explanation, basis in checks:
        db.add(
            EvaluationResult(
                evaluation_id=evaluation.id,
                check_name=name,
                status=status,
                explanation=explanation,
                evidence_json=canonical_json({"source": basis}),
                confidence_basis=basis,
            )
        )
    run.status = "HUMAN_REVIEW"
    run.current_stage = "Human review requested"
    run.spent_minor = 525
    run.human_escalation = True
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="evaluation.completed",
        actor_type="AGENT",
        actor_id="verification-engine",
        payload={"result": "FAIL", "failed_check": "Arabic narration present"},
        related_object_ids=[evaluation.id],
    )
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="human_review.requested",
        actor_type="POLICY",
        actor_id="mandate-policy",
        payload={"reason": "Explicit approval + failed narration check"},
        related_object_ids=[run.id],
    )
    db.commit()


def seed_database(db: Session) -> None:
    if db.scalar(select(Workspace).limit(1)):
        return
    ws = Workspace(name="Thikra Demo Studio", environment=settings.app_mode.upper())
    db.add(ws)
    db.flush()
    user = User(workspace_id=ws.id, email="brand.manager@thikra.demo", name="Noura Brand Manager")
    db.add(user)
    db.flush()
    deadline = datetime.now(UTC) + timedelta(days=7)
    request = BriefCreate.model_validate(
        {
            "campaign_name": DEMO_CAMPAIGN,
            "product": "Noura Glow skincare",
            "objective": "Create three warm, modern vertical advertisements for young adults in Saudi Arabia.",
            "target_audience": "Young adults in Saudi Arabia",
            "language": "Arabic",
            "tone": "Warm and modern",
            "creative_brief": "Create three 15-second vertical advertisements for Noura Glow. Use Arabic narration, show the product clearly, use no real people, and invent no medical claims.",
            "deliverables": [
                {
                    "modality": "combined_advertisement",
                    "variants": 3,
                    "duration_sec": 15,
                    "aspect_ratio": "9:16",
                    "resolution": "1080x1920",
                    "file_format": "mp4",
                }
            ],
            "maximum_budget_minor": 2000,
            "currency": "USD",
            "deadline": deadline,
            "maximum_retries": 2,
            "permitted_providers": ["openai", "replicate", "google", "runway"],
            "forbidden_providers": [],
            "human_approval_threshold_minor": 1000,
            "commercial_use_required": True,
            "likeness_restrictions": "No real human or celebrity likenesses",
            "forbidden_elements": ["medical before-and-after imagery", "celebrity likenesses"],
            "required_elements": ["Noura Glow product visible in every variant"],
            "claim_constraints": ["Do not invent medical or therapeutic claims"],
            "required_language": "Arabic",
            "attribution_requirements": ["Record provider and model provenance"],
        }
    )
    compiled = compile_brief(db, request)
    confirm_mandate(db, compiled["mandate_id"])
    strategy = provider_strategy(db, compiled["mandate_id"])
    payment = PaymentRecord(
        workspace_id=ws.id,
        mandate_id=compiled["mandate_id"],
        gateway="DEMO",
        environment="DEMO",
        external_session_id="demo_sess_seeded_noura",
        external_order_id="demo_order_seeded_noura",
        merchant="Replicate (simulated)",
        currency="USD",
        maximum_amount_minor=2000,
        invoked_amount_minor=525,
        authorization_state="AUTHORIZED",
        payment_state="SIMULATED_INVOKED",
        expires_at=deadline,
    )
    db.add(payment)
    db.flush()
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="payment.authorization_approved",
            sanitized_payload_json=canonical_json(
                {"simulated": True, "maximum_amount_minor": 2000}
            ),
            idempotency_key="seed-payment-authorized",
        )
    )
    run = launch_run(
        db, compiled["mandate_id"], payment, strategy["selection"], "seed-noura-glow-run"
    )
    materialize_demo_delivery(db, run.id)
    case = RedressCase(
        workspace_id=ws.id,
        mandate_id=run.mandate_id,
        run_id=run.id,
        payment_id=payment.id,
        reason="Arabic narration was missing from scene 2 in the controlled demo fixture.",
        severity="MEDIUM",
        evidence_snapshot_json=canonical_json({"failed_check": "Arabic narration present"}),
        recommended_next_action="Retry scene 2 narration, rerun verification, then request approval.",
        owner="Trust operations",
        status="OPEN",
    )
    db.add(case)
    db.flush()
    append_event(
        db,
        workspace_id=ws.id,
        run_id=run.id,
        event_type="case.opened",
        actor_type="USER",
        actor_id=user.id,
        payload={"reason": case.reason},
        related_object_ids=[case.id],
    )
    integrations = {
        "Backblaze B2": (bool(settings.b2_bucket_name), ["storage", "provenance"]),
        "Prava": (bool(settings.prava_secret_key), ["authorization", "payment credentials"]),
        "OpenAI": (bool(settings.openai_api_key), ["chat", "image", "tts"]),
        "Replicate": (bool(settings.replicate_api_token), ["image", "video", "music"]),
        "Google": (bool(settings.google_api_key), ["image", "video"]),
        "NVIDIA NIM": (bool(settings.nvidia_api_key), ["image", "video", "tts"]),
        "Decart": (bool(settings.decart_api_key), ["image", "video"]),
        "GMI Cloud": (bool(settings.gmi_api_key), ["video", "music"]),
        "Runway": (bool(settings.runway_api_secret), ["video"]),
        "Luma": (bool(settings.luma_api_key), ["video"]),
        "ElevenLabs": (bool(settings.elevenlabs_api_key), ["tts"]),
        "LMNT": (bool(settings.lmnt_api_key), ["tts"]),
        "Hume": (bool(settings.hume_api_key), ["tts"]),
        "ffmpeg": (True, ["composition", "technical verification"]),
    }
    for name, (configured, modalities) in integrations.items():
        db.add(
            IntegrationHealth(
                workspace_id=ws.id,
                integration=name,
                configured=configured,
                healthy=configured or settings.app_mode == "DEMO",
                supported_modalities_json=canonical_json(modalities),
                last_success_at=datetime.now(UTC) if configured else None,
                message="Using a clearly labeled demo fixture"
                if settings.app_mode == "DEMO" and not configured
                else "Ready",
            )
        )
    db.commit()


def overview(db: Session) -> dict:
    ws = workspace(db)
    runs = list(db.scalars(select(GenerationRun).order_by(GenerationRun.created_at.desc())))
    assets = list(db.scalars(select(DBAsset).order_by(DBAsset.created_at.desc()).limit(6)))
    events = list(db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(8)))
    total_budget = sum(run.authorized_minor for run in runs)
    total_spend = sum(run.spent_minor for run in runs)
    accepted = [run for run in runs if run.accepted]
    retries = sum(run.retry_count for run in runs)
    provider_counts: dict[str, int] = {}
    modality_costs: dict[str, int] = {}
    for asset in db.scalars(select(DBAsset)):
        provider_counts[asset.provider] = provider_counts.get(asset.provider, 0) + 1
        modality_costs[asset.asset_type] = (
            modality_costs.get(asset.asset_type, 0) + asset.cost_minor
        )
    return {
        "environment": ws.environment,
        "demo_data": settings.app_mode == "DEMO",
        "metrics": {
            "active_runs": sum(
                run.status not in {"COMPLETED", "CANCELLED", "FAILED"} for run in runs
            ),
            "completed_runs": sum(run.status == "COMPLETED" for run in runs),
            "authorized_minor": total_budget,
            "spent_minor": total_spend,
            "savings_minor": max(0, total_budget - total_spend),
            "acceptance_rate": round(len(accepted) / len(runs) * 100) if runs else 0,
            "retry_rate": round(retries / len(runs) * 100) if runs else 0,
            "escalation_rate": round(sum(run.human_escalation for run in runs) / len(runs) * 100)
            if runs
            else 0,
            "failed_deliveries": sum(run.status == "FAILED" for run in runs),
        },
        "runs": [serialize_run(db, run) for run in runs[:5]],
        "recent_assets": [serialize_asset(db, asset) for asset in assets],
        "recent_events": [serialize_event(event) for event in events],
        "provider_usage": [
            {"label": key, "value": value} for key, value in provider_counts.items()
        ],
        "cost_by_modality": [
            {"label": key, "value": value} for key, value in modality_costs.items()
        ],
    }


def evidence_export(db: Session, run_id: str) -> dict:
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise LookupError("Run not found")
    events = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.created_at)
        )
    )
    versions = list(
        db.scalars(select(MandateVersion).where(MandateVersion.mandate_id == run.mandate_id))
    )
    assets = list(db.scalars(select(DBAsset).where(DBAsset.run_id == run.id)))
    cases = list(db.scalars(select(RedressCase).where(RedressCase.run_id == run.id)))
    return {
        "run": serialize_run(db, run, detail=True),
        "mandate_versions": [mandate_schema(version) for version in versions],
        "asset_hashes": [
            {"asset_id": asset.id, "sha256": asset.sha256, "object_key": asset.object_key}
            for asset in assets
        ],
        "audit_chain_valid": verify_chain(events),
        "audit_chain": [serialize_event(event) for event in events],
        "cases": [serialize_case(db, case, detail=True) for case in cases],
        "exported_at": datetime.now(UTC).isoformat(),
        "redacted": True,
    }
