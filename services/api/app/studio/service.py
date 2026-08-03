"""Studio project versioning, proposals, estimates, local assets, and execution."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.repo import provider_catalog as catalog
from app.studio.graph import (
    apply_operations,
    canonical_graph,
    default_graph,
    graph_hash,
    topological_nodes,
    validate_graph,
)
from app.studio.models import (
    AgentProposal,
    NodeExecution,
    SequenceAgentProposal,
    SequenceRevision,
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
    ProposalConfigPatch,
    ProposalOperation,
    WorkflowGraph,
    WorkflowProposalOutput,
)
from app.thikra.database import SessionLocal
from app.thikra.models import Workspace

NODE_COST_MINOR = {
    "look_director": 2,
    "image_generation": 18,
    "video_generation": 105,
    "narration": 8,
    "music": 15,
    "composition": 2,
    "verification": 5,
}
MEDIA_LIMITS = {
    "image": (25 * 1024 * 1024, {"image/png", "image/jpeg", "image/webp"}),
    "audio": (
        100 * 1024 * 1024,
        {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/m4a"},
    ),
    "video": (500 * 1024 * 1024, {"video/mp4", "video/webm", "video/quicktime", "video/x-m4v"}),
}
KEYRING_SERVICE = "thikra-studio"
VENDOR_SETTING = {
    "openai": "openai_api_key",
    "google": "google_api_key",
    "decart": "decart_api_key",
    "nvidia": "nvidia_api_key",
    "gmicloud": "gmi_api_key",
    "replicate": "replicate_api_token",
    "runway": "runway_api_secret",
    "luma": "luma_api_key",
    "elevenlabs": "elevenlabs_api_key",
    "lmnt": "lmnt_api_key",
    "hume": "hume_api_key",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _workspace(db: Session) -> Workspace:
    workspace = db.scalar(select(Workspace).order_by(Workspace.created_at))
    if workspace is None:
        raise RuntimeError("Studio requires the seeded local workspace")
    return workspace


def serialize_revision(revision: WorkflowRevision) -> dict:
    return {
        "id": revision.id,
        "project_id": revision.project_id,
        "parent_revision_id": revision.parent_revision_id,
        "number": revision.number,
        "schema_version": revision.schema_version,
        "graph": _load(revision.graph_json, {}),
        "content_hash": revision.content_hash,
        "summary": revision.summary,
        "source": revision.source,
        "created_at": revision.created_at,
    }


def serialize_project(db: Session, project: StudioProject, *, detail: bool = True) -> dict:
    result = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "currency": project.currency,
        "budget_cap_minor": project.budget_cap_minor,
        "spent_minor": project.spent_minor,
        "remaining_minor": max(0, project.budget_cap_minor - project.spent_minor),
        "current_revision_id": project.current_revision_id,
        "current_revision_number": project.current_revision_number,
        "updated_at": project.updated_at,
    }
    if detail:
        revision = db.get(WorkflowRevision, project.current_revision_id)
        result.update(
            {
                "revision": serialize_revision(revision) if revision else None,
                "layout": _load(project.layout_json, {}),
                "viewport": _load(project.viewport_json, {"x": 0, "y": 0, "zoom": 1}),
            }
        )
    return result


def create_project(
    db: Session,
    *,
    name: str,
    description: str,
    budget: int,
    currency: str,
    graph: WorkflowGraph | None,
) -> StudioProject:
    workflow = graph or default_graph()
    errors = validate_graph(workflow)
    if errors:
        raise ValueError("; ".join(errors))
    project = StudioProject(
        workspace_id=_workspace(db).id,
        name=name,
        description=description,
        budget_cap_minor=budget,
        currency=currency.upper(),
    )
    db.add(project)
    db.flush()
    revision = WorkflowRevision(
        project_id=project.id,
        number=1,
        schema_version=1,
        graph_json=canonical_graph(workflow),
        content_hash=graph_hash(workflow),
        summary="Initial Studio workflow",
        source="TEMPLATE" if graph is None else "MANUAL",
    )
    db.add(revision)
    db.flush()
    project.current_revision_id = revision.id
    project.current_revision_number = 1
    db.commit()
    return project


def delete_project(db: Session, project: StudioProject) -> None:
    running = db.scalar(
        select(func.count())
        .select_from(WorkflowExecution)
        .where(
            WorkflowExecution.project_id == project.id,
            WorkflowExecution.status.in_({"QUEUED", "RUNNING"}),
        )
    )
    if running:
        raise RuntimeError("PROJECT_EXECUTION_ACTIVE")
    active_editor = db.scalar(
        select(func.count())
        .select_from(StudioRender)
        .where(
            StudioRender.project_id == project.id,
            StudioRender.status.in_({"QUEUED", "RUNNING"}),
        )
    ) or db.scalar(
        select(func.count())
        .select_from(StudioGenerationJob)
        .where(
            StudioGenerationJob.project_id == project.id,
            StudioGenerationJob.status.in_({"QUEUED", "RUNNING"}),
        )
    )
    if active_editor:
        raise RuntimeError("PROJECT_EDITOR_JOB_ACTIVE")

    execution_ids = select(WorkflowExecution.id).where(WorkflowExecution.project_id == project.id)
    db.execute(
        delete(StudioExecutionEvent).where(StudioExecutionEvent.execution_id.in_(execution_ids))
    )
    db.execute(delete(NodeExecution).where(NodeExecution.execution_id.in_(execution_ids)))
    db.execute(delete(WorkflowExecution).where(WorkflowExecution.project_id == project.id))
    db.execute(delete(StudioAnnotation).where(StudioAnnotation.project_id == project.id))
    sequence_ids = select(StudioSequence.id).where(StudioSequence.project_id == project.id)
    revision_ids = select(SequenceRevision.id).where(SequenceRevision.sequence_id.in_(sequence_ids))
    render_ids = select(StudioRender.id).where(StudioRender.project_id == project.id)
    generation_ids = select(StudioGenerationJob.id).where(
        StudioGenerationJob.project_id == project.id
    )
    caption_ids = select(StudioCaptionJob.id).where(StudioCaptionJob.project_id == project.id)
    db.execute(delete(StudioRenderEvent).where(StudioRenderEvent.render_id.in_(render_ids)))
    db.execute(
        delete(StudioJobEvent).where(
            ((StudioJobEvent.job_kind == "generation") & StudioJobEvent.job_id.in_(generation_ids))
            | ((StudioJobEvent.job_kind == "caption") & StudioJobEvent.job_id.in_(caption_ids))
        )
    )
    db.execute(delete(StudioRender).where(StudioRender.project_id == project.id))
    db.execute(delete(StudioGenerationJob).where(StudioGenerationJob.project_id == project.id))
    db.execute(delete(StudioCaptionJob).where(StudioCaptionJob.project_id == project.id))
    db.execute(
        delete(SequenceAgentProposal).where(SequenceAgentProposal.sequence_id.in_(sequence_ids))
    )
    db.execute(delete(SequenceRevision).where(SequenceRevision.id.in_(revision_ids)))
    db.execute(delete(StudioSequence).where(StudioSequence.project_id == project.id))
    db.execute(delete(StudioAsset).where(StudioAsset.project_id == project.id))
    db.execute(delete(AgentProposal).where(AgentProposal.project_id == project.id))
    db.execute(delete(WorkflowRevision).where(WorkflowRevision.project_id == project.id))
    db.delete(project)
    db.commit()

    studio_root = (Path(settings.thikra_data_dir) / "studio").resolve()
    for category in ("imports", "generated"):
        target = (studio_root / category / project.id).resolve()
        if target.parent == (studio_root / category).resolve() and target.is_dir():
            shutil.rmtree(target)


def create_revision(
    db: Session,
    project: StudioProject,
    base_revision_id: str,
    graph: WorkflowGraph,
    summary: str,
    *,
    source: str = "MANUAL",
) -> WorkflowRevision:
    if project.current_revision_id != base_revision_id:
        raise RuntimeError("STALE_REVISION")
    errors = validate_graph(graph)
    if errors:
        raise ValueError("; ".join(errors))
    revision = WorkflowRevision(
        project_id=project.id,
        parent_revision_id=project.current_revision_id,
        number=project.current_revision_number + 1,
        schema_version=graph.schema_version,
        graph_json=canonical_graph(graph),
        content_hash=graph_hash(graph),
        summary=summary,
        source=source,
    )
    db.add(revision)
    db.flush()
    project.current_revision_id = revision.id
    project.current_revision_number = revision.number
    db.commit()
    return revision


def _demo_proposal(prompt: str, graph: WorkflowGraph) -> WorkflowProposalOutput:
    brief = next((node for node in graph.nodes if node.type == "creative_brief"), None)
    operations: list[ProposalOperation] = []
    if brief:
        operations.append(
            ProposalOperation(
                id="op-brief",
                type="update_node",
                node_id=brief.id,
                node=None,
                edge=None,
                config_patch=ProposalConfigPatch(
                    text=prompt,
                    prompt_guidance=None,
                    variants=None,
                    vendor=None,
                    model=None,
                    duration_sec=None,
                    selected_index=None,
                    format=None,
                ),
                depends_on=[],
                summary="Shape the creative brief around your direction",
            )
        )
    image = next((node for node in graph.nodes if node.type == "image_generation"), None)
    if image:
        operations.append(
            ProposalOperation(
                id="op-variants",
                type="update_node",
                node_id=image.id,
                node=None,
                edge=None,
                config_patch=ProposalConfigPatch(
                    text=None,
                    prompt_guidance=prompt,
                    variants=4,
                    vendor=None,
                    model=None,
                    duration_sec=None,
                    selected_index=None,
                    format=None,
                ),
                depends_on=["op-brief"] if brief else [],
                summary="Explore four controlled visual variants",
            )
        )
    return WorkflowProposalOutput(
        rationale="I kept the workflow readable and concentrated exploration in the visual branch before animation.",
        estimated_cost_impact_minor=18,
        operations=operations,
    )


def create_proposal(
    db: Session,
    project: StudioProject,
    base_revision_id: str,
    prompt: str,
    selected_node_ids: list[str],
    asset_urls: list[str] | None = None,
    annotations: list[dict] | None = None,
) -> AgentProposal:
    if project.current_revision_id != base_revision_id:
        raise RuntimeError("STALE_REVISION")
    revision = db.get(WorkflowRevision, base_revision_id)
    graph = WorkflowGraph.model_validate(_load(revision.graph_json, {}))
    if settings.app_mode.upper() == "DEMO" or not effective_provider_secret("openai"):
        output = _demo_proposal(prompt, graph)
    else:
        from app.repo.pipelines import generate_workflow_proposal

        output = generate_workflow_proposal(
            prompt,
            graph,
            selected_node_ids,
            asset_urls or [],
            annotations or [],
            api_key=effective_provider_secret("openai"),
        )
    proposal = AgentProposal(
        project_id=project.id,
        base_revision_id=base_revision_id,
        prompt=prompt,
        selected_node_ids_json=_json(selected_node_ids),
        operations_json=_json([item.model_dump(mode="json") for item in output.operations]),
        rationale=output.rationale,
        estimated_cost_impact_minor=output.estimated_cost_impact_minor,
    )
    db.add(proposal)
    db.commit()
    return proposal


def proposal_asset_urls(db: Session, project_id: str, asset_ids: list[str]) -> list[str]:
    urls: list[str] = []
    for asset_id in asset_ids[:4]:
        asset = db.get(StudioAsset, asset_id)
        if (
            asset is None
            or asset.project_id != project_id
            or not asset.content_type.startswith("image/")
        ):
            continue
        if asset.remote_url:
            from app.studio.storage_connection import studio_presign_asset_url

            urls.append(studio_presign_asset_url(asset.remote_url))
        elif asset.local_path and asset.size <= 5 * 1024 * 1024:
            encoded = base64.b64encode(Path(asset.local_path).read_bytes()).decode()
            urls.append(f"data:{asset.content_type};base64,{encoded}")
    return urls


def proposal_annotations(db: Session, project_id: str, asset_ids: list[str]) -> list[dict]:
    if not asset_ids:
        return []
    items = list(
        db.scalars(
            select(StudioAnnotation)
            .where(
                StudioAnnotation.project_id == project_id,
                StudioAnnotation.asset_id.in_(asset_ids[:4]),
            )
            .order_by(StudioAnnotation.created_at)
        )
    )
    return [
        {
            "asset_id": item.asset_id,
            "kind": item.kind,
            "geometry": _load(item.geometry_json, {}),
            "body": item.body,
            "timestamp_ms": item.timestamp_ms,
        }
        for item in items
    ]


def validate_provider_configuration(
    graph: WorkflowGraph, node_ids: set[str] | None = None
) -> list[str]:
    if settings.app_mode.upper() == "DEMO":
        return []
    errors: list[str] = []
    slots = {
        "image_generation": (catalog.IMAGE, "replicate"),
        "video_generation": (catalog.VIDEO, settings.video_provider),
        "narration": (catalog.TTS, "openai"),
        "music": (catalog.MUSIC, "replicate"),
    }
    for node in graph.nodes:
        if node_ids is not None and node.id not in node_ids:
            continue
        slot_default = slots.get(node.type)
        if not slot_default:
            continue
        slot, default_vendor = slot_default
        vendor = str(node.config.get("vendor") or default_vendor)
        try:
            entry = catalog.resolve(slot, vendor)
        except ValueError:
            errors.append(f"{node.label} selects unavailable provider '{vendor}'")
            continue
        if not effective_provider_secret(vendor):
            errors.append(f"{node.label} requires a configured {vendor} credential")
        if entry.snap_durations and "duration_sec" in node.config:
            duration = float(node.config["duration_sec"])
            if duration not in entry.snap_durations:
                allowed = ", ".join(f"{value:g}s" for value in entry.snap_durations)
                errors.append(f"{node.label} duration must be one of {allowed}")
    return errors


def serialize_proposal(proposal: AgentProposal) -> dict:
    return {
        "id": proposal.id,
        "project_id": proposal.project_id,
        "base_revision_id": proposal.base_revision_id,
        "prompt": proposal.prompt,
        "selected_node_ids": _load(proposal.selected_node_ids_json, []),
        "operations": _load(proposal.operations_json, []),
        "rationale": proposal.rationale,
        "estimated_cost_impact_minor": proposal.estimated_cost_impact_minor,
        "status": proposal.status,
        "created_at": proposal.created_at,
    }


def apply_proposal(
    db: Session,
    project: StudioProject,
    proposal: AgentProposal,
    base_revision_id: str,
    operation_ids: list[str],
) -> WorkflowRevision:
    if (
        project.current_revision_id != base_revision_id
        or proposal.base_revision_id != base_revision_id
    ):
        raise RuntimeError("STALE_REVISION")
    all_operations = [
        ProposalOperation.model_validate(item) for item in _load(proposal.operations_json, [])
    ]
    by_id = {item.id: item for item in all_operations}
    selected = set(operation_ids)
    pending = list(selected)
    while pending:
        current = by_id.get(pending.pop())
        if not current:
            raise ValueError("Unknown proposal operation")
        for dependency in current.depends_on:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    revision = db.get(WorkflowRevision, base_revision_id)
    graph = WorkflowGraph.model_validate(_load(revision.graph_json, {}))
    updated = apply_operations(graph, [item for item in all_operations if item.id in selected])
    result = create_revision(
        db,
        project,
        base_revision_id,
        updated,
        f"Agent proposal: {proposal.prompt[:420]}",
        source="AGENT",
    )
    proposal.status = "APPLIED"
    db.commit()
    return result


def estimate_revision(
    revision: WorkflowRevision, target_node_ids: list[str], force_rerun: bool
) -> dict:
    graph = WorkflowGraph.model_validate(_load(revision.graph_json, {}))
    known = {node.id for node in graph.nodes}
    unknown = set(target_node_ids) - known
    if unknown:
        raise ValueError(f"Unknown target node(s): {', '.join(sorted(unknown))}")
    targets = set(target_node_ids)
    if targets:
        children: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
        for edge in graph.edges:
            children[edge.source].add(edge.target)
        pending = list(targets)
        while pending:
            for child in children[pending.pop()]:
                if child not in targets:
                    targets.add(child)
                    pending.append(child)
    else:
        targets = known
    line_items = []
    total = 0
    for node in topological_nodes(graph):
        if targets and node.id not in targets:
            continue
        multiplier = (
            node.config.get("variants", 1)
            if node.type in {"image_generation", "video_generation"}
            else 1
        )
        amount = NODE_COST_MINOR.get(node.type, 0) * int(multiplier)
        if amount:
            line_items.append(
                {
                    "node_id": node.id,
                    "node_type": node.type,
                    "amount_minor": amount,
                    "estimated": True,
                }
            )
            total += amount
    document = {
        "revision_id": revision.id,
        "revision_hash": revision.content_hash,
        "target_node_ids": sorted(targets),
        "force_rerun": force_rerun,
        "estimated_cost_minor": total,
        "line_items": line_items,
        "execution_node_ids": sorted(targets),
    }
    document["estimate_hash"] = hashlib.sha256(_json(document).encode()).hexdigest()
    return document


def _event(
    db: Session, execution: WorkflowExecution, event_type: str, node_id: str | None, payload: dict
) -> StudioExecutionEvent:
    sequence = (
        int(
            db.scalar(
                select(func.coalesce(func.max(StudioExecutionEvent.sequence), 0)).where(
                    StudioExecutionEvent.execution_id == execution.id
                )
            )
            or 0
        )
        + 1
    )
    event = StudioExecutionEvent(
        execution_id=execution.id,
        sequence=sequence,
        node_id=node_id,
        event_type=event_type,
        payload_json=_json(payload),
    )
    db.add(event)
    db.flush()
    return event


def create_execution(
    db: Session,
    project: StudioProject,
    revision: WorkflowRevision,
    estimate: dict,
    force_rerun: bool,
    *,
    resumed_from_execution_id: str | None = None,
) -> WorkflowExecution:
    if resumed_from_execution_id is None and project.current_revision_id != revision.id:
        raise RuntimeError("STALE_REVISION")
    if project.spent_minor + estimate["estimated_cost_minor"] > project.budget_cap_minor:
        raise RuntimeError("BUDGET_EXCEEDED")
    graph = WorkflowGraph.model_validate(_load(revision.graph_json, {}))
    paid_node_ids = set(estimate["execution_node_ids"]) - set(
        estimate.get("recoverable_node_ids", [])
    )
    provider_errors = validate_provider_configuration(graph, paid_node_ids)
    if provider_errors:
        raise RuntimeError("PROVIDER_PREFLIGHT_FAILED:" + "; ".join(provider_errors))
    execution = WorkflowExecution(
        project_id=project.id,
        revision_id=revision.id,
        status="QUEUED",
        estimated_cost_minor=estimate["estimated_cost_minor"],
        estimate_hash=estimate["estimate_hash"],
        target_node_ids_json=_json(estimate["execution_node_ids"]),
        force_rerun=force_rerun,
        resumed_from_execution_id=resumed_from_execution_id,
    )
    db.add(execution)
    db.flush()
    costs = {item["node_id"]: item["amount_minor"] for item in estimate["line_items"]}
    execution_node_ids = set(estimate["execution_node_ids"])
    for node in topological_nodes(graph):
        if node.id not in execution_node_ids:
            continue
        db.add(
            NodeExecution(
                execution_id=execution.id,
                node_id=node.id,
                node_type=node.type,
                status="QUEUED",
                cache_key="pending",
                estimated_cost_minor=costs.get(node.id, 0),
            )
        )
    _event(db, execution, "execution.queued", None, {"message": "Workflow queued"})
    db.commit()
    return execution


def _provider_checkpoints(db: Session, execution_id: str) -> dict[str, list[dict]]:
    checkpoints: dict[str, list[dict]] = {}
    events = db.scalars(
        select(StudioExecutionEvent)
        .where(
            StudioExecutionEvent.execution_id == execution_id,
            StudioExecutionEvent.event_type == "node.provider_completed",
        )
        .order_by(StudioExecutionEvent.sequence)
    )
    for event in events:
        payload = _load(event.payload_json, {})
        assets = payload.get("assets", [])
        if event.node_id and assets:
            checkpoints[event.node_id] = assets
    return checkpoints


def _execution_lineage(db: Session, execution: WorkflowExecution) -> list[WorkflowExecution]:
    lineage: list[WorkflowExecution] = []
    seen: set[str] = set()
    current: WorkflowExecution | None = execution
    while current is not None and current.id not in seen:
        lineage.append(current)
        seen.add(current.id)
        current = (
            db.get(WorkflowExecution, current.resumed_from_execution_id)
            if current.resumed_from_execution_id
            else None
        )
    return lineage


def _lineage_checkpoints(db: Session, execution: WorkflowExecution) -> dict[str, list[dict]]:
    checkpoints: dict[str, list[dict]] = {}
    for ancestor in _execution_lineage(db, execution):
        for node_id, assets in _provider_checkpoints(db, ancestor.id).items():
            checkpoints.setdefault(node_id, assets)
    return checkpoints


def estimate_resume(db: Session, execution: WorkflowExecution) -> dict:
    if execution.status not in {"FAILED", "CANCELLED"}:
        raise RuntimeError("EXECUTION_NOT_RESUMABLE")
    revision = db.get(WorkflowRevision, execution.revision_id)
    records_by_node: dict[str, NodeExecution] = {}
    for ancestor in _execution_lineage(db, execution):
        for record in db.scalars(
            select(NodeExecution).where(NodeExecution.execution_id == ancestor.id)
        ):
            records_by_node.setdefault(record.node_id, record)
    records = list(records_by_node.values())
    media_nodes = {
        "image_generation",
        "video_generation",
        "narration",
        "music",
        "composition",
    }
    unresolved = [
        item
        for item in records
        if item.status not in {"SUCCEEDED", "CACHED"}
        or (
            item.node_type in media_nodes
            and not _load(item.output_json, {}).get("assets")
            and not _load(item.output_json, {}).get("simulated")
        )
    ]
    if not unresolved:
        raise RuntimeError("EXECUTION_NOT_RESUMABLE")
    estimate = estimate_revision(revision, [item.node_id for item in unresolved], False)
    checkpoints = _lineage_checkpoints(db, execution)
    recoverable = sorted(set(estimate["execution_node_ids"]).intersection(checkpoints))
    if recoverable:
        estimate["line_items"] = [
            item for item in estimate["line_items"] if item["node_id"] not in recoverable
        ]
        estimate["estimated_cost_minor"] = sum(
            item["amount_minor"] for item in estimate["line_items"]
        )
    estimate.pop("estimate_hash", None)
    estimate["resume_from_execution_id"] = execution.id
    estimate["recoverable_node_ids"] = recoverable
    estimate["reused_node_ids"] = sorted(
        item.node_id
        for item in records
        if item.status in {"SUCCEEDED", "CACHED"}
        and not (
            item.node_type in media_nodes
            and not _load(item.output_json, {}).get("assets")
            and not _load(item.output_json, {}).get("simulated")
        )
    )
    estimate["estimate_hash"] = hashlib.sha256(_json(estimate).encode()).hexdigest()
    return estimate


def create_resume_execution(
    db: Session,
    project: StudioProject,
    original: WorkflowExecution,
    estimate: dict,
) -> WorkflowExecution:
    revision = db.get(WorkflowRevision, original.revision_id)
    execution = create_execution(
        db,
        project,
        revision,
        estimate,
        False,
        resumed_from_execution_id=original.id,
    )
    checkpoints = _lineage_checkpoints(db, original)
    graph = WorkflowGraph.model_validate(_load(revision.graph_json, {}))
    node_types = {node.id: node.type for node in graph.nodes}
    output_kinds = {
        "image_generation": "variant_set",
        "video_generation": "variant_set",
        "narration": "audio",
        "music": "audio",
        "composition": "media",
    }
    for node_id in estimate.get("recoverable_node_ids", []):
        record = db.scalar(
            select(NodeExecution).where(
                NodeExecution.execution_id == execution.id,
                NodeExecution.node_id == node_id,
            )
        )
        if record is None:
            continue
        assets = _persist_asset_descriptors(
            db, project.id, node_id, checkpoints[node_id], allow_remote_only=True
        )
        record.status = "CACHED"
        record.cache_key = (
            "checkpoint:" + hashlib.sha256(_json(checkpoints[node_id]).encode()).hexdigest()[:53]
        )
        record.output_json = _json(
            {"kind": output_kinds.get(node_types[node_id], node_types[node_id]), "assets": assets}
        )
        record.estimated_cost_minor = 0
        _event(
            db,
            execution,
            "node.recovered",
            node_id,
            {"message": "Recovered provider output without another paid generation"},
        )
    _event(
        db,
        execution,
        "execution.resumed",
        None,
        {
            "message": f"Resuming from execution {original.id[:8]}",
            "reused_node_ids": estimate.get("reused_node_ids", []),
            "recoverable_node_ids": estimate.get("recoverable_node_ids", []),
        },
    )
    db.commit()
    return execution


def serialize_execution(db: Session, execution: WorkflowExecution, *, detail: bool = True) -> dict:
    result = {
        "id": execution.id,
        "project_id": execution.project_id,
        "revision_id": execution.revision_id,
        "status": execution.status,
        "estimated_cost_minor": execution.estimated_cost_minor,
        "cancel_requested": execution.cancel_requested,
        "target_node_ids": _load(execution.target_node_ids_json, []),
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "failure_reason": execution.failure_reason,
        "resumed_from_execution_id": execution.resumed_from_execution_id,
    }
    if detail:
        nodes = list(
            db.scalars(
                select(NodeExecution)
                .where(NodeExecution.execution_id == execution.id)
                .order_by(NodeExecution.created_at)
            )
        )
        result["nodes"] = [
            {
                "id": item.id,
                "node_id": item.node_id,
                "node_type": item.node_type,
                "status": item.status,
                "output": _load(item.output_json, {}),
                "error": item.error,
                "estimated_cost_minor": item.estimated_cost_minor,
                "charged_minor": item.charged_minor,
            }
            for item in nodes
        ]
    return result


def interrupt_incomplete_executions(db: Session) -> int:
    executions = list(
        db.scalars(
            select(WorkflowExecution).where(WorkflowExecution.status.in_({"QUEUED", "RUNNING"}))
        )
    )
    now = datetime.now(UTC)
    for execution in executions:
        execution.status = "FAILED"
        execution.failure_reason = "Local API restarted before this execution completed"
        execution.finished_at = now
        for node in db.scalars(
            select(NodeExecution).where(
                NodeExecution.execution_id == execution.id,
                NodeExecution.status.in_({"QUEUED", "RUNNING"}),
            )
        ):
            node.status = "FAILED" if node.status == "RUNNING" else "BLOCKED"
            node.error = "Interrupted by local API restart"
        _event(
            db,
            execution,
            "execution.interrupted",
            None,
            {"message": "Execution interrupted by local API restart"},
        )
    if executions:
        db.commit()
    return len(executions)


def _demo_svg(
    project_id: str, execution_id: str, node_id: str, variant: int, prompt: str
) -> StudioAsset:
    root = (
        Path(settings.thikra_data_dir) / "studio" / "generated" / project_id / execution_id
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{node_id}-{variant}.svg"
    safe_prompt = prompt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:120]
    colors = [
        ("#342f79", "#d769ff"),
        ("#0b5e65", "#61e6bb"),
        ("#814421", "#ffb457"),
        ("#283f73", "#75b7ff"),
    ]
    start, end = colors[variant % len(colors)]
    payload = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><defs><linearGradient id="g"><stop stop-color="{start}"/><stop offset="1" stop-color="{end}"/></linearGradient></defs><rect width="1280" height="720" fill="url(#g)"/><circle cx="970" cy="180" r="180" fill="white" opacity=".12"/><text x="72" y="590" fill="white" font-size="44" font-family="Segoe UI">Variant {variant + 1}</text><text x="72" y="650" fill="white" opacity=".8" font-size="22" font-family="Segoe UI">{safe_prompt}</text></svg>'''.encode()
    path.write_bytes(payload)
    return StudioAsset(
        project_id=project_id,
        name=f"{node_id} variant {variant + 1}",
        asset_type="image",
        content_type="image/svg+xml",
        local_path=str(path),
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        source_kind="GENERATED",
        width=1280,
        height=720,
        analysis_status="READY",
    )


def _persist_remote_assets(
    db: Session,
    project_id: str,
    node_id: str,
    assets,
) -> list[dict]:
    return _persist_asset_descriptors(
        db,
        project_id,
        node_id,
        [
            {
                "url": str(asset.url),
                "media_type": str(getattr(asset, "media_type", "application/octet-stream")),
                "size_bytes": int(getattr(asset, "size_bytes", 0) or 0),
                "sha256": str(getattr(asset, "sha256", "") or ""),
            }
            for asset in assets
        ],
    )


def _persist_asset_descriptors(
    db: Session,
    project_id: str,
    node_id: str,
    assets: list[dict],
    *,
    allow_remote_only: bool = False,
) -> list[dict]:
    result: list[dict] = []
    for index, asset in enumerate(assets):
        url = str(asset["url"])
        media_type = str(asset.get("media_type", "application/octet-stream"))
        kind = media_type.split("/", 1)[0]
        try:
            local_path, size, digest = _download_generated_asset(
                project_id, node_id, index, url, media_type
            )
        except httpx.HTTPError:
            if not allow_remote_only:
                raise
            local_path = None
            size = int(asset.get("size_bytes", 0) or 0)
            digest = str(asset.get("sha256") or hashlib.sha256(url.encode()).hexdigest())
        remote_url = None
        from app.studio.storage_connection import studio_backend

        storage = studio_backend()
        if storage is not None and local_path is not None:
            key = f"studio/{project_id}/generated/{digest[:2]}/{digest}{local_path.suffix}"
            storage.put(key, local_path.read_bytes(), content_type=media_type)
            remote_url = storage.get_durable_url(key)
        record = StudioAsset(
            project_id=project_id,
            name=f"{node_id} output {index + 1}",
            asset_type=kind,
            content_type=media_type,
            local_path=str(local_path) if local_path is not None else None,
            remote_url=remote_url or (url if allow_remote_only else None),
            size=size,
            sha256=digest,
            source_kind="GENERATED",
            origin_node_id=node_id,
        )
        db.add(record)
        db.flush()
        result.append(
            {
                "id": record.id,
                "name": record.name,
                "content_type": record.content_type,
                "url": record.remote_url or record.local_path,
            }
        )
    return result


def _download_generated_asset(
    project_id: str, node_id: str, index: int, url: str, media_type: str
) -> tuple[Path, int, str]:
    kind = media_type.split("/", 1)[0]
    limit = MEDIA_LIMITS.get(kind, (500 * 1024 * 1024, set()))[0]
    root = (Path(settings.thikra_data_dir) / "studio" / "generated" / project_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{node_id}-{index}-{uuid.uuid4().hex}.download"
    digest = hashlib.sha256()
    size = 0
    parsed = urlparse(url)
    direct_path = Path(url)
    source_path = Path(parsed.path) if parsed.scheme == "file" else direct_path
    if direct_path.is_file() or parsed.scheme == "file":
        source = source_path.open("rb")
        close_source = True
        response = None
    else:
        response = httpx.stream("GET", url, follow_redirects=True, timeout=120)
        opened = response.__enter__()
        opened.raise_for_status()
        source = opened.iter_bytes(1024 * 1024)
        close_source = False
    try:
        with temporary.open("wb") as target:
            chunks = iter(lambda: source.read(1024 * 1024), b"") if close_source else source
            for chunk in chunks:
                size += len(chunk)
                if size > limit:
                    raise RuntimeError(
                        f"Provider output exceeds the {limit // (1024 * 1024)} MB limit"
                    )
                digest.update(chunk)
                target.write(chunk)
    finally:
        if close_source:
            source.close()
        elif response is not None:
            response.__exit__(None, None, None)
    sha = digest.hexdigest()
    suffix = Path(parsed.path).suffix or mimetypes.guess_extension(media_type) or ".bin"
    destination_dir = root / sha[:2]
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{sha}{suffix.lower()}"
    if destination.exists():
        temporary.unlink(missing_ok=True)
    else:
        temporary.replace(destination)
    return destination, size, sha


def _checkpoint_remote_assets(
    db: Session,
    execution: WorkflowExecution,
    node_id: str,
    assets,
) -> list:
    materialized = list(assets)
    descriptors = [
        {
            "url": str(asset.url),
            "media_type": str(getattr(asset, "media_type", "application/octet-stream")),
            "size_bytes": int(getattr(asset, "size_bytes", 0) or 0),
            "sha256": str(getattr(asset, "sha256", "") or ""),
        }
        for asset in materialized
    ]
    if descriptors:
        _event(
            db,
            execution,
            "node.provider_completed",
            node_id,
            {
                "message": "Provider output checkpointed before local persistence",
                "assets": descriptors,
            },
        )
        db.commit()
    return materialized


def _upstream_assets(source_ids: list[str], node_records: dict[str, NodeExecution]) -> list[dict]:
    assets: list[dict] = []
    for source_id in source_ids:
        if source_id not in node_records:
            continue
        output = _load(node_records[source_id].output_json, {})
        assets.extend(output.get("assets", []))
        if output.get("asset"):
            assets.append(output["asset"])
    return assets


def _ensure_remote_asset(asset: StudioAsset) -> str | None:
    if asset.remote_url:
        return asset.remote_url
    if not asset.local_path or settings.app_mode.upper() == "DEMO":
        return None
    from app.studio.storage_connection import studio_backend

    storage = studio_backend()
    if storage is None:
        raise RuntimeError(
            "Video reference handoff requires Backblaze B2. Connect storage in Studio settings."
        )
    payload = Path(asset.local_path).read_bytes()
    key = f"studio/{asset.project_id}/imports/{asset.id}/{Path(asset.local_path).name}"
    storage.put(key, payload, content_type=asset.content_type)
    asset.remote_url = storage.get_durable_url(key)
    return asset.remote_url


def execute_workflow(execution_id: str) -> None:
    should_resume = False
    with SessionLocal() as db:
        execution = db.get(WorkflowExecution, execution_id)
        if execution is None:
            return
        revision = db.get(WorkflowRevision, execution.revision_id)
        project = db.get(StudioProject, execution.project_id)
        graph = WorkflowGraph.model_validate(_load(revision.graph_json, {}))
        node_records = {
            item.node_id: item
            for item in db.scalars(
                select(NodeExecution).where(NodeExecution.execution_id == execution.id)
            )
        }
        source_records = dict(node_records)
        for node in graph.nodes:
            if node.id in source_records:
                continue
            historical = None
            if execution.resumed_from_execution_id:
                parent = db.get(WorkflowExecution, execution.resumed_from_execution_id)
                for ancestor in _execution_lineage(db, parent):
                    historical = db.scalar(
                        select(NodeExecution).where(
                            NodeExecution.execution_id == ancestor.id,
                            NodeExecution.node_id == node.id,
                            NodeExecution.status.in_({"SUCCEEDED", "CACHED"}),
                        )
                    )
                    if historical is not None:
                        break
            if historical is None:
                historical = db.scalar(
                    select(NodeExecution)
                    .join(
                        WorkflowExecution,
                        WorkflowExecution.id == NodeExecution.execution_id,
                    )
                    .where(
                        WorkflowExecution.project_id == execution.project_id,
                        NodeExecution.node_id == node.id,
                        NodeExecution.status.in_({"SUCCEEDED", "CACHED"}),
                    )
                    .order_by(NodeExecution.created_at.desc())
                )
            if historical:
                source_records[node.id] = historical
        execution.status = "RUNNING"
        execution.started_at = datetime.now(UTC)
        _event(db, execution, "execution.started", None, {"message": "Workflow started"})
        db.commit()
        try:
            upstream_keys = {
                node_id: record.cache_key
                for node_id, record in source_records.items()
                if node_id not in node_records
            }
            for node in topological_nodes(graph):
                db.refresh(execution)
                record = node_records.get(node.id)
                if record is None:
                    continue
                if record.status in {"SUCCEEDED", "CACHED", "FAILED", "BLOCKED"}:
                    if record.status in {"SUCCEEDED", "CACHED"}:
                        upstream_keys[node.id] = record.cache_key
                    continue
                if execution.cancel_requested:
                    record.status = "CANCELLED"
                    continue
                source_ids = sorted(edge.source for edge in graph.edges if edge.target == node.id)
                unavailable = [
                    source_id
                    for source_id in source_ids
                    if source_id not in source_records
                    or source_records[source_id].status in {"FAILED", "BLOCKED", "CANCELLED"}
                ]
                if unavailable:
                    record.status = "BLOCKED"
                    record.error = f"Blocked by unavailable input: {', '.join(unavailable)}"
                    _event(
                        db,
                        execution,
                        "node.blocked",
                        node.id,
                        {"message": record.error},
                    )
                    db.commit()
                    continue
                cache_material = _json(
                    {
                        "type": node.type,
                        "config": node.config,
                        "inputs": [upstream_keys.get(item) for item in source_ids],
                    }
                )
                record.cache_key = hashlib.sha256(cache_material.encode()).hexdigest()
                cached = (
                    None
                    if execution.force_rerun
                    else db.scalar(
                        select(NodeExecution)
                        .where(
                            NodeExecution.cache_key == record.cache_key,
                            NodeExecution.status == "SUCCEEDED",
                        )
                        .order_by(NodeExecution.created_at.desc())
                    )
                )
                if cached and cached.id != record.id:
                    record.status = "CACHED"
                    record.output_json = cached.output_json
                    source_records[node.id] = record
                    upstream_keys[node.id] = cached.cache_key
                    _event(
                        db, execution, "node.cached", node.id, {"message": f"Reused {node.label}"}
                    )
                    db.commit()
                    continue
                record.status = "RUNNING"
                if record.estimated_cost_minor and not record.charged_minor:
                    record.charged_minor = record.estimated_cost_minor
                    project.spent_minor += record.charged_minor
                start_payload = {
                    "message": (
                        f"Running {node.label} · {node.config.get('vendor')} / {node.config.get('model')} · "
                        f"{node.config.get('variants', 1)} variant(s)"
                        if node.type in {"image_generation", "video_generation"}
                        else f"Running {node.label}"
                    ),
                    "provider": node.config.get("vendor"),
                    "model": node.config.get("model"),
                    "variants": node.config.get("variants", 1),
                    "timeout_seconds": 600
                    if node.type == "image_generation"
                    else 1200
                    if node.type == "video_generation"
                    else 600,
                }
                _event(db, execution, "node.started", node.id, start_payload)
                db.commit()
                time.sleep(0.035 if settings.app_mode.upper() == "DEMO" else 0.01)
                output: dict[str, Any] = {"kind": node.type}
                if node.type == "image_generation":
                    variants = int(node.config.get("variants", 1))
                    prompt = str(node.config.get("prompt_guidance") or node.label)
                    if settings.app_mode.upper() == "DEMO":
                        assets = []
                        for index in range(variants):
                            asset = _demo_svg(project.id, execution.id, node.id, index, prompt)
                            db.add(asset)
                            db.flush()
                            assets.append(
                                {
                                    "id": asset.id,
                                    "name": asset.name,
                                    "content_type": asset.content_type,
                                }
                            )
                    else:
                        from app.repo.studio_runtime import generate_images, result_assets

                        vendor = str(node.config.get("vendor") or "replicate")
                        entry = catalog.resolve(catalog.IMAGE, vendor)
                        model = str(node.config.get("model") or entry.default_model)
                        completed_steps = 0

                        def image_progress(
                            event,
                            variant_total=variants,
                            active_node_id=node.id,
                            active_node_label=node.label,
                        ) -> None:
                            nonlocal completed_steps
                            event_type = str(getattr(event, "type", "pipeline.event"))
                            if event_type == "step.completed":
                                completed_steps += 1
                            if event_type in {"step.started", "step.completed", "step.failed"}:
                                progress = completed_steps / max(1, variant_total)
                                _event(
                                    db,
                                    execution,
                                    "node.progress",
                                    active_node_id,
                                    {
                                        "message": f"{active_node_label}: {completed_steps} of {variant_total} variants complete",
                                        "provider_event": event_type,
                                        "step_index": getattr(event, "step_index", None),
                                        "completed": completed_steps,
                                        "total": variant_total,
                                        "progress": progress,
                                    },
                                )
                                db.commit()

                        result = generate_images(
                            entry,
                            model,
                            prompt,
                            variants,
                            effective_provider_secret(vendor),
                            on_event=image_progress,
                        )
                        durable = _checkpoint_remote_assets(
                            db, execution, node.id, result_assets(result)
                        )
                        assets = _persist_remote_assets(db, project.id, node.id, durable)
                    output = {"kind": "variant_set", "assets": assets}
                elif node.type == "asset_selector":
                    source = next(
                        (source_records[item] for item in source_ids if item in source_records),
                        None,
                    )
                    variants = _load(source.output_json, {}).get("assets", []) if source else []
                    index = min(
                        int(node.config.get("selected_index", 0)), max(0, len(variants) - 1)
                    )
                    output = {"kind": "asset", "asset": variants[index] if variants else None}
                elif node.type == "video_generation":
                    if settings.app_mode.upper() == "DEMO":
                        output = {
                            "kind": "variant_set",
                            "assets": [],
                            "demo_url": "/demo/noura-glow.mp4",
                            "simulated": True,
                        }
                    else:
                        from app.repo.studio_runtime import generate_videos, result_assets

                        inputs = _upstream_assets(source_ids, source_records)
                        image = next(
                            (
                                item
                                for item in inputs
                                if str(item.get("content_type", "")).startswith("image/")
                            ),
                            None,
                        )
                        if not image:
                            raise RuntimeError("Video generation requires a selected image asset")
                        image_record = db.get(StudioAsset, str(image.get("id")))
                        image_url = (
                            _ensure_remote_asset(image_record) if image_record else image.get("url")
                        )
                        if not image_url:
                            raise RuntimeError(
                                "Video generation requires a provider-readable image asset"
                            )
                        vendor = str(node.config.get("vendor") or settings.video_provider)
                        entry = catalog.resolve(catalog.VIDEO, vendor)
                        model = str(node.config.get("model") or entry.default_model)
                        result = generate_videos(
                            entry,
                            model,
                            str(
                                node.config.get("prompt_guidance")
                                or "Subtle cinematic subject and camera motion"
                            ),
                            image_url,
                            int(node.config.get("variants", 1)),
                            float(node.config.get("duration_sec", 5)),
                            effective_provider_secret(vendor),
                        )
                        durable = _checkpoint_remote_assets(
                            db, execution, node.id, result_assets(result)
                        )
                        assets = _persist_remote_assets(db, project.id, node.id, durable)
                        output = {"kind": "variant_set", "assets": assets}
                elif node.type in {"narration", "music"}:
                    if settings.app_mode.upper() == "DEMO":
                        output = {"kind": "audio", "simulated": True}
                    else:
                        from app.repo.studio_runtime import generate_audio, result_assets

                        slot = catalog.TTS if node.type == "narration" else catalog.MUSIC
                        default_vendor = "openai" if node.type == "narration" else "replicate"
                        vendor = str(node.config.get("vendor") or default_vendor)
                        entry = catalog.resolve(slot, vendor)
                        result = generate_audio(
                            entry,
                            str(node.config.get("model") or entry.default_model),
                            str(
                                node.config.get("text")
                                or node.config.get("prompt_guidance")
                                or node.label
                            ),
                            duration_sec=float(node.config.get("duration_sec", 5))
                            if node.type == "music"
                            else None,
                            secret=effective_provider_secret(vendor),
                        )
                        durable = _checkpoint_remote_assets(
                            db, execution, node.id, result_assets(result)
                        )
                        assets = _persist_remote_assets(db, project.id, node.id, durable)
                        output = {"kind": "audio", "assets": assets}
                elif node.type == "composition":
                    if settings.app_mode.upper() == "DEMO":
                        output = {
                            "kind": "media",
                            "demo_url": "/demo/noura-glow.mp4",
                            "simulated": True,
                        }
                    else:
                        from app.repo.composer import compose_studio

                        inputs = _upstream_assets(source_ids, source_records)
                        visual = next(
                            (
                                item
                                for item in inputs
                                if str(item.get("content_type", "")).startswith(
                                    ("image/", "video/")
                                )
                            ),
                            None,
                        )
                        audio = [
                            item["url"]
                            for item in inputs
                            if str(item.get("content_type", "")).startswith("audio/")
                            and item.get("url")
                        ]
                        if not visual or not visual.get("url"):
                            raise RuntimeError("Composition requires a selected visual asset")
                        composed = compose_studio(
                            visual["url"],
                            audio,
                            project_id=project.id,
                            execution_id=execution.id,
                            duration_sec=float(node.config.get("duration_sec", 5)),
                        )
                        durable = _checkpoint_remote_assets(db, execution, node.id, [composed])
                        assets = _persist_remote_assets(db, project.id, node.id, durable)
                        output = {"kind": "media", "assets": assets}
                elif node.type == "export":
                    inputs = _upstream_assets(source_ids, source_records)
                    output = {"kind": "delivery", "assets": inputs}
                elif node.type == "reference_asset":
                    asset_id = node.config.get("asset_id")
                    asset = db.get(StudioAsset, str(asset_id)) if asset_id else None
                    output = {
                        "kind": "asset",
                        "asset": {
                            "id": asset.id,
                            "name": asset.name,
                            "content_type": asset.content_type,
                            "url": _ensure_remote_asset(asset),
                        }
                        if asset
                        else None,
                    }
                elif node.type == "verification":
                    output = {
                        "kind": "report",
                        "status": "PASS",
                        "checks": ["graph-valid", "asset-present"],
                    }
                elif node.type == "look_director":
                    output = {
                        "kind": "style",
                        "text": "Cohesive cinematic lighting, deliberate palette, consistent subject proportions",
                    }
                else:
                    output = {"kind": node.type, "value": node.config}
                record.output_json = _json(output)
                record.status = "SUCCEEDED"
                source_records[node.id] = record
                upstream_keys[node.id] = record.cache_key
                _event(
                    db,
                    execution,
                    "node.succeeded",
                    node.id,
                    {"message": f"Completed {node.label}", "output": output},
                )
                db.commit()
            failed_count = int(
                db.scalar(
                    select(func.count())
                    .select_from(NodeExecution)
                    .where(
                        NodeExecution.execution_id == execution.id,
                        NodeExecution.status.in_({"FAILED", "BLOCKED"}),
                    )
                )
                or 0
            )
            if execution.cancel_requested:
                execution.status = "CANCELLED"
                message = "Workflow cancelled"
            elif failed_count:
                execution.status = "FAILED"
                execution.failure_reason = f"{failed_count} node(s) failed or were blocked"
                message = "Independent branches completed; some nodes failed or were blocked"
            else:
                execution.status = "SUCCEEDED"
                message = "Workflow completed"
            execution.finished_at = datetime.now(UTC)
            _event(
                db, execution, f"execution.{execution.status.lower()}", None, {"message": message}
            )
            db.commit()
        except Exception as exc:
            execution_id_value = execution.id
            node_id_value = node.id
            error_message = str(exc)[:2000]
            db.rollback()
            execution = db.get(WorkflowExecution, execution_id_value)
            record = db.scalar(
                select(NodeExecution).where(
                    NodeExecution.execution_id == execution_id_value,
                    NodeExecution.node_id == node_id_value,
                )
            )
            record.status = "FAILED"
            record.error = error_message
            _event(
                db,
                execution,
                "node.failed",
                node_id_value,
                {"message": error_message[:500]},
            )
            db.commit()
            should_resume = True
    if should_resume:
        execute_workflow(execution_id)


def import_asset(
    db: Session, project: StudioProject, filename: str, content_type: str, source
) -> StudioAsset:
    kind = next((name for name, (_, types) in MEDIA_LIMITS.items() if content_type in types), None)
    if kind is None:
        raise ValueError("Unsupported media type")
    limit = MEDIA_LIMITS[kind][0]
    root = (Path(settings.thikra_data_dir) / "studio" / "imports" / project.id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower() or mimetypes.guess_extension(content_type) or ".bin"
    destination = (root / f"{uuid.uuid4()}{suffix}").resolve()
    if root not in destination.parents:
        raise ValueError("Asset path escapes the Studio data directory")
    digest = hashlib.sha256()
    size = 0
    with destination.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                target.close()
                destination.unlink(missing_ok=True)
                raise ValueError(f"{kind} files may not exceed {limit // (1024 * 1024)} MB")
            digest.update(chunk)
            target.write(chunk)
    asset = StudioAsset(
        project_id=project.id,
        name=Path(filename).name[:260],
        asset_type=kind,
        content_type=content_type,
        local_path=str(destination),
        size=size,
        sha256=digest.hexdigest(),
    )
    db.add(asset)
    db.commit()
    return asset


def effective_provider_secret(vendor: str) -> str:
    try:
        import keyring

        personal = keyring.get_password(KEYRING_SERVICE, vendor)
    except Exception:
        personal = None
    setting = VENDOR_SETTING.get(vendor)
    return personal or (str(getattr(settings, setting, "")) if setting else "")


def provider_connection_status() -> list[dict]:
    vendors = sorted(
        {entry.vendor for entries in catalog.CATALOG.values() for entry in entries.values()}
    )
    result = []
    for vendor in vendors:
        try:
            import keyring

            personal = bool(keyring.get_password(KEYRING_SERVICE, vendor))
        except Exception:
            personal = False
        setting = VENDOR_SETTING.get(vendor)
        environment = bool(getattr(settings, setting, "")) if setting else False
        result.append(
            {
                "vendor": vendor,
                "configured": personal or environment,
                "source": "personal" if personal else "environment" if environment else "none",
            }
        )
    return result


def set_provider_secret(vendor: str, secret: str) -> None:
    if vendor not in VENDOR_SETTING:
        raise ValueError("Unknown provider vendor")
    import keyring

    keyring.set_password(KEYRING_SERVICE, vendor, secret)


def clear_provider_secret(vendor: str) -> None:
    if vendor not in VENDOR_SETTING:
        raise ValueError("Unknown provider vendor")
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, vendor)
    except Exception:
        return
