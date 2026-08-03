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
from app.studio import editor
from app.studio.models import (
    AgentProposal,
    NodeExecution,
    SequenceAgentProposal,
    StudioAnnotation,
    StudioAsset,
    StudioCaptionJob,
    StudioExecutionEvent,
    StudioGenerationJob,
    StudioJobEvent,
    StudioProject,
    StudioRender,
    StudioRenderEvent,
    StudioSequence,
    WorkflowExecution,
    WorkflowRevision,
)
from app.studio.schemas import (
    AgentProposalCreate,
    AnnotationCreate,
    CaptionApply,
    CaptionCreate,
    CaptionEstimate,
    EditorGenerationCreate,
    EditorGenerationEstimate,
    EditorJobResume,
    EstimateRequest,
    ExecutionCreate,
    LayoutUpdate,
    ProjectCreate,
    ProjectUpdate,
    ProposalApply,
    ProviderConnectionSet,
    RenderCreate,
    RenderResume,
    ResumeExecutionCreate,
    RevisionCreate,
    SequenceCreate,
    SequenceProposalApply,
    SequenceProposalCreate,
    SequenceRestore,
    SequenceRevisionCreate,
    SequenceViewUpdate,
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
    {
        "type": "creative_brief",
        "label": "Creative Brief",
        "category": "Input",
        "description": "The creative goal and story direction.",
    },
    {
        "type": "reference_asset",
        "label": "Reference Asset",
        "category": "Input",
        "description": "A selected image used as visual context.",
    },
    {
        "type": "look_director",
        "label": "Look Director",
        "category": "Agent",
        "description": "Analyzes references into reusable style guidance.",
    },
    {
        "type": "image_generation",
        "label": "Image Generation",
        "category": "Generate",
        "description": "Creates one to four visual variants.",
    },
    {
        "type": "asset_selector",
        "label": "Asset Selector",
        "category": "Control",
        "description": "Pins one approved variant for downstream work.",
    },
    {
        "type": "video_generation",
        "label": "Video Generation",
        "category": "Generate",
        "description": "Animates an approved keyframe.",
    },
    {
        "type": "narration",
        "label": "Narration",
        "category": "Audio",
        "description": "Generates spoken narration.",
    },
    {
        "type": "music",
        "label": "Music",
        "category": "Audio",
        "description": "Generates an instrumental score.",
    },
    {
        "type": "composition",
        "label": "Composition",
        "category": "Finish",
        "description": "Assembles ordered visual and audio inputs.",
    },
    {
        "type": "verification",
        "label": "Verification",
        "category": "Finish",
        "description": "Runs delivery checks and records evidence.",
    },
    {
        "type": "export",
        "label": "Export",
        "category": "Finish",
        "description": "Produces the final delivery.",
    },
    {
        "type": "note",
        "label": "Note",
        "category": "Organize",
        "description": "A non-executing canvas note.",
    },
    {
        "type": "group",
        "label": "Group",
        "category": "Organize",
        "description": "A non-executing visual group.",
    },
]


def _project(db: Session, project_id: str) -> StudioProject:
    item = db.get(StudioProject, project_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "Studio project not found"},
        )
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
    return {
        "items": [serialize_project(db, item, detail=False) for item in items],
        "total": len(items),
    }


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
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_GRAPH", "message": str(exc)}
        ) from exc
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
        raise _conflict(
            "Cancel the active execution before deleting this project", str(exc)
        ) from exc


@router.post("/projects/{project_id}/revisions", status_code=201)
def post_revision(project_id: str, request: RevisionCreate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    try:
        revision = create_revision(
            db, project, request.base_revision_id, request.graph, request.summary
        )
    except RuntimeError as exc:
        raise _conflict("The workflow changed while you were editing it", "STALE_REVISION") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_GRAPH", "message": str(exc)}
        ) from exc
    return serialize_revision(revision)


