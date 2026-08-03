"""Local loopback API for the Thikra Studio desktop application."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repo import provider_catalog as catalog
from app.studio.models import (
    AgentProposal,
    NodeExecution,
    StudioAnnotation,
    StudioAsset,
    StudioExecutionEvent,
    StudioProject,
    WorkflowExecution,
    WorkflowRevision,
)
from app.studio.schemas import (
    AgentProposalCreate,
    AnnotationCreate,
    EstimateRequest,
    ExecutionCreate,
    LayoutUpdate,
    ProjectCreate,
    ProjectUpdate,
    ProposalApply,
    ProviderConnectionSet,
    ResumeExecutionCreate,
    RevisionCreate,
)
from app.studio.service import (
    apply_proposal,
    clear_provider_secret,
    create_execution,
    create_project,
    create_proposal,
    create_resume_execution,
    create_revision,
    delete_project,
    estimate_resume,
    estimate_revision,
    execute_workflow,
    import_asset,
    proposal_annotations,
    proposal_asset_urls,
    provider_connection_status,
    serialize_execution,
    serialize_project,
    serialize_proposal,
    serialize_revision,
    set_provider_secret,
)
from app.thikra.database import get_db

router = APIRouter(prefix="/studio", tags=["Thikra Studio"])

NODE_CATALOG = [
    {"type": "creative_brief", "label": "Creative Brief", "category": "Input", "description": "The creative goal and story direction."},
    {"type": "reference_asset", "label": "Reference Asset", "category": "Input", "description": "A selected image used as visual context."},
    {"type": "look_director", "label": "Look Director", "category": "Agent", "description": "Analyzes references into reusable style guidance."},
    {"type": "image_generation", "label": "Image Generation", "category": "Generate", "description": "Creates one to four visual variants."},
    {"type": "asset_selector", "label": "Asset Selector", "category": "Control", "description": "Pins one approved variant for downstream work."},
    {"type": "video_generation", "label": "Video Generation", "category": "Generate", "description": "Animates an approved keyframe."},
    {"type": "narration", "label": "Narration", "category": "Audio", "description": "Generates spoken narration."},
    {"type": "music", "label": "Music", "category": "Audio", "description": "Generates an instrumental score."},
    {"type": "composition", "label": "Composition", "category": "Finish", "description": "Assembles ordered visual and audio inputs."},
    {"type": "verification", "label": "Verification", "category": "Finish", "description": "Runs delivery checks and records evidence."},
    {"type": "export", "label": "Export", "category": "Finish", "description": "Produces the final delivery."},
    {"type": "note", "label": "Note", "category": "Organize", "description": "A non-executing canvas note."},
    {"type": "group", "label": "Group", "category": "Organize", "description": "A non-executing visual group."},
]


def _project(db: Session, project_id: str) -> StudioProject:
    item = db.get(StudioProject, project_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "Studio project not found"})
    return item


def _conflict(message: str, code: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


@router.get("/node-catalog")
def node_catalog():
    providers = catalog.matrix()
    configured = {item["vendor"]: item for item in provider_connection_status()}
    for entries in providers.values():
        for entry in entries:
            status = configured.get(entry["vendor"])
            entry["key_available"] = bool(status and status["configured"])
            entry["credential_source"] = status["source"] if status else "none"
    return {"nodes": NODE_CATALOG, "providers": providers, "schema_version": 1}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    items = list(db.scalars(select(StudioProject).order_by(StudioProject.updated_at.desc())))
    return {"items": [serialize_project(db, item, detail=False) for item in items], "total": len(items)}


@router.post("/projects", status_code=201)
def post_project(request: ProjectCreate, db: Session = Depends(get_db)):
    try:
        item = create_project(
            db,
            name=request.name,
            description=request.description,
            budget=request.budget_cap_minor,
            currency=request.currency,
            graph=request.graph,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_GRAPH", "message": str(exc)}) from exc
    return serialize_project(db, item)


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    return serialize_project(db, _project(db, project_id))


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, request: ProjectUpdate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    changes = request.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(project, field, value.upper() if field == "currency" else value)
    db.commit()
    db.refresh(project)
    return serialize_project(db, project)


@router.delete("/projects/{project_id}", status_code=204)
def remove_project(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    try:
        delete_project(db, project)
    except RuntimeError as exc:
        raise _conflict("Cancel the active execution before deleting this project", str(exc)) from exc


@router.post("/projects/{project_id}/revisions", status_code=201)
def post_revision(project_id: str, request: RevisionCreate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    try:
        revision = create_revision(db, project, request.base_revision_id, request.graph, request.summary)
    except RuntimeError as exc:
        raise _conflict("The workflow changed while you were editing it", "STALE_REVISION") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_GRAPH", "message": str(exc)}) from exc
    return serialize_revision(revision)


@router.get("/projects/{project_id}/revisions")
def list_revisions(project_id: str, db: Session = Depends(get_db)):
    _project(db, project_id)
    items = list(db.scalars(select(WorkflowRevision).where(WorkflowRevision.project_id == project_id).order_by(WorkflowRevision.number.desc())))
    return {"items": [serialize_revision(item) for item in items]}


@router.patch("/projects/{project_id}/layout")
def patch_layout(project_id: str, request: LayoutUpdate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    project.layout_json = json.dumps(request.positions, separators=(",", ":"))
    project.viewport_json = json.dumps(request.viewport, separators=(",", ":"))
    db.commit()
    return {"layout": request.positions, "viewport": request.viewport}


@router.post("/projects/{project_id}/agent-proposals", status_code=201)
def post_proposal(project_id: str, request: AgentProposalCreate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    try:
        proposal = create_proposal(
            db,
            project,
            request.base_revision_id,
            request.prompt,
            request.selected_node_ids,
            proposal_asset_urls(db, project.id, request.asset_ids),
            proposal_annotations(db, project.id, request.asset_ids),
        )
    except RuntimeError as exc:
        raise _conflict("The workflow changed; ask the agent again", "STALE_REVISION") from exc
    return serialize_proposal(proposal)


@router.post("/projects/{project_id}/agent-proposals/{proposal_id}/apply", status_code=201)
def post_apply_proposal(project_id: str, proposal_id: str, request: ProposalApply, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    proposal = db.get(AgentProposal, proposal_id)
    if proposal is None or proposal.project_id != project.id:
        raise HTTPException(status_code=404, detail={"code": "PROPOSAL_NOT_FOUND", "message": "Agent proposal not found"})
    try:
        revision = apply_proposal(db, project, proposal, request.base_revision_id, request.operation_ids)
    except RuntimeError as exc:
        raise _conflict("The workflow changed; ask the agent again", "STALE_REVISION") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_PROPOSAL", "message": str(exc)}) from exc
    return serialize_revision(revision)


@router.post("/projects/{project_id}/estimate")
def post_estimate(project_id: str, request: EstimateRequest, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    revision = db.get(WorkflowRevision, request.revision_id)
    if revision is None or revision.project_id != project.id:
        raise HTTPException(status_code=404, detail={"code": "REVISION_NOT_FOUND", "message": "Workflow revision not found"})
    try:
        estimate = estimate_revision(revision, request.target_node_ids, request.force_rerun)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_TARGET", "message": str(exc)},
        ) from exc
    estimate["budget_cap_minor"] = project.budget_cap_minor
    estimate["remaining_minor"] = max(0, project.budget_cap_minor - project.spent_minor)
    estimate["within_budget"] = estimate["estimated_cost_minor"] <= estimate["remaining_minor"]
    estimate["simulated"] = True
    return estimate


@router.post("/projects/{project_id}/executions", status_code=202)
def post_execution(project_id: str, request: ExecutionCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    revision = db.get(WorkflowRevision, request.revision_id)
    if revision is None or revision.project_id != project.id:
        raise HTTPException(status_code=404, detail={"code": "REVISION_NOT_FOUND", "message": "Workflow revision not found"})
    try:
        estimate = estimate_revision(revision, request.target_node_ids, request.force_rerun)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_TARGET", "message": str(exc)},
        ) from exc
    if estimate["estimate_hash"] != request.estimate_hash:
        raise _conflict("The estimate is stale; review the updated cost before running", "STALE_ESTIMATE")
    try:
        execution = create_execution(db, project, revision, estimate, request.force_rerun)
    except RuntimeError as exc:
        code = str(exc)
        if code.startswith("PROVIDER_PREFLIGHT_FAILED:"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "PROVIDER_PREFLIGHT_FAILED",
                    "message": code.partition(":")[2],
                },
            ) from exc
        raise _conflict("The workflow or project budget changed", code) from exc
    background.add_task(execute_workflow, execution.id)
    return serialize_execution(db, execution)


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str, db: Session = Depends(get_db)):
    item = db.get(WorkflowExecution, execution_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"})
    return serialize_execution(db, item)


@router.post("/executions/{execution_id}/resume-estimate")
def post_resume_estimate(execution_id: str, db: Session = Depends(get_db)):
    original = db.get(WorkflowExecution, execution_id)
    if original is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"},
        )
    try:
        estimate = estimate_resume(db, original)
    except RuntimeError as exc:
        raise _conflict("Only failed or cancelled executions can be resumed", str(exc)) from exc
    project = _project(db, original.project_id)
    estimate["budget_cap_minor"] = project.budget_cap_minor
    estimate["remaining_minor"] = max(0, project.budget_cap_minor - project.spent_minor)
    estimate["within_budget"] = estimate["estimated_cost_minor"] <= estimate["remaining_minor"]
    estimate["simulated"] = True
    return estimate


@router.post("/executions/{execution_id}/resume", status_code=202)
def post_resume_execution(
    execution_id: str,
    request: ResumeExecutionCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    original = db.get(WorkflowExecution, execution_id)
    if original is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"},
        )
    project = _project(db, original.project_id)
    try:
        estimate = estimate_resume(db, original)
    except RuntimeError as exc:
        raise _conflict("Only failed or cancelled executions can be resumed", str(exc)) from exc
    if estimate["estimate_hash"] != request.estimate_hash:
        raise _conflict(
            "The resume estimate is stale; review the remaining cost again",
            "STALE_ESTIMATE",
        )
    try:
        execution = create_resume_execution(db, project, original, estimate)
    except RuntimeError as exc:
        code = str(exc)
        if code.startswith("PROVIDER_PREFLIGHT_FAILED:"):
            raise HTTPException(
                status_code=422,
                detail={"code": "PROVIDER_PREFLIGHT_FAILED", "message": code.partition(":")[2]},
            ) from exc
        raise _conflict("The project budget or provider setup changed", code) from exc
    background.add_task(execute_workflow, execution.id)
    return serialize_execution(db, execution)


@router.post("/executions/{execution_id}/cancel")
def cancel_execution(execution_id: str, db: Session = Depends(get_db)):
    item = db.get(WorkflowExecution, execution_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"})
    if item.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        raise _conflict("A finished execution cannot be cancelled", "EXECUTION_TERMINAL")
    item.cancel_requested = True
    db.commit()
    return {"id": item.id, "status": item.status, "cancel_requested": True}


@router.get("/executions/{execution_id}/events")
def execution_events(
    execution_id: str,
    cursor: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
):
    execution = db.get(WorkflowExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"})

    async def stream():
        event_cursor = int(last_event_id or cursor)
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
        last_heartbeat = 0.0
        while True:
            from app.thikra.database import SessionLocal

            with SessionLocal() as session:
                current = session.get(WorkflowExecution, execution_id)
                events = list(session.scalars(select(StudioExecutionEvent).where(StudioExecutionEvent.execution_id == execution_id, StudioExecutionEvent.sequence > event_cursor).order_by(StudioExecutionEvent.sequence)))
                for event in events:
                    event_cursor = event.sequence
                    payload = json.loads(event.payload_json)
                    envelope = {
                        "eventId": str(event.sequence),
                        "executionId": execution_id,
                        "revisionId": current.revision_id,
                        "nodeId": event.node_id,
                        "type": event.event_type,
                        "status": current.status,
                        "message": payload.get("message", event.event_type),
                        "progress": payload.get("progress"),
                        "data": payload,
                        "estimatedCostMinor": current.estimated_cost_minor,
                    }
                    yield f"id: {event.sequence}\ndata: {json.dumps(envelope, default=str)}\n\n"
                now = time.monotonic()
                if current.status == "RUNNING" and not events and now - last_heartbeat >= 5:
                    running_node = session.scalar(
                        select(NodeExecution).where(
                            NodeExecution.execution_id == execution_id,
                            NodeExecution.status == "RUNNING",
                        )
                    )
                    diagnostics: dict = {}
                    if running_node:
                        started_event = session.scalar(
                            select(StudioExecutionEvent)
                            .where(
                                StudioExecutionEvent.execution_id == execution_id,
                                StudioExecutionEvent.node_id == running_node.node_id,
                                StudioExecutionEvent.event_type == "node.started",
                            )
                            .order_by(StudioExecutionEvent.sequence.desc())
                        )
                        diagnostics = (
                            json.loads(started_event.payload_json) if started_event else {}
                        )
                    provider_detail = (
                        f" · {diagnostics.get('provider')} / {diagnostics.get('model')}"
                        if diagnostics.get("provider")
                        else ""
                    )
                    elapsed = max(
                        0,
                        int(
                            (
                                datetime.now(UTC)
                                - (current.started_at.replace(tzinfo=UTC) if current.started_at and current.started_at.tzinfo is None else current.started_at or datetime.now(UTC))
                            ).total_seconds()
                        ),
                    )
                    heartbeat = {
                        "eventId": f"heartbeat-{execution_id}-{elapsed // 5}",
                        "executionId": execution_id,
                        "revisionId": current.revision_id,
                        "nodeId": running_node.node_id if running_node else None,
                        "type": "node.heartbeat",
                        "status": current.status,
                        "message": (
                            f"{running_node.node_type.replace('_', ' ').title() if running_node else 'Workflow'}"
                            f"{provider_detail}"
                            f" · still running {elapsed // 60}m {elapsed % 60:02d}s"
                        ),
                        "progress": None,
                        "data": {"elapsedSeconds": elapsed, **diagnostics},
                        "estimatedCostMinor": current.estimated_cost_minor,
                    }
                    yield f"data: {json.dumps(heartbeat)}\n\n"
                    last_heartbeat = now
                if current.status in terminal and not events:
                    break
            await asyncio.sleep(0.18)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/projects/{project_id}/assets")
def list_project_assets(project_id: str, db: Session = Depends(get_db)):
    _project(db, project_id)
    assets = list(
        db.scalars(
            select(StudioAsset)
            .where(StudioAsset.project_id == project_id)
            .order_by(StudioAsset.created_at.desc())
        )
    )
    def serialize(asset: StudioAsset) -> dict:
        return {
            "id": asset.id,
            "name": asset.name,
            "asset_type": asset.asset_type,
            "content_type": asset.content_type,
            "size": asset.size,
            "sha256": asset.sha256,
            "content_url": f"/studio/assets/{asset.id}/content",
            "created_at": asset.created_at,
        }

    latest_image = db.scalar(
        select(NodeExecution)
        .join(WorkflowExecution, WorkflowExecution.id == NodeExecution.execution_id)
        .where(
            WorkflowExecution.project_id == project_id,
            NodeExecution.node_type == "image_generation",
            NodeExecution.status.in_({"SUCCEEDED", "CACHED"}),
        )
        .order_by(NodeExecution.created_at.desc())
    )
    latest_ids = [
        item.get("id")
        for item in (json.loads(latest_image.output_json).get("assets", []) if latest_image else [])
        if item.get("id")
    ]
    by_id = {asset.id: asset for asset in assets}
    return {
        "items": [serialize(asset) for asset in assets],
        "latest_image_variants": [
            serialize(by_id[asset_id]) for asset_id in latest_ids if asset_id in by_id
        ],
    }


@router.post("/projects/{project_id}/assets", status_code=201)
def post_asset(project_id: str, upload: UploadFile = File(...), db: Session = Depends(get_db)):
    project = _project(db, project_id)
    try:
        asset = import_asset(db, project, upload.filename or "asset", upload.content_type or "application/octet-stream", upload.file)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_ASSET", "message": str(exc)}) from exc
    return {"id": asset.id, "name": asset.name, "asset_type": asset.asset_type, "content_type": asset.content_type, "size": asset.size, "sha256": asset.sha256, "content_url": f"/studio/assets/{asset.id}/content"}


@router.get("/assets/{asset_id}/content")
def asset_content(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "Studio asset not found"})
    if asset.remote_url:
        from app.repo.pipelines import presign_asset_url

        return RedirectResponse(presign_asset_url(asset.remote_url), status_code=302)
    path = Path(asset.local_path or "").resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "ASSET_CONTENT_MISSING", "message": "Studio asset content is missing"})
    return FileResponse(path, media_type=asset.content_type, filename=asset.name)


@router.post("/projects/{project_id}/annotations", status_code=201)
def post_annotation(project_id: str, request: AnnotationCreate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    asset = db.get(StudioAsset, request.asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "Studio asset not found"})
    if request.kind in {"point", "rectangle"} and any(value < 0 or value > 1 for value in request.geometry.values()):
        raise HTTPException(status_code=422, detail={"code": "INVALID_GEOMETRY", "message": "Annotation coordinates must be normalized from 0 to 1"})
    item = StudioAnnotation(
        project_id=project.id,
        asset_id=asset.id,
        kind=request.kind,
        geometry_json=json.dumps(request.geometry, separators=(",", ":")),
        body=request.body,
        timestamp_ms=request.timestamp_ms,
    )
    db.add(item)
    db.commit()
    return {"id": item.id, "asset_id": item.asset_id, "kind": item.kind, "geometry": request.geometry, "body": item.body, "timestamp_ms": item.timestamp_ms}


@router.get("/provider-connections")
def connections():
    return {"items": provider_connection_status()}


@router.put("/provider-connections/{vendor}")
def put_connection(vendor: str, request: ProviderConnectionSet):
    try:
        set_provider_secret(vendor, request.secret)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "CONNECTION_STORE_FAILED", "message": str(exc)}) from exc
    return {"vendor": vendor, "configured": True, "source": "personal"}


@router.delete("/provider-connections/{vendor}")
def delete_connection(vendor: str):
    try:
        clear_provider_secret(vendor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": str(exc)}) from exc
    return {"vendor": vendor, "configured": False}
