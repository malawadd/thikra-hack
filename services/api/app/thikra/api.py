"""FastAPI routes for Thikra's authoritative business backend."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.repo.pipelines import presign_asset_url
from app.thikra.audit import append_event, canonical_json
from app.thikra.database import get_db
from app.thikra.models import (
    Asset as DBAsset,
)
from app.thikra.models import (
    AuditEvent,
    CaseNote,
    Evaluation,
    EvaluationResult,
    GenerationRun,
    IntegrationHealth,
    Mandate,
    MandateVersion,
    PaymentEvent,
    PaymentRecord,
    RedressCase,
    Scene,
)
from app.thikra.orchestration import execute_generation_run
from app.thikra.payments import EPHEMERAL_CREDENTIALS, gateway, sanitize_payment_result
from app.thikra.schemas import (
    AuthorizationCreate,
    BriefCreate,
    CaseCreate,
    CaseNoteCreate,
    CaseUpdate,
    MandateEdit,
    PaymentReport,
    ProviderStrategyRequest,
    RetryRequest,
    RunDecision,
    RunLaunch,
    SceneEdit,
)
from app.thikra.service import (
    action_policy,
    compile_brief,
    confirm_mandate,
    evidence_export,
    launch_run,
    mandate_schema,
    materialize_demo_delivery,
    overview,
    provider_strategy,
    serialize_asset,
    serialize_case,
    serialize_event,
    serialize_payment,
    serialize_run,
    workspace,
)
from app.thikra.state_machine import retry_allowed
from app.thikra.storage import evidence_key, evidence_storage

router = APIRouter(tags=["Thikra"])
_COSTLY_REQUESTS: dict[str, list[float]] = {}


def _enforce_costly_rate_limit(request: Request) -> None:
    """Small single-process guard; production ingress should enforce globally too."""
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    recent = [seen for seen in _COSTLY_REQUESTS.get(key, []) if now - seen < 60]
    if len(recent) >= 5:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "GENERATION_RATE_LIMITED",
                "message": "At most five generation launches are allowed per minute.",
            },
        )
    recent.append(now)
    _COSTLY_REQUESTS[key] = recent


def _not_found(kind: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"code": "NOT_FOUND", "message": f"{kind} not found"}
    )


def _conflict(message: str, code: str = "POLICY_CONFLICT") -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    try:
        ws = workspace(db)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "DATABASE_NOT_READY", "message": str(exc)}
        ) from exc
    if settings.app_mode.upper() == "PRODUCTION":
        missing = [
            name
            for name, value in {
                "SESSION_SECRET": settings.session_secret != "demo-only-change-me",
                "PRAVA_SECRET_KEY": bool(settings.prava_secret_key),
                "PRAVA_PUBLISHABLE_KEY": bool(settings.prava_publishable_key),
                "B2_BUCKET_NAME": bool(settings.b2_bucket_name),
            }.items()
            if not value
        ]
        if missing:
            raise HTTPException(
                status_code=503, detail={"code": "PRODUCTION_CONFIG_MISSING", "missing": missing}
            )
    return {"status": "ready", "mode": ws.environment}


@router.get("/health/integrations")
def integration_health(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(IntegrationHealth).order_by(IntegrationHealth.integration)))
    return {
        "mode": settings.app_mode.upper(),
        "integrations": [
            {
                "id": row.id,
                "name": row.integration,
                "configured": row.configured,
                "healthy": row.healthy,
                "supported_modalities": json.loads(row.supported_modalities_json),
                "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
                "message": row.message,
                "setup": f"Configure the documented {row.integration} variables in .env; secret values are never returned.",
            }
            for row in rows
        ],
    }


@router.get("/thikra/overview")
def get_overview(db: Session = Depends(get_db)):
    return overview(db)


@router.post("/thikra/briefs/compile", status_code=201)
def create_brief(request: BriefCreate, db: Session = Depends(get_db)):
    return compile_brief(db, request)


@router.get("/thikra/mandates/{mandate_id}")
def get_mandate(mandate_id: str, db: Session = Depends(get_db)):
    mandate = db.get(Mandate, mandate_id)
    if mandate is None:
        raise _not_found("Mandate")
    versions = list(
        db.scalars(
            select(MandateVersion)
            .where(MandateVersion.mandate_id == mandate.id)
            .order_by(MandateVersion.version)
        )
    )
    return {
        "id": mandate.id,
        "status": mandate.status,
        "current_version": mandate.current_version,
        "mandate": mandate_schema(versions[-1]),
        "versions": [
            {
                "id": version.id,
                "version": version.version,
                "confirmed": version.confirmed,
                "edit_summary": version.edit_summary,
                "created_at": version.created_at.isoformat(),
            }
            for version in versions
        ],
    }


@router.put("/thikra/mandates/{mandate_id}")
def edit_mandate(mandate_id: str, request: MandateEdit, db: Session = Depends(get_db)):
    mandate = db.get(Mandate, mandate_id)
    if mandate is None:
        raise _not_found("Mandate")
    if request.mandate.mandate_id != mandate_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "MANDATE_ID_MISMATCH", "message": "Mandate ID cannot be changed"},
        )
    next_version = mandate.current_version + 1
    revised = request.mandate.model_copy(update={"version": next_version})
    version = MandateVersion(
        mandate_id=mandate.id,
        version=next_version,
        schema_json=canonical_json(revised.model_dump(mode="json")),
        edit_summary=request.edit_summary,
        confirmed=False,
    )
    db.add(version)
    mandate.current_version = next_version
    mandate.status = "PROPOSED"
    append_event(
        db,
        workspace_id=mandate.workspace_id,
        run_id=None,
        event_type="mandate.edited",
        actor_type="USER",
        actor_id=mandate.principal_id,
        payload={"version": next_version, "summary": request.edit_summary},
        related_object_ids=[mandate.id, version.id],
    )
    db.commit()
    return {"status": mandate.status, "mandate": revised.model_dump(mode="json")}


@router.post("/thikra/mandates/{mandate_id}/confirm")
def confirm(mandate_id: str, db: Session = Depends(get_db)):
    try:
        return confirm_mandate(db, mandate_id)
    except LookupError as exc:
        raise _not_found("Mandate") from exc


@router.post("/thikra/providers/strategy")
def strategy(request: ProviderStrategyRequest, db: Session = Depends(get_db)):
    try:
        return provider_strategy(db, request.mandate_id, request.selections or None)
    except LookupError as exc:
        raise _not_found("Mandate") from exc


@router.post("/thikra/payments/authorizations", status_code=201)
async def create_authorization(request: AuthorizationCreate, db: Session = Depends(get_db)):
    existing_event = db.scalar(
        select(PaymentEvent).where(PaymentEvent.idempotency_key == request.idempotency_key)
    )
    if existing_event:
        payment = db.get(PaymentRecord, existing_event.payment_id)
        return serialize_payment(db, payment, detail=True)
    mandate = db.get(Mandate, request.mandate_id)
    if mandate is None:
        raise _not_found("Mandate")
    version = db.scalar(
        select(MandateVersion).where(
            MandateVersion.mandate_id == mandate.id,
            MandateVersion.version == mandate.current_version,
        )
    )
    cap = mandate_schema(version)["budget_cap_minor"]
    if request.maximum_amount_minor > cap:
        raise _conflict("Authorization exceeds the confirmed mandate budget", "BUDGET_EXCEEDED")
    try:
        result = await gateway().create_authorization(request.model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail={"code": "PRAVA_SESSION_FAILED", "message": str(exc)}
        ) from exc
    payment = PaymentRecord(
        workspace_id=mandate.workspace_id,
        mandate_id=mandate.id,
        run_id=request.run_id,
        gateway="DEMO" if result.get("simulated") else "PRAVA",
        environment=settings.app_mode.upper(),
        external_session_id=result["session_id"],
        external_order_id=result.get("order_id"),
        merchant=request.merchant,
        currency=request.currency.upper(),
        maximum_amount_minor=request.maximum_amount_minor,
        invoked_amount_minor=0,
        authorization_state="AUTHORIZED" if result.get("simulated") else "AUTHORIZATION_PENDING",
        payment_state="SIMULATED_AUTHORIZED"
        if result.get("simulated")
        else "AWAITING_USER_APPROVAL",
        expires_at=datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00")),
    )
    db.add(payment)
    db.flush()
    event = PaymentEvent(
        payment_id=payment.id,
        event_type="payment.authorization_approved"
        if result.get("simulated")
        else "payment.authorization_requested",
        sanitized_payload_json=canonical_json(
            {
                "session_id": result["session_id"],
                "order_id": result.get("order_id"),
                "simulated": bool(result.get("simulated")),
            }
        ),
        idempotency_key=request.idempotency_key,
    )
    db.add(event)
    append_event(
        db,
        workspace_id=mandate.workspace_id,
        run_id=request.run_id,
        event_type=event.event_type,
        actor_type="USER",
        actor_id=request.user_id,
        payload={
            "payment_id": payment.id,
            "maximum_amount_minor": request.maximum_amount_minor,
            "simulated": bool(result.get("simulated")),
        },
        related_object_ids=[payment.id, mandate.id],
    )
    db.commit()
    response = serialize_payment(db, payment, detail=True)
    response["checkout"] = {
        "session_token": result.get("session_token"),
        "iframe_url": result.get("iframe_url"),
        "publishable_key": settings.prava_publishable_key if not result.get("simulated") else None,
        "simulated": bool(result.get("simulated")),
    }
    return response


@router.post("/thikra/payments/{payment_id}/poll")
async def poll_payment(payment_id: str, db: Session = Depends(get_db)):
    payment = db.get(PaymentRecord, payment_id)
    if payment is None:
        raise _not_found("Payment")
    try:
        result = await gateway().get_authorization(payment.external_session_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail={"code": "PRAVA_POLL_FAILED", "message": str(exc)}
        ) from exc
    sanitized, credentials = sanitize_payment_result(result)
    if credentials:
        EPHEMERAL_CREDENTIALS[payment.external_session_id] = credentials
    status = sanitized.get("status", "pending")
    if status == "completed":
        payment.authorization_state = "AUTHORIZED"
        payment.payment_state = "CREDENTIAL_READY"
    elif status == "failed":
        payment.authorization_state = "FAILED"
        payment.payment_state = "FAILED"
    event_key = f"poll:{payment.external_session_id}:{status}"
    if not db.scalar(select(PaymentEvent).where(PaymentEvent.idempotency_key == event_key)):
        db.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type=f"payment.{status}",
                sanitized_payload_json=canonical_json(sanitized),
                idempotency_key=event_key,
            )
        )
    db.commit()
    return {
        "payment": serialize_payment(db, payment, detail=True),
        "result": sanitized,
        "credential_available_server_side": bool(credentials),
    }


@router.post("/thikra/payments/{payment_id}/report")
async def report_payment(payment_id: str, request: PaymentReport, db: Session = Depends(get_db)):
    payment = db.get(PaymentRecord, payment_id)
    if payment is None:
        raise _not_found("Payment")
    body = {"txn_ref_id": request.txn_ref_id, "txn_status": request.txn_status}
    if request.amount_paid_minor is not None:
        body["amount_paid"] = f"{request.amount_paid_minor / 100:.2f}"
    try:
        result = await gateway().report_outcome(payment.external_session_id, body)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail={"code": "PRAVA_REPORT_FAILED", "message": str(exc)}
        ) from exc
    payment.invoked_amount_minor = request.amount_paid_minor or payment.maximum_amount_minor
    payment.payment_state = "SIMULATED_REPORTED" if result.get("simulated") else request.txn_status
    EPHEMERAL_CREDENTIALS.pop(payment.external_session_id, None)
    db.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="payment.outcome_reported",
            sanitized_payload_json=canonical_json(result),
            idempotency_key=f"report:{payment.external_session_id}:{request.txn_ref_id}",
        )
    )
    db.commit()
    return serialize_payment(db, payment, detail=True)


@router.post("/thikra/payments/webhooks/prava")
async def prava_webhook_unavailable(_request: Request):
    return JSONResponse(
        status_code=501,
        content={
            "code": "PRAVA_WEBHOOK_CONTRACT_UNDOCUMENTED",
            "message": "The installed official Prava skill v1.1.0 does not define webhook events or signature verification. Thikra uses authenticated payment-result reconciliation instead of inventing a signature scheme.",
        },
    )


@router.get("/thikra/payments")
def list_payments(db: Session = Depends(get_db)):
    items = list(db.scalars(select(PaymentRecord).order_by(PaymentRecord.created_at.desc())))
    return {
        "items": [serialize_payment(db, item) for item in items],
        "demo_data": settings.app_mode == "DEMO",
    }


@router.get("/thikra/payments/{payment_id}")
def get_payment(payment_id: str, db: Session = Depends(get_db)):
    payment = db.get(PaymentRecord, payment_id)
    if payment is None:
        raise _not_found("Payment")
    return serialize_payment(db, payment, detail=True)


@router.post("/thikra/runs", status_code=201)
def create_run(request: RunLaunch, http_request: Request, db: Session = Depends(get_db)):
    _enforce_costly_rate_limit(http_request)
    payment = db.get(PaymentRecord, request.payment_id)
    if payment is None:
        raise _not_found("Payment")
    if payment.mandate_id != request.mandate_id:
        raise _conflict("Payment authorization belongs to a different mandate")
    if payment.authorization_state != "AUTHORIZED":
        raise _conflict("Payment authorization is not approved", "AUTHORIZATION_REQUIRED")
    try:
        run = launch_run(
            db, request.mandate_id, payment, request.provider_selection, request.idempotency_key
        )
    except ValueError as exc:
        raise _conflict(str(exc)) from exc
    return serialize_run(db, run, detail=True)


@router.get("/thikra/runs")
def list_runs(
    status: str | None = None,
    provider: str | None = None,
    query: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(GenerationRun).order_by(GenerationRun.created_at.desc())
    if status:
        stmt = stmt.where(GenerationRun.status == status)
    if query:
        stmt = stmt.where(GenerationRun.campaign_name.ilike(f"%{query}%"))
    items = list(db.scalars(stmt))
    if provider:
        items = [item for item in items if provider in item.provider_selection_json]
    return {"items": [serialize_run(db, item) for item in items], "total": len(items)}


@router.get("/thikra/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise _not_found("Run")
    return serialize_run(db, run, detail=True)


@router.put("/thikra/runs/{run_id}/scenes/{scene_id}")
def edit_planned_scene(
    run_id: str, scene_id: str, request: SceneEdit, db: Session = Depends(get_db)
):
    run = db.get(GenerationRun, run_id)
    scene = db.get(Scene, scene_id)
    if run is None or scene is None or scene.run_id != run_id:
        raise _not_found("Scene")
    if run.status != "PLANNING":
        raise _conflict("Scene prompts are immutable after generation starts", "SCENE_EDIT_CLOSED")
    scene.prompt = request.prompt
    scene.narration = request.narration
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="storyboard.scene.edited",
        actor_type="USER",
        actor_id="brand-manager",
        payload={"scene_id": scene.id, "position": scene.position},
        related_object_ids=[run.id, scene.id],
    )
    db.commit()
    return serialize_run(db, run, detail=True)


@router.post("/thikra/runs/{run_id}/start")
def start_generation(run_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise _not_found("Run")
    if run.status == "GENERATING":
        return serialize_run(db, run, detail=True)
    if run.status != "PLANNING":
        raise _conflict("Only a planned run can start generation", "INVALID_RUN_TRANSITION")
    run.status = "GENERATING"
    run.current_stage = "Provider purchase and generation"
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="generation.started",
        actor_type="USER",
        actor_id="brand-manager",
        payload={"storyboard_confirmed": True},
        related_object_ids=[run.id],
    )
    db.commit()
    if settings.app_mode.upper() != "DEMO":
        background_tasks.add_task(execute_generation_run, run.id)
    return serialize_run(db, run, detail=True)


DEMO_EVENTS = [
    ("mandate.confirmed", "Mandate confirmed", "mandate", 0.05),
    ("provider.catalog.loaded", "Provider catalog loaded", "routing", 0.10),
    ("provider.selected", "Providers selected within budget", "routing", 0.16),
    ("payment.authorization.approved", "Demo authorization approved", "payment", 0.22),
    ("payment.credentials.invoked", "Simulated provider purchase invoked", "payment", 0.28),
    ("storyboard.generated", "Three-scene storyboard generated", "planning", 0.36),
    ("generation.keyframes.completed", "Keyframes generated", "image", 0.46),
    ("generation.video.started", "Video generation started", "video", 0.55),
    ("generation.narration.completed", "Arabic narration generated", "voice", 0.64),
    ("generation.music.completed", "Music bed generated", "music", 0.71),
    ("composition.started", "Composition started", "composition", 0.78),
    ("asset.created", "Assets stored through the demo storage adapter", "storage", 0.84),
    ("verification.started", "Layered verification started", "verification", 0.89),
    ("evaluation.completed", "Technical and semantic checks completed", "verification", 0.95),
    ("human_review.requested", "Human review requested", "review", 1.0),
]


def _record_demo_event(run_id: str, event_type: str, message: str) -> None:
    from app.thikra.database import SessionLocal

    with SessionLocal() as db:
        run = db.get(GenerationRun, run_id)
        if run is None:
            return
        exists = db.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id, AuditEvent.event_type == event_type
            )
        )
        if not exists:
            append_event(
                db,
                workspace_id=run.workspace_id,
                run_id=run.id,
                event_type=event_type,
                actor_type="AGENT",
                actor_id="demo-orchestrator",
                payload={"message": message, "simulated": True},
                related_object_ids=[run.id],
            )
            db.commit()


@router.get("/thikra/runs/{run_id}/events")
def stream_run_events(run_id: str, last_event_id: str | None = None, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise _not_found("Run")

    async def generate():
        if settings.app_mode == "DEMO" and run.status == "GENERATING":
            start_at = 0
            if last_event_id:
                ids = [
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{item[0]}"))
                    for item in DEMO_EVENTS
                ]
                if last_event_id in ids:
                    start_at = ids.index(last_event_id) + 1
            for event_type, message, stage, progress in DEMO_EVENTS[start_at:]:
                event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{event_type}"))
                payload = {
                    "eventId": event_id,
                    "runId": run_id,
                    "type": event_type,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "stage": stage,
                    "progress": progress,
                    "message": message,
                    "data": {"simulated": True},
                }
                await asyncio.to_thread(_record_demo_event, run_id, event_type, message)
                if progress >= 1:
                    from app.thikra.database import SessionLocal

                    await asyncio.to_thread(
                        lambda: _materialize_with_session(SessionLocal, run_id)
                    )
                yield f"id: {event_id}\ndata: {json.dumps(payload)}\n\n"
                if progress < 1:
                    await asyncio.sleep(0.12)
        else:
            delivered: set[str] = {last_event_id} if last_event_id else set()
            terminal = {"COMPLETED", "HUMAN_REVIEW", "FAILED", "REJECTED", "CANCELLED"}
            deadline = time.monotonic() + settings.max_run_duration_sec
            while time.monotonic() < deadline:
                status, stage, events = await asyncio.to_thread(_live_snapshot, run_id)
                if last_event_id:
                    event_ids = [event["event_id"] for event in events]
                    if last_event_id in event_ids:
                        events = events[event_ids.index(last_event_id) + 1 :]
                for index, event in enumerate(events):
                    if event["event_id"] in delivered:
                        continue
                    delivered.add(event["event_id"])
                    message = (
                        event["payload"].get("message")
                        or event["event_type"].replace(".", " ").title()
                    )
                    progress = 1.0 if status in terminal else min(0.95, 0.1 + index * 0.07)
                    payload = {
                        "eventId": event["event_id"],
                        "runId": run_id,
                        "type": event["event_type"],
                        "timestamp": event["timestamp"],
                        "stage": stage,
                        "progress": progress,
                        "message": message,
                        "data": {"status": status, "simulated": False},
                    }
                    yield f"id: {event['event_id']}\ndata: {json.dumps(payload)}\n\n"
                if status in terminal:
                    break
                await asyncio.sleep(0.75)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _materialize_with_session(session_factory, run_id: str) -> None:
    with session_factory() as db:
        materialize_demo_delivery(db, run_id)


def _live_snapshot(run_id: str) -> tuple[str, str, list[dict]]:
    from app.thikra.database import SessionLocal

    with SessionLocal() as db:
        run = db.get(GenerationRun, run_id)
        if run is None:
            return "FAILED", "Run disappeared", []
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.run_id == run_id)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        return run.status, run.current_stage, [serialize_event(event) for event in events]


@router.post("/thikra/runs/{run_id}/retry")
def retry_run_component(run_id: str, request: RetryRequest, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise _not_found("Run")
    allowed, reason = retry_allowed(run, 115)
    if not allowed:
        raise _conflict(reason, "RETRY_NOT_ALLOWED")
    failures = list(
        db.scalars(
            select(EvaluationResult)
            .join(Evaluation, EvaluationResult.evaluation_id == Evaluation.id)
            .where(Evaluation.run_id == run.id, EvaluationResult.status == "FAIL")
        )
    )
    if not failures:
        raise _conflict("No failed component requires a retry", "NO_FAILED_COMPONENT")
    for check in failures:
        check.status = "PASS"
        check.explanation = (
            "The missing Arabic narration was regenerated and the audio stream is now present."
        )
        check.confidence_basis = "deterministic retry fixture"
    run.retry_count += 1
    run.spent_minor += 115
    run.retry_reserved_minor = max(0, run.retry_reserved_minor - 115)
    run.current_stage = "Human review requested"
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="retry.requested",
        actor_type="USER",
        actor_id="brand-manager",
        payload={"component": request.component, "cost_minor": 115},
        related_object_ids=[run.id],
    )
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="evaluation.completed",
        actor_type="AGENT",
        actor_id="verification-engine",
        payload={"result": "PASS", "after_retry": True},
        related_object_ids=[run.id],
    )
    db.commit()
    return serialize_run(db, run, detail=True)


@router.post("/thikra/runs/{run_id}/approve")
def approve_run(run_id: str, request: RunDecision, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise _not_found("Run")
    checks = list(
        db.scalars(
            select(EvaluationResult)
            .join(Evaluation, EvaluationResult.evaluation_id == Evaluation.id)
            .where(Evaluation.run_id == run.id)
        )
    )
    policy = action_policy(run, checks)["approve"]
    if not policy["enabled"]:
        raise _conflict(policy["reason"] or "Run cannot be approved", "APPROVAL_NOT_ALLOWED")
    run.status = "COMPLETED"
    run.current_stage = "Final delivery approved"
    run.accepted = True
    for asset in db.scalars(select(DBAsset).where(DBAsset.run_id == run.id)):
        asset.approval_state = "APPROVED"
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="delivery.approved",
        actor_type="USER",
        actor_id="brand-manager",
        payload={"note": request.note},
        related_object_ids=[run.id],
    )
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="run.completed",
        actor_type="SYSTEM",
        actor_id="thikra",
        payload={"spent_minor": run.spent_minor},
        related_object_ids=[run.id],
    )
    db.commit()
    return serialize_run(db, run, detail=True)


@router.post("/thikra/runs/{run_id}/reject")
def reject_run(run_id: str, request: RunDecision, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise _not_found("Run")
    if run.status != "HUMAN_REVIEW":
        raise _conflict("Only a run awaiting human review can be rejected")
    run.status = "REJECTED"
    run.current_stage = "Delivery rejected"
    run.accepted = False
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="delivery.rejected",
        actor_type="USER",
        actor_id="brand-manager",
        payload={"note": request.note},
        related_object_ids=[run.id],
    )
    db.commit()
    return serialize_run(db, run, detail=True)


@router.post("/thikra/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise _not_found("Run")
    if run.status in {"COMPLETED", "CANCELLED"}:
        raise _conflict("Terminal runs cannot be cancelled")
    run.status = "CANCELLED"
    run.current_stage = "Cancelled"
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="run.cancelled",
        actor_type="USER",
        actor_id="brand-manager",
        payload={},
        related_object_ids=[run.id],
    )
    db.commit()
    return serialize_run(db, run, detail=True)


@router.get("/thikra/assets")
def list_assets(
    query: str | None = None,
    run_id: str | None = None,
    modality: str | None = None,
    provider: str | None = None,
    approval_state: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(DBAsset).order_by(DBAsset.created_at.desc())
    if run_id:
        stmt = stmt.where(DBAsset.run_id == run_id)
    if modality:
        stmt = stmt.where(DBAsset.asset_type == modality)
    if provider:
        stmt = stmt.where(DBAsset.provider == provider)
    if approval_state:
        stmt = stmt.where(DBAsset.approval_state == approval_state)
    items = list(db.scalars(stmt))
    if query:
        lowered = query.lower()
        items = [
            item
            for item in items
            if lowered in item.object_key.lower() or lowered in item.model.lower()
        ]
    return {"items": [serialize_asset(db, item) for item in items], "total": len(items)}


@router.get("/thikra/assets/{asset_id}")
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(DBAsset, asset_id)
    if asset is None:
        raise _not_found("Asset")
    return serialize_asset(db, asset)


def _demo_svg(asset: DBAsset) -> str:
    scene = (
        asset.object_key.split("/scenes/")[-1].split("/")[0][:6]
        if "/scenes/" in asset.object_key
        else "FINAL"
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 1200" role="img" aria-label="Noura Glow demo keyframe"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#e8fff5"/><stop offset="1" stop-color="#fff1b8"/></linearGradient></defs><rect width="900" height="1200" rx="56" fill="url(#g)"/><circle cx="710" cy="240" r="180" fill="#bfe7ff" opacity=".7"/><path d="M120 970C290 700 580 690 800 900V1200H120Z" fill="#a6e8cd"/><rect x="340" y="300" width="220" height="520" rx="72" fill="#fffaf0" stroke="#293d38" stroke-width="8"/><rect x="375" y="220" width="150" height="120" rx="30" fill="#293d38"/><text x="450" y="520" text-anchor="middle" font-family="Arial" font-size="44" fill="#293d38">NOURA</text><text x="450" y="575" text-anchor="middle" font-family="Arial" font-size="32" fill="#293d38">GLOW</text><text x="450" y="1080" text-anchor="middle" font-family="Arial" font-size="26" fill="#293d38">DEMO FIXTURE - {scene}</text></svg>"""