@router.get("/projects/{project_id}/revisions")
def list_revisions(project_id: str, db: Session = Depends(get_db)):
    _project(db, project_id)
    items = list(
        db.scalars(
            select(WorkflowRevision)
            .where(WorkflowRevision.project_id == project_id)
            .order_by(WorkflowRevision.number.desc())
        )
    )
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
def post_apply_proposal(
    project_id: str, proposal_id: str, request: ProposalApply, db: Session = Depends(get_db)
):
    project = _project(db, project_id)
    proposal = db.get(AgentProposal, proposal_id)
    if proposal is None or proposal.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROPOSAL_NOT_FOUND", "message": "Agent proposal not found"},
        )
    try:
        revision = apply_proposal(
            db, project, proposal, request.base_revision_id, request.operation_ids
        )
    except RuntimeError as exc:
        raise _conflict("The workflow changed; ask the agent again", "STALE_REVISION") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_PROPOSAL", "message": str(exc)}
        ) from exc
    return serialize_revision(revision)


@router.post("/projects/{project_id}/estimate")
def post_estimate(project_id: str, request: EstimateRequest, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    revision = db.get(WorkflowRevision, request.revision_id)
    if revision is None or revision.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "REVISION_NOT_FOUND", "message": "Workflow revision not found"},
        )
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
def post_execution(
    project_id: str,
    request: ExecutionCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    project = _project(db, project_id)
    revision = db.get(WorkflowRevision, request.revision_id)
    if revision is None or revision.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "REVISION_NOT_FOUND", "message": "Workflow revision not found"},
        )
    try:
        estimate = estimate_revision(revision, request.target_node_ids, request.force_rerun)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_TARGET", "message": str(exc)},
        ) from exc
    if estimate["estimate_hash"] != request.estimate_hash:
        raise _conflict(
            "The estimate is stale; review the updated cost before running", "STALE_ESTIMATE"
        )
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
        raise HTTPException(
            status_code=404,
            detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"},
        )
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
        raise HTTPException(
            status_code=404,
            detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"},
        )
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
        raise HTTPException(
            status_code=404,
            detail={"code": "EXECUTION_NOT_FOUND", "message": "Workflow execution not found"},
        )

    async def stream():
        event_cursor = int(last_event_id or cursor)
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
        last_heartbeat = 0.0
        while True:
            from app.thikra.database import SessionLocal

            with SessionLocal() as session:
                current = session.get(WorkflowExecution, execution_id)
                events = list(
                    session.scalars(
                        select(StudioExecutionEvent)
                        .where(
                            StudioExecutionEvent.execution_id == execution_id,
                            StudioExecutionEvent.sequence > event_cursor,
                        )
                        .order_by(StudioExecutionEvent.sequence)
                    )
                )
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
                                - (
                                    current.started_at.replace(tzinfo=UTC)
                                    if current.started_at and current.started_at.tzinfo is None
                                    else current.started_at or datetime.now(UTC)
                                )
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

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        return editor.serialize_asset(asset)

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
def post_asset(
    project_id: str,
    background: BackgroundTasks,
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id)
    try:
        asset = import_asset(
            db,
            project,
            upload.filename or "asset",
            upload.content_type or "application/octet-stream",
            upload.file,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_ASSET", "message": str(exc)}
        ) from exc
    background.add_task(editor.prepare_asset, asset.id)
    return editor.serialize_asset(asset)


@router.get("/assets/{asset_id}/content")
def asset_content(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, asset_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "Studio asset not found"}
        )
    if asset.remote_url:
        from app.repo.pipelines import presign_asset_url

        return RedirectResponse(presign_asset_url(asset.remote_url), status_code=302)
    path = Path(asset.local_path or "").resolve()
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_CONTENT_MISSING", "message": "Studio asset content is missing"},
        )
    return FileResponse(path, media_type=asset.content_type, filename=asset.name)


@router.get("/assets/{asset_id}/thumbnail")
def asset_thumbnail(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, asset_id)
    path = Path(asset.thumbnail_path or "").resolve() if asset else None
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "THUMBNAIL_NOT_READY", "message": "Thumbnail is not ready"},
        )
    return FileResponse(path)


@router.get("/assets/{asset_id}/proxy")
def asset_proxy(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, asset_id)
    path = Path(asset.proxy_path or "").resolve() if asset else None
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "PROXY_NOT_READY", "message": "Editing proxy is not ready"},
        )
    return FileResponse(path, media_type="video/mp4")


@router.get("/assets/{asset_id}/waveform")
def asset_waveform(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, asset_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "Studio asset not found"}
        )
    if asset.analysis_status != "READY":
        raise HTTPException(
            status_code=409,
            detail={"code": "WAVEFORM_NOT_READY", "message": "Waveform analysis is not ready"},
        )
    return {"asset_id": asset.id, "samples": json.loads(asset.waveform_json or "[]")}


@router.post("/assets/{asset_id}/prepare", status_code=202)
def post_prepare_asset(asset_id: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, asset_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "Studio asset not found"}
        )
    if asset.analysis_status not in {"READY", "RUNNING"}:
        background.add_task(editor.prepare_asset, asset.id)
    return editor.serialize_asset(asset)


@router.get("/assets/{asset_id}/download")
def download_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, asset_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "Studio asset not found"}
        )
    if asset.local_path:
        path = Path(asset.local_path).resolve()
        if path.is_file():
            return FileResponse(path, media_type=asset.content_type, filename=asset.name)
    if not asset.remote_url:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_CONTENT_MISSING", "message": "Studio asset content is missing"},
        )
    from app.repo.pipelines import presign_asset_url

    return RedirectResponse(presign_asset_url(asset.remote_url), status_code=302)


def _sequence(db: Session, sequence_id: str) -> StudioSequence:
    item = db.get(StudioSequence, sequence_id)
    if item is None:
        raise HTTPException(
            status_code=404, detail={"code": "SEQUENCE_NOT_FOUND", "message": "Sequence not found"}
        )
    return item


@router.get("/projects/{project_id}/sequences")
def list_sequences(project_id: str, db: Session = Depends(get_db)):
    _project(db, project_id)
    items = list(
        db.scalars(
            select(StudioSequence)
            .where(StudioSequence.project_id == project_id)
            .order_by(StudioSequence.created_at)
        )
    )
    return {"items": [editor.serialize_sequence(db, item, detail=False) for item in items]}


@router.post("/projects/{project_id}/sequences", status_code=201)
def post_sequence(project_id: str, request: SequenceCreate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    return editor.serialize_sequence(
        db, editor.create_sequence(db, project, request.name, request.preset)
    )


@router.get("/sequences/{sequence_id}")
def get_sequence(sequence_id: str, db: Session = Depends(get_db)):
    return editor.serialize_sequence(db, _sequence(db, sequence_id))


@router.delete("/sequences/{sequence_id}", status_code=204)
def delete_sequence(sequence_id: str, db: Session = Depends(get_db)):
    sequence = _sequence(db, sequence_id)
    try:
        editor.delete_sequence(db, sequence)
    except RuntimeError as exc:
        raise _conflict("Cancel the active export before deleting this sequence", str(exc)) from exc


@router.get("/sequences/{sequence_id}/revisions")
def sequence_revisions(sequence_id: str, db: Session = Depends(get_db)):
    sequence = _sequence(db, sequence_id)
    from app.studio.models import SequenceRevision

    items = db.scalars(
        select(SequenceRevision)
        .where(SequenceRevision.sequence_id == sequence.id)
        .order_by(SequenceRevision.number.desc())
    )
    return {"items": [editor.serialize_revision(item) for item in items]}


@router.post("/sequences/{sequence_id}/revisions", status_code=201)
def post_sequence_revision(
    sequence_id: str, request: SequenceRevisionCreate, db: Session = Depends(get_db)
):
    sequence = _sequence(db, sequence_id)
    try:
        revision = editor.create_revision(
            db, sequence, request.base_revision_id, request.document, request.summary
        )
    except RuntimeError as exc:
        raise _conflict("The sequence changed while you were editing it", str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_SEQUENCE", "message": str(exc)}
        ) from exc
    return editor.serialize_revision(revision)


@router.post("/sequences/{sequence_id}/restore", status_code=201)
def restore_sequence_revision(
    sequence_id: str, request: SequenceRestore, db: Session = Depends(get_db)
):
    sequence = _sequence(db, sequence_id)
    try:
        return editor.serialize_revision(
            editor.restore_revision(db, sequence, request.base_revision_id, request.revision_id)
        )
    except RuntimeError as exc:
        raise _conflict("The sequence changed while you were restoring history", str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "REVISION_NOT_FOUND", "message": str(exc)}
        ) from exc