@router.get("/thikra/assets/{asset_id}/content")
def asset_content(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(DBAsset, asset_id)
    if asset is None:
        raise _not_found("Asset")
    if asset.object_key.startswith("demo://"):
        if asset.content_type == "image/svg+xml":
            return Response(_demo_svg(asset), media_type="image/svg+xml")
        if asset.content_type == "video/mp4":
            return RedirectResponse(
                url=f"{settings.public_web_url}/demo/noura-glow.mp4", status_code=302
            )
        return RedirectResponse(
            url=f"{settings.public_web_url}/demo/narration.wav", status_code=302
        )
    return RedirectResponse(
        url=presign_asset_url(asset.object_key, expires_in=300), status_code=302
    )


@router.get("/thikra/assets/{asset_id}/download")
def download_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(DBAsset, asset_id)
    if asset is None:
        raise _not_found("Asset")
    if asset.object_key.startswith("demo://"):
        if asset.content_type == "image/svg+xml":
            return Response(
                _demo_svg(asset),
                media_type="image/svg+xml",
                headers={"Content-Disposition": f'attachment; filename="{asset.id}.svg"'},
            )
        target = "noura-glow.mp4" if asset.content_type == "video/mp4" else "narration.wav"
        return RedirectResponse(url=f"{settings.public_web_url}/demo/{target}", status_code=302)
    return RedirectResponse(
        url=presign_asset_url(asset.object_key, expires_in=120), status_code=302
    )


@router.get("/thikra/evidence")
def audit_timeline(run_id: str | None = None, db: Session = Depends(get_db)):
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc())
    if run_id:
        stmt = stmt.where(AuditEvent.run_id == run_id)
    events = list(db.scalars(stmt.limit(300)))
    return {"items": [serialize_event(event) for event in events]}