@router.patch("/sequences/{sequence_id}/view")
def patch_sequence_view(
    sequence_id: str, request: SequenceViewUpdate, db: Session = Depends(get_db)
):
    sequence = _sequence(db, sequence_id)
    sequence.view_state_json = json.dumps(request.model_dump(mode="json"), separators=(",", ":"))
    db.commit()
    return {"view_state": request.model_dump(mode="json")}


@router.post("/sequences/{sequence_id}/agent-proposals", status_code=201)
def post_sequence_proposal(
    sequence_id: str, request: SequenceProposalCreate, db: Session = Depends(get_db)
):
    sequence = _sequence(db, sequence_id)
    try:
        proposal = editor.create_sequence_proposal(
            db, sequence, request.base_revision_id, request.prompt, request.selected_clip_ids
        )
    except RuntimeError as exc:
        raise _conflict("The sequence changed; ask the editor agent again", str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_PROPOSAL_REQUEST", "message": str(exc)}
        ) from exc
    return editor.serialize_sequence_proposal(proposal)


@router.post("/sequences/{sequence_id}/agent-proposals/{proposal_id}/apply", status_code=201)
def post_apply_sequence_proposal(
    sequence_id: str,
    proposal_id: str,
    request: SequenceProposalApply,
    db: Session = Depends(get_db),
):
    sequence = _sequence(db, sequence_id)
    proposal = db.get(SequenceAgentProposal, proposal_id)
    if proposal is None or proposal.sequence_id != sequence.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROPOSAL_NOT_FOUND", "message": "Timeline proposal not found"},
        )
    try:
        revision = editor.apply_sequence_proposal(
            db, sequence, proposal, request.base_revision_id, request.operation_ids
        )
    except RuntimeError as exc:
        raise _conflict("The sequence changed; ask the editor agent again", str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_PROPOSAL", "message": str(exc)}
        ) from exc
    return editor.serialize_revision(revision)


@router.post("/sequences/{sequence_id}/renders", status_code=202)
def post_render(
    sequence_id: str,
    request: RenderCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    from app.studio.models import SequenceRevision

    sequence = _sequence(db, sequence_id)
    revision = db.get(SequenceRevision, request.revision_id)
    if revision is None or revision.sequence_id != sequence.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "REVISION_NOT_FOUND", "message": "Sequence revision not found"},
        )
    try:
        render = editor.create_render(db, sequence, revision, request.preset)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_SEQUENCE", "message": str(exc)}
        ) from exc
    if render.status != "SUCCEEDED":
        background.add_task(editor.execute_render, render.id)
    return editor.serialize_render(render)


@router.get("/renders/{render_id}")
def get_render(render_id: str, db: Session = Depends(get_db)):
    render = db.get(StudioRender, render_id)
    if render is None:
        raise HTTPException(
            status_code=404, detail={"code": "RENDER_NOT_FOUND", "message": "Export not found"}
        )
    return editor.serialize_render(render)


@router.post("/renders/{render_id}/cancel")
def cancel_render(render_id: str, db: Session = Depends(get_db)):
    render = db.get(StudioRender, render_id)
    if render is None:
        raise HTTPException(
            status_code=404, detail={"code": "RENDER_NOT_FOUND", "message": "Export not found"}
        )
    if render.status not in {"QUEUED", "RUNNING"}:
        raise _conflict("A finished export cannot be cancelled", "RENDER_TERMINAL")
    render.cancel_requested = True
    db.commit()
    return editor.serialize_render(render)


@router.post("/renders/{render_id}/retry", status_code=202)
def retry_render(
    render_id: str,
    request: RenderResume,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    original = db.get(StudioRender, render_id)
    if original is None:
        raise HTTPException(
            status_code=404, detail={"code": "RENDER_NOT_FOUND", "message": "Export not found"}
        )
    if original.status not in {"FAILED", "CANCELLED"}:
        raise _conflict("Only failed or cancelled exports can be retried", "RENDER_NOT_RESUMABLE")
    from app.studio.models import SequenceRevision

    sequence = _sequence(db, original.sequence_id)
    revision = db.get(SequenceRevision, original.revision_id)
    render = editor.create_render(db, sequence, revision, original.preset, resumed_from=original.id)
    if render.status != "SUCCEEDED":
        background.add_task(editor.execute_render, render.id)
    return editor.serialize_render(render)


@router.get("/renders/{render_id}/events")
def render_events(
    render_id: str,
    cursor: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
):
    if db.get(StudioRender, render_id) is None:
        raise HTTPException(
            status_code=404, detail={"code": "RENDER_NOT_FOUND", "message": "Export not found"}
        )

    async def stream():
        event_cursor = int(last_event_id or cursor)
        while True:
            from app.thikra.database import SessionLocal

            with SessionLocal() as session:
                current = session.get(StudioRender, render_id)
                events = list(
                    session.scalars(
                        select(StudioRenderEvent)
                        .where(
                            StudioRenderEvent.render_id == render_id,
                            StudioRenderEvent.sequence > event_cursor,
                        )
                        .order_by(StudioRenderEvent.sequence)
                    )
                )
                for event in events:
                    event_cursor = event.sequence
                    payload = json.loads(event.payload_json)
                    envelope = {
                        "eventId": str(event.sequence),
                        "renderId": render_id,
                        "revisionId": current.revision_id,
                        "type": event.event_type,
                        "status": current.status,
                        **payload,
                    }
                    yield f"id: {event.sequence}\ndata: {json.dumps(envelope, default=str)}\n\n"
                if current.status in {"SUCCEEDED", "FAILED", "CANCELLED"} and not events:
                    break
            await asyncio.sleep(0.2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/projects/{project_id}/editor-generation/estimate")
def post_editor_generation_estimate(
    project_id: str, request: EditorGenerationEstimate, db: Session = Depends(get_db)
):
    project = _project(db, project_id)
    try:
        return editor.generation_estimate(db, project, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "GENERATION_UNAVAILABLE", "message": str(exc)}
        ) from exc


@router.post("/projects/{project_id}/editor-generation/jobs", status_code=202)
def post_editor_generation_job(
    project_id: str,
    request: EditorGenerationCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    project = _project(db, project_id)
    fields = request.model_dump(exclude={"estimate_hash", "confirm", "sequence_id"})
    try:
        estimate = editor.generation_estimate(db, project, **fields)
        job = editor.create_generation_job(
            db, project, estimate, request.estimate_hash, request.sequence_id
        )
    except RuntimeError as exc:
        raise _conflict("The generation estimate or project budget changed", str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "GENERATION_UNAVAILABLE", "message": str(exc)}
        ) from exc
    background.add_task(editor.execute_generation_job, job.id)
    return editor.serialize_generation_job(job)


@router.get("/editor-generation/jobs/{job_id}")
def get_editor_generation_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(StudioGenerationJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "GENERATION_JOB_NOT_FOUND", "message": "Generation job not found"},
        )
    return editor.serialize_generation_job(job)