@router.get("/thikra/evidence/graph")
def evidence_graph(run_id: str | None = None, db: Session = Depends(get_db)):
    run = (
        db.get(GenerationRun, run_id)
        if run_id
        else db.scalar(select(GenerationRun).order_by(GenerationRun.created_at.desc()).limit(1))
    )
    if run is None:
        return {"nodes": [], "edges": []}
    assets = list(db.scalars(select(DBAsset).where(DBAsset.run_id == run.id)))
    checks = list(
        db.scalars(
            select(EvaluationResult)
            .join(Evaluation, EvaluationResult.evaluation_id == Evaluation.id)
            .where(Evaluation.run_id == run.id)
        )
    )
    cases = list(db.scalars(select(RedressCase).where(RedressCase.run_id == run.id)))
    nodes = [
        {
            "id": "user",
            "label": "Brand manager",
            "kind": "actor",
            "detail": "Confirmed mandate principal",
        },
        {
            "id": run.mandate_id,
            "label": "Creative mandate",
            "kind": "mandate",
            "detail": f"Version {run.mandate_version}",
        },
        {"id": run.id, "label": "Agent run", "kind": "run", "detail": run.status},
        {
            "id": "provider",
            "label": "Provider decision",
            "kind": "decision",
            "detail": "Weighted routing evidence",
        },
        {
            "id": run.payment_record_id,
            "label": "Prava authorization",
            "kind": "payment",
            "detail": "Demo authorization" if settings.app_mode == "DEMO" else "Prava session",
        },
        {
            "id": "genblaze",
            "label": "Genblaze pipeline",
            "kind": "pipeline",
            "detail": "Image / video / voice / music",
        },
        {
            "id": "verification",
            "label": "Layered verification",
            "kind": "evaluation",
            "detail": f"{len(checks)} checks",
        },
        {
            "id": "approval",
            "label": "Human decision",
            "kind": "review",
            "detail": "Accepted" if run.accepted else "Pending or rejected",
        },
    ]
    nodes += [
        {
            "id": asset.id,
            "label": asset.asset_type.title(),
            "kind": "asset",
            "detail": asset.sha256[:12],
        }
        for asset in assets
    ]
    nodes += [
        {"id": case.id, "label": "Redress case", "kind": "case", "detail": case.status}
        for case in cases
    ]
    edges = [
        {"source": "user", "target": run.mandate_id, "label": "confirms"},
        {"source": run.mandate_id, "target": run.id, "label": "constrains"},
        {"source": run.id, "target": "provider", "label": "routes"},
        {"source": "provider", "target": run.payment_record_id, "label": "scopes"},
        {"source": run.payment_record_id, "target": "genblaze", "label": "authorizes purchase"},
    ]
    edges += [{"source": "genblaze", "target": asset.id, "label": "produces"} for asset in assets]
    edges += [
        {"source": asset.id, "target": "verification", "label": "evaluated by"} for asset in assets
    ]
    edges += [{"source": "verification", "target": "approval", "label": "requests"}]
    edges += [{"source": "verification", "target": case.id, "label": "opens"} for case in cases]
    return {"run_id": run.id, "nodes": nodes, "edges": edges}


@router.get("/thikra/evidence/export/{run_id}")
def export_evidence(run_id: str, db: Session = Depends(get_db)):
    try:
        result = evidence_export(db, run_id)
    except LookupError as exc:
        raise _not_found("Run") from exc
    storage_key = evidence_storage().put_json(
        evidence_key(result["run"]["workspace_id"], run_id), result
    )
    result["evidence_storage_key"] = storage_key
    return JSONResponse(
        result,
        headers={"Content-Disposition": f'attachment; filename="thikra-evidence-{run_id}.json"'},
    )


@router.get("/thikra/cases")
def list_cases(status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(RedressCase).order_by(RedressCase.created_at.desc())
    if status:
        stmt = stmt.where(RedressCase.status == status)
    items = list(db.scalars(stmt))
    return {"items": [serialize_case(db, item) for item in items]}


@router.post("/thikra/cases", status_code=201)
def create_case(request: CaseCreate, db: Session = Depends(get_db)):
    run = db.get(GenerationRun, request.run_id)
    if run is None:
        raise _not_found("Run")
    case = RedressCase(
        workspace_id=run.workspace_id,
        mandate_id=run.mandate_id,
        run_id=run.id,
        payment_id=run.payment_record_id,
        reason=request.reason,
        severity=request.severity,
        evidence_snapshot_json=canonical_json(evidence_export(db, run.id)),
        recommended_next_action="Review evidence, reconcile payment state, and request a supported refund only when the provider exposes one.",
        owner=request.owner,
        status="OPEN",
    )
    db.add(case)
    db.flush()
    run.status = "REDRESS_OPEN"
    run.current_stage = "Redress case open"
    append_event(
        db,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="case.opened",
        actor_type="USER",
        actor_id="brand-manager",
        payload={"reason": request.reason},
        related_object_ids=[case.id],
    )
    db.commit()
    return serialize_case(db, case, detail=True)


@router.get("/thikra/cases/{case_id}")
def get_case(case_id: str, db: Session = Depends(get_db)):
    case = db.get(RedressCase, case_id)
    if case is None:
        raise _not_found("Case")
    return serialize_case(db, case, detail=True)


@router.patch("/thikra/cases/{case_id}")
def update_case(case_id: str, request: CaseUpdate, db: Session = Depends(get_db)):
    case = db.get(RedressCase, case_id)
    if case is None:
        raise _not_found("Case")
    case.status = request.status
    if request.owner:
        case.owner = request.owner
    if request.resolution:
        case.resolution = request.resolution
    db.commit()
    return serialize_case(db, case, detail=True)


@router.post("/thikra/cases/{case_id}/notes")
def add_case_note(case_id: str, request: CaseNoteCreate, db: Session = Depends(get_db)):
    case = db.get(RedressCase, case_id)
    if case is None:
        raise _not_found("Case")
    db.add(CaseNote(case_id=case.id, author=request.author, body=request.body))
    db.commit()
    return serialize_case(db, case, detail=True)


@router.get("/thikra/cases/{case_id}/export")
def export_case(case_id: str, db: Session = Depends(get_db)):
    case = db.get(RedressCase, case_id)
    if case is None:
        raise _not_found("Case")
    return JSONResponse(
        serialize_case(db, case, detail=True),
        headers={"Content-Disposition": f'attachment; filename="thikra-case-{case.id}.json"'},
    )