@router.post("/editor-generation/jobs/{job_id}/cancel")
def cancel_editor_generation_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(StudioGenerationJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "GENERATION_JOB_NOT_FOUND", "message": "Generation job not found"},
        )
    if job.status not in {"QUEUED", "RUNNING"}:
        raise _conflict("A finished generation cannot be cancelled", "GENERATION_TERMINAL")
    job.cancel_requested = True
    db.commit()
    return editor.serialize_generation_job(job)


@router.post("/editor-generation/jobs/{job_id}/resume-estimate")
def editor_generation_resume_estimate(job_id: str, db: Session = Depends(get_db)):
    job = db.get(StudioGenerationJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "GENERATION_JOB_NOT_FOUND", "message": "Generation job not found"},
        )
    try:
        return editor.generation_resume_estimate(db, job)
    except RuntimeError as exc:
        raise _conflict("Only failed or cancelled generations can be resumed", str(exc)) from exc


@router.post("/editor-generation/jobs/{job_id}/resume", status_code=202)
def resume_editor_generation_job(
    job_id: str,
    request: EditorJobResume,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    original = db.get(StudioGenerationJob, job_id)
    if original is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "GENERATION_JOB_NOT_FOUND", "message": "Generation job not found"},
        )
    try:
        estimate = editor.generation_resume_estimate(db, original)
        job = editor.create_resume_generation_job(db, original, estimate, request.estimate_hash)
    except RuntimeError as exc:
        raise _conflict("The resume estimate changed", str(exc)) from exc
    if job.status != "SUCCEEDED":
        background.add_task(editor.execute_generation_job, job.id)
    return editor.serialize_generation_job(job)


@router.get("/editor-generation/jobs/{job_id}/events")
def editor_generation_events(
    job_id: str,
    cursor: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
):
    if db.get(StudioGenerationJob, job_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "GENERATION_JOB_NOT_FOUND", "message": "Generation job not found"},
        )

    async def stream():
        event_cursor = int(last_event_id or cursor)
        while True:
            from app.thikra.database import SessionLocal

            with SessionLocal() as session:
                current = session.get(StudioGenerationJob, job_id)
                events = list(
                    session.scalars(
                        select(StudioJobEvent)
                        .where(
                            StudioJobEvent.job_kind == "generation",
                            StudioJobEvent.job_id == job_id,
                            StudioJobEvent.sequence > event_cursor,
                        )
                        .order_by(StudioJobEvent.sequence)
                    )
                )
                for event in events:
                    event_cursor = event.sequence
                    payload = json.loads(event.payload_json)
                    envelope = {
                        "eventId": str(event.sequence),
                        "jobId": job_id,
                        "type": event.event_type,
                        "status": current.status,
                        **payload,
                    }
                    yield f"id: {event.sequence}\ndata: {json.dumps(envelope, default=str)}\n\n"
                if current.status in {"SUCCEEDED", "FAILED", "CANCELLED"} and not events:
                    break
            await asyncio.sleep(0.2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sequences/{sequence_id}/captions/estimate")
def post_caption_estimate(
    sequence_id: str, request: CaptionEstimate, db: Session = Depends(get_db)
):
    sequence = _sequence(db, sequence_id)
    project = _project(db, sequence.project_id)
    try:
        return editor.caption_estimate(db, project, sequence, request.revision_id, request.language)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "CAPTION_UNAVAILABLE", "message": str(exc)}
        ) from exc


@router.post("/sequences/{sequence_id}/captions/jobs", status_code=202)
def post_caption_job(
    sequence_id: str,
    request: CaptionCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    sequence = _sequence(db, sequence_id)
    project = _project(db, sequence.project_id)
    try:
        estimate = editor.caption_estimate(
            db, project, sequence, request.revision_id, request.language
        )
        job = editor.create_caption_job(db, project, estimate, request.estimate_hash)
    except RuntimeError as exc:
        raise _conflict("The caption estimate or project budget changed", str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "CAPTION_UNAVAILABLE", "message": str(exc)}
        ) from exc
    background.add_task(editor.execute_caption_job, job.id)
    return editor.serialize_caption_job(job)


@router.get("/captions/jobs/{job_id}")
def get_caption_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(StudioCaptionJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CAPTION_JOB_NOT_FOUND", "message": "Caption job not found"},
        )
    return editor.serialize_caption_job(job)


@router.post("/captions/jobs/{job_id}/cancel")
def cancel_caption_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(StudioCaptionJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CAPTION_JOB_NOT_FOUND", "message": "Caption job not found"},
        )
    if job.status not in {"QUEUED", "RUNNING"}:
        raise _conflict("A finished caption job cannot be cancelled", "CAPTION_JOB_TERMINAL")
    job.cancel_requested = True
    db.commit()
    return editor.serialize_caption_job(job)


@router.get("/captions/jobs/{job_id}/events")
def caption_job_events(
    job_id: str,
    cursor: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
):
    if db.get(StudioCaptionJob, job_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CAPTION_JOB_NOT_FOUND", "message": "Caption job not found"},
        )

    async def stream():
        event_cursor = int(last_event_id or cursor)
        while True:
            from app.thikra.database import SessionLocal

            with SessionLocal() as session:
                current = session.get(StudioCaptionJob, job_id)
                events = list(
                    session.scalars(
                        select(StudioJobEvent)
                        .where(
                            StudioJobEvent.job_kind == "caption",
                            StudioJobEvent.job_id == job_id,
                            StudioJobEvent.sequence > event_cursor,
                        )
                        .order_by(StudioJobEvent.sequence)
                    )
                )
                for event in events:
                    event_cursor = event.sequence
                    payload = json.loads(event.payload_json)
                    envelope = {
                        "eventId": str(event.sequence),
                        "jobId": job_id,
                        "type": event.event_type,
                        "status": current.status,
                        **payload,
                    }
                    yield f"id: {event.sequence}\ndata: {json.dumps(envelope, default=str)}\n\n"
                if current.status in {"SUCCEEDED", "FAILED", "CANCELLED"} and not events:
                    break
            await asyncio.sleep(0.2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sequences/{sequence_id}/captions/apply", status_code=201)
def apply_caption_job(sequence_id: str, request: CaptionApply, db: Session = Depends(get_db)):
    sequence = _sequence(db, sequence_id)
    try:
        revision = editor.apply_caption_cues(db, sequence, request.base_revision_id, request.cues)
    except RuntimeError as exc:
        raise _conflict(
            "The sequence changed; review captions against the latest edit", str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_CAPTIONS", "message": str(exc)}
        ) from exc
    return editor.serialize_revision(revision)


@router.post("/projects/{project_id}/annotations", status_code=201)
def post_annotation(project_id: str, request: AnnotationCreate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    asset = db.get(StudioAsset, request.asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(
            status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "Studio asset not found"}
        )
    if request.kind in {"point", "rectangle"} and any(
        value < 0 or value > 1 for value in request.geometry.values()
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_GEOMETRY",
                "message": "Annotation coordinates must be normalized from 0 to 1",
            },
        )
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
    return {
        "id": item.id,
        "asset_id": item.asset_id,
        "kind": item.kind,
        "geometry": request.geometry,
        "body": item.body,
        "timestamp_ms": item.timestamp_ms,
    }


@router.get("/provider-connections")
def connections():
    return {"items": provider_connection_status()}


@router.put("/provider-connections/{vendor}")
def put_connection(vendor: str, request: ProviderConnectionSet):
    try:
        set_provider_secret(vendor, request.secret)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=422, detail={"code": "CONNECTION_STORE_FAILED", "message": str(exc)}
        ) from exc
    return {"vendor": vendor, "configured": True, "source": "personal"}


@router.delete("/provider-connections/{vendor}")
def delete_connection(vendor: str):
    try:
        clear_provider_secret(vendor)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": str(exc)}
        ) from exc
    return {"vendor": vendor, "configured": False}
