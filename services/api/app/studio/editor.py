"""Immutable sequence editing, media preparation, and resumable local renders."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.studio.models import (
    SequenceAgentProposal,
    SequenceRevision,
    StudioAsset,
    StudioCaptionJob,
    StudioGenerationJob,
    StudioJobEvent,
    StudioProject,
    StudioRender,
    StudioRenderEvent,
    StudioSequence,
)
from app.studio.schemas import (
    SequenceClip,
    SequenceDocument,
    SequencePreset,
    SequenceProposalOperation,
    SequenceProposalOutput,
)
from app.thikra.database import SessionLocal

PRESET_SIZES: dict[str, tuple[int, int]] = {
    "landscape_720": (1280, 720),
    "landscape_1080": (1920, 1080),
    "portrait_720": (720, 1280),
    "portrait_1080": (1080, 1920),
    "square_720": (720, 720),
    "square_1080": (1080, 1080),
}


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def default_document(preset: SequencePreset = "landscape_1080") -> SequenceDocument:
    return SequenceDocument.model_validate(
        {
            "schema_version": 2,
            "preset": preset,
            "tracks": [
                {"id": "v1", "name": "Video 1", "kind": "visual", "order": 0},
                {"id": "titles", "name": "Titles", "kind": "text", "order": 1},
                {"id": "captions", "name": "Captions", "kind": "caption", "order": 2},
                {"id": "a1", "name": "Audio 1", "kind": "audio", "order": 3},
                {"id": "music", "name": "Music", "kind": "audio", "order": 4},
            ],
        }
    )


def upgrade_document(document: SequenceDocument) -> SequenceDocument:
    """Normalize a historical timeline without mutating its stored revision."""
    if document.schema_version == 2:
        return document
    payload = document.model_dump(mode="json")
    payload["schema_version"] = 2
    for clip in payload["clips"]:
        if clip["kind"] in {"text", "caption"} and clip.get("text"):
            clip["transform"]["position_x"] = clip["text"].get("position_x", 0.5)
            clip["transform"]["position_y"] = clip["text"].get("position_y", 0.82)
    return SequenceDocument.model_validate(payload)


def _seed_latest_video(db: Session, project_id: str, document: SequenceDocument) -> None:
    asset = db.scalar(
        select(StudioAsset)
        .where(StudioAsset.project_id == project_id, StudioAsset.asset_type == "video")
        .order_by(StudioAsset.created_at.desc())
    )
    if asset is None:
        return
    duration = min(asset.duration_ms or 5_000, 300_000)
    document.clips.append(
        SequenceClip.model_validate(
            {
                "id": f"seed-{asset.id[:8]}",
                "track_id": "v1",
                "kind": "video",
                "name": asset.name,
                "asset_id": asset.id,
                "start_ms": 0,
                "duration_ms": duration,
            }
        )
    )


def validate_sources(db: Session, project_id: str, document: SequenceDocument) -> None:
    assets = {
        asset.id: asset
        for asset in db.scalars(select(StudioAsset).where(StudioAsset.project_id == project_id))
    }
    by_track: dict[str, list] = {}
    for clip in document.clips:
        if clip.asset_id:
            asset = assets.get(clip.asset_id)
            if asset is None:
                raise ValueError(f"clip {clip.id} references an unavailable project asset")
            expected = {"image": "image", "video": "video", "audio": "audio"}.get(clip.kind)
            if expected and asset.asset_type != expected:
                raise ValueError(f"clip {clip.id} requires a {expected} asset")
            if (
                asset.duration_ms
                and clip.kind in {"video", "audio"}
                and clip.source_in_ms + clip.duration_ms > asset.duration_ms + 34
            ):
                raise ValueError(f"clip {clip.id} exceeds the source duration")
        by_track.setdefault(clip.track_id, []).append(clip)
    for clips in by_track.values():
        ordered = sorted(clips, key=lambda item: (item.start_ms, item.id))
        for left, right in pairwise(ordered):
            overlap = left.start_ms + left.duration_ms - right.start_ms
            allowed = max(left.transition_duration_ms, right.transition_duration_ms)
            if overlap > allowed:
                raise ValueError(
                    f"clips {left.id} and {right.id} overlap without a supported transition"
                )


def serialize_revision(revision: SequenceRevision) -> dict:
    return {
        "id": revision.id,
        "sequence_id": revision.sequence_id,
        "number": revision.number,
        "parent_revision_id": revision.parent_revision_id,
        "schema_version": revision.schema_version,
        "document": json.loads(revision.timeline_json),
        "content_hash": revision.content_hash,
        "summary": revision.summary,
        "source": revision.source,
        "created_at": revision.created_at,
    }


def serialize_sequence(db: Session, sequence: StudioSequence, detail: bool = True) -> dict:
    result = {
        "id": sequence.id,
        "project_id": sequence.project_id,
        "name": sequence.name,
        "current_revision_id": sequence.current_revision_id,
        "current_revision_number": sequence.current_revision_number,
        "view_state": json.loads(sequence.view_state_json),
        "created_at": sequence.created_at,
        "updated_at": sequence.updated_at,
    }
    if detail and sequence.current_revision_id:
        result["revision"] = serialize_revision(
            db.get(SequenceRevision, sequence.current_revision_id)
        )
    return result


def create_sequence(
    db: Session, project: StudioProject, name: str, preset: SequencePreset
) -> StudioSequence:
    sequence = StudioSequence(project_id=project.id, name=name)
    db.add(sequence)
    db.flush()
    document = default_document(preset)
    _seed_latest_video(db, project.id, document)
    create_revision(db, sequence, None, document, "Initial edit", source="SYSTEM")
    db.refresh(sequence)
    return sequence


def create_revision(
    db: Session,
    sequence: StudioSequence,
    base_revision_id: str | None,
    document: SequenceDocument,
    summary: str,
    *,
    source: str = "MANUAL",
) -> SequenceRevision:
    if sequence.current_revision_id != base_revision_id:
        raise RuntimeError("STALE_REVISION")
    document = upgrade_document(document)
    validate_sources(db, sequence.project_id, document)
    payload = document.model_dump(mode="json")
    encoded = _dump(payload)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    current = db.get(SequenceRevision, base_revision_id) if base_revision_id else None
    if current and current.content_hash == digest:
        return current
    revision = SequenceRevision(
        sequence_id=sequence.id,
        parent_revision_id=base_revision_id,
        number=sequence.current_revision_number + 1,
        schema_version=document.schema_version,
        timeline_json=encoded,
        content_hash=digest,
        summary=summary,
        source=source,
    )
    db.add(revision)
    db.flush()
    sequence.current_revision_id = revision.id
    sequence.current_revision_number = revision.number
    db.commit()
    return revision


def delete_sequence(db: Session, sequence: StudioSequence) -> None:
    active = db.scalar(
        select(func.count())
        .select_from(StudioRender)
        .where(
            StudioRender.sequence_id == sequence.id,
            StudioRender.status.in_({"QUEUED", "RUNNING"}),
        )
    )
    if active:
        raise RuntimeError("RENDER_ACTIVE")
    render_ids = select(StudioRender.id).where(StudioRender.sequence_id == sequence.id)
    caption_ids = select(StudioCaptionJob.id).where(StudioCaptionJob.sequence_id == sequence.id)
    generation_ids = select(StudioGenerationJob.id).where(
        StudioGenerationJob.sequence_id == sequence.id
    )
    db.execute(delete(StudioRenderEvent).where(StudioRenderEvent.render_id.in_(render_ids)))
    db.execute(
        delete(StudioJobEvent).where(
            StudioJobEvent.job_kind == "caption", StudioJobEvent.job_id.in_(caption_ids)
        )
    )
    db.execute(
        delete(StudioJobEvent).where(
            StudioJobEvent.job_kind == "generation", StudioJobEvent.job_id.in_(generation_ids)
        )
    )
    db.execute(delete(StudioCaptionJob).where(StudioCaptionJob.sequence_id == sequence.id))
    db.execute(delete(StudioRender).where(StudioRender.sequence_id == sequence.id))
    db.execute(delete(StudioGenerationJob).where(StudioGenerationJob.sequence_id == sequence.id))
    db.execute(
        delete(SequenceAgentProposal).where(SequenceAgentProposal.sequence_id == sequence.id)
    )
    db.execute(delete(SequenceRevision).where(SequenceRevision.sequence_id == sequence.id))
    db.delete(sequence)
    db.commit()


def restore_revision(
    db: Session, sequence: StudioSequence, base_revision_id: str, revision_id: str
) -> SequenceRevision:
    old = db.get(SequenceRevision, revision_id)
    if old is None or old.sequence_id != sequence.id:
        raise ValueError("Revision not found in this sequence")
    document = upgrade_document(SequenceDocument.model_validate(json.loads(old.timeline_json)))
    return create_revision(
        db,
        sequence,
        base_revision_id,
        document,
        f"Restored revision {old.number}",
        source="RESTORE",
    )


def serialize_asset(asset: StudioAsset) -> dict:
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "content_type": asset.content_type,
        "size": asset.size,
        "sha256": asset.sha256,
        "source_kind": asset.source_kind,
        "width": asset.width,
        "height": asset.height,
        "duration_ms": asset.duration_ms,
        "frame_rate": asset.frame_rate,
        "has_audio": asset.has_audio,
        "analysis_status": asset.analysis_status,
        "metadata": json.loads(asset.metadata_json or "{}"),
        "waveform": json.loads(asset.waveform_json or "[]"),
        "content_url": f"/studio/assets/{asset.id}/content",
        "thumbnail_url": f"/studio/assets/{asset.id}/thumbnail" if asset.thumbnail_path else None,
        "proxy_url": f"/studio/assets/{asset.id}/proxy" if asset.proxy_path else None,
        "created_at": asset.created_at,
    }


def prepare_asset(asset_id: str) -> None:
    from app.repo.composer import prepare_studio_asset

    with SessionLocal() as db:
        asset = db.get(StudioAsset, asset_id)
        if asset is None:
            return
        asset.analysis_status = "RUNNING"
        db.commit()
        try:
            cache = (Path(settings.thikra_data_dir) / "studio" / "cache" / asset.sha256).resolve()
            result = prepare_studio_asset(
                asset.local_path or asset.remote_url or "", asset.content_type, cache
            )
            asset.width = result.get("width")
            asset.height = result.get("height")
            asset.duration_ms = result.get("duration_ms")
            asset.frame_rate = result.get("frame_rate")
            asset.has_audio = result.get("has_audio")
            asset.thumbnail_path = result.get("thumbnail_path")
            asset.proxy_path = result.get("proxy_path")
            asset.waveform_json = _dump(result.get("waveform", []))
            asset.metadata_json = _dump(result.get("metadata", {}))
            asset.analysis_status = "READY"
        except Exception as exc:
            asset.analysis_status = "FAILED"
            asset.metadata_json = _dump({"error": str(exc)[:1000]})
        db.commit()


def _render_event(db: Session, render: StudioRender, event_type: str, **payload) -> None:
    number = (
        int(
            db.scalar(
                select(func.coalesce(func.max(StudioRenderEvent.sequence), 0)).where(
                    StudioRenderEvent.render_id == render.id
                )
            )
            or 0
        )
        + 1
    )
    db.add(
        StudioRenderEvent(
            render_id=render.id,
            sequence=number,
            event_type=event_type,
            payload_json=_dump(payload),
        )
    )
    db.flush()


def serialize_render(render: StudioRender) -> dict:
    return {
        "id": render.id,
        "project_id": render.project_id,
        "sequence_id": render.sequence_id,
        "revision_id": render.revision_id,
        "preset": render.preset,
        "status": render.status,
        "progress": render.progress,
        "output_asset_id": render.output_asset_id,
        "srt_asset_id": render.srt_asset_id,
        "cancel_requested": render.cancel_requested,
        "resumed_from_render_id": render.resumed_from_render_id,
        "error": render.error,
        "created_at": render.created_at,
        "finished_at": render.finished_at,
    }


def create_render(
    db: Session,
    sequence: StudioSequence,
    revision: SequenceRevision,
    preset: SequencePreset,
    *,
    resumed_from: str | None = None,
) -> StudioRender:
    document = upgrade_document(SequenceDocument.model_validate(json.loads(revision.timeline_json)))
    validate_sources(db, sequence.project_id, document)
    hashes = sorted(
        asset.sha256
        for asset in db.scalars(
            select(StudioAsset).where(
                StudioAsset.id.in_([clip.asset_id for clip in document.clips if clip.asset_id])
            )
        )
    )
    render_hash = hashlib.sha256(
        _dump({"revision": revision.content_hash, "preset": preset, "inputs": hashes}).encode()
    ).hexdigest()
    cached = db.scalar(
        select(StudioRender).where(
            StudioRender.render_hash == render_hash, StudioRender.status == "SUCCEEDED"
        )
    )
    render = StudioRender(
        project_id=sequence.project_id,
        sequence_id=sequence.id,
        revision_id=revision.id,
        preset=preset,
        render_hash=render_hash,
        resumed_from_render_id=resumed_from,
    )
    if cached:
        render.status = "SUCCEEDED"
        render.progress = 100
        render.output_asset_id = cached.output_asset_id
        render.srt_asset_id = cached.srt_asset_id
        render.finished_at = datetime.now(UTC)
    db.add(render)
    db.flush()
    _render_event(
        db,
        render,
        "render.cached" if cached else "render.queued",
        progress=render.progress,
        message="Reused identical export" if cached else "Export queued",
    )
    db.commit()
    return render


def execute_render(render_id: str) -> None:
    from app.repo.composer import render_studio_sequence

    with SessionLocal() as db:
        render = db.get(StudioRender, render_id)
        if render is None or render.status == "SUCCEEDED":
            return
        revision = db.get(SequenceRevision, render.revision_id)
        document = upgrade_document(
            SequenceDocument.model_validate(json.loads(revision.timeline_json))
        )
        asset_ids = [clip.asset_id for clip in document.clips if clip.asset_id]
        assets = {
            asset.id: {
                "path": asset.local_path,
                "url": asset.remote_url,
                "content_type": asset.content_type,
                "sha256": asset.sha256,
                "has_audio": asset.has_audio,
            }
            for asset in db.scalars(select(StudioAsset).where(StudioAsset.id.in_(asset_ids)))
        }
        render.status = "RUNNING"
        render.started_at = datetime.now(UTC)
        _render_event(db, render, "render.started", progress=1, message="Preparing media")
        db.commit()

        def cancelled() -> bool:
            db.expire(render, ["cancel_requested"])
            return bool(render.cancel_requested)

        def progress(stage: str, percent: int, message: str) -> None:
            render.progress = max(render.progress, percent)
            _render_event(db, render, f"render.{stage}", progress=percent, message=message)
            db.commit()

        try:
            output, srt = render_studio_sequence(
                document.model_dump(mode="json"),
                assets,
                PRESET_SIZES[render.preset],
                project_id=render.project_id,
                render_id=render.id,
                on_progress=progress,
                cancelled=cancelled,
            )
            output_record = StudioAsset(
                project_id=render.project_id,
                name=f"{render.preset} export.mp4",
                asset_type="video",
                content_type="video/mp4",
                local_path=output.url,
                size=output.size_bytes or 0,
                sha256=output.sha256 or render.render_hash,
                source_kind="RENDERED",
                analysis_status="PENDING",
            )
            from app.studio.storage_connection import studio_backend

            storage = studio_backend()
            if storage is not None:
                key = f"studio/{render.project_id}/renders/{render.id}/final.mp4"
                storage.put(key, Path(output.url).read_bytes(), content_type="video/mp4")
                output_record.remote_url = storage.get_durable_url(key)
            db.add(output_record)
            db.flush()
            render.output_asset_id = output_record.id
            if srt:
                srt_record = StudioAsset(
                    project_id=render.project_id,
                    name=f"{render.preset} captions.srt",
                    asset_type="caption",
                    content_type="application/x-subrip",
                    local_path=srt.url,
                    size=srt.size_bytes or 0,
                    sha256=srt.sha256 or "",
                    source_kind="RENDERED",
                    analysis_status="READY",
                )
                if storage is not None:
                    srt_key = f"studio/{render.project_id}/renders/{render.id}/captions.srt"
                    storage.put(
                        srt_key,
                        Path(srt.url).read_bytes(),
                        content_type="application/x-subrip",
                    )
                    srt_record.remote_url = storage.get_durable_url(srt_key)
                db.add(srt_record)
                db.flush()
                render.srt_asset_id = srt_record.id
            render.status = "SUCCEEDED"
            render.progress = 100
            render.finished_at = datetime.now(UTC)
            _render_event(db, render, "render.completed", progress=100, message="Export ready")
            db.commit()
        except InterruptedError:
            render.status = "CANCELLED"
            render.finished_at = datetime.now(UTC)
            _render_event(
                db, render, "render.cancelled", progress=render.progress, message="Export cancelled"
            )
            db.commit()
        except Exception as exc:
            render.status = "FAILED"
            render.error = str(exc)[:2000]
            render.finished_at = datetime.now(UTC)
            _render_event(
                db, render, "render.failed", progress=render.progress, message=render.error
            )
            db.commit()


def interrupt_stale_renders(db: Session) -> int:
    renders = list(
        db.scalars(select(StudioRender).where(StudioRender.status.in_({"QUEUED", "RUNNING"})))
    )
    for render in renders:
        render.status = "FAILED"
        render.error = "Local API restarted before this export completed; retry to resume"
        render.finished_at = datetime.now(UTC)
        _render_event(
            db, render, "render.interrupted", progress=render.progress, message=render.error
        )
    jobs: list[tuple[str, StudioGenerationJob | StudioCaptionJob]] = [
        *(
            ("generation", item)
            for item in db.scalars(
                select(StudioGenerationJob).where(
                    StudioGenerationJob.status.in_({"QUEUED", "RUNNING"})
                )
            )
        ),
        *(
            ("caption", item)
            for item in db.scalars(
                select(StudioCaptionJob).where(StudioCaptionJob.status.in_({"QUEUED", "RUNNING"}))
            )
        ),
    ]
    for kind, job in jobs:
        job.status = "FAILED"
        job.error = (
            "Local API restarted before this job completed; resume from its durable checkpoint"
        )
        _job_event(
            db, kind, job.id, f"{kind}.interrupted", progress=job.progress, message=job.error
        )
    if renders or jobs:
        db.commit()
    return len(renders) + len(jobs)


def generation_estimate(
    db: Session,
    project: StudioProject,
    *,
    kind: str,
    vendor: str,
    model: str,
    prompt: str,
    variants: int,
    duration_ms: int | None,
    reference_asset_id: str | None,
) -> dict:
    from app.repo import provider_catalog as catalog
    from app.studio.service import effective_provider_secret, provider_connection_status

    entry = catalog.resolve(kind, vendor)
    if model not in entry.suggested_models:
        raise ValueError("Select a model published by the configured provider catalog")
    if not effective_provider_secret(vendor):
        raise ValueError("The selected provider is not connected")
    reference = db.get(StudioAsset, reference_asset_id) if reference_asset_id else None
    if reference and (reference.project_id != project.id or reference.asset_type != "image"):
        raise ValueError("Video reference must be an image from this project")
    if kind == "video":
        if not reference and not entry.supports_text_only:
            raise ValueError("This video provider requires a selected image reference")
        if entry.snap_durations and (duration_ms or 0) / 1000 not in entry.snap_durations:
            raise ValueError("Duration is not supported by the selected provider")
    estimated = (18 if kind == "image" else 105) * variants
    config = {
        "project_id": project.id,
        "kind": kind,
        "vendor": vendor,
        "model": model,
        "prompt": prompt,
        "variants": variants,
        "duration_ms": duration_ms,
        "reference_asset_id": reference_asset_id,
        "estimated_cost_minor": estimated,
    }
    return {
        **config,
        "estimate_hash": hashlib.sha256(_dump(config).encode()).hexdigest(),
        "remaining_minor": max(0, project.budget_cap_minor - project.spent_minor),
        "within_budget": estimated <= project.budget_cap_minor - project.spent_minor,
        "credential_source": next(
            (item["source"] for item in provider_connection_status() if item["vendor"] == vendor),
            "none",
        ),
        "estimated": True,
    }


def _job_event(db: Session, kind: str, job_id: str, event_type: str, **payload) -> None:
    number = (
        int(
            db.scalar(
                select(func.coalesce(func.max(StudioJobEvent.sequence), 0)).where(
                    StudioJobEvent.job_kind == kind, StudioJobEvent.job_id == job_id
                )
            )
            or 0
        )
        + 1
    )
    db.add(
        StudioJobEvent(
            job_kind=kind,
            job_id=job_id,
            sequence=number,
            event_type=event_type,
            payload_json=_dump(payload),
        )
    )
    db.flush()


def create_generation_job(
    db: Session,
    project: StudioProject,
    estimate: dict,
    estimate_hash: str,
    sequence_id: str | None,
    *,
    resumed_from_job_id: str | None = None,
) -> StudioGenerationJob:
    if estimate["estimate_hash"] != estimate_hash:
        raise RuntimeError("STALE_ESTIMATE")
    if not estimate["within_budget"]:
        raise RuntimeError("BUDGET_EXCEEDED")
    if sequence_id:
        sequence = db.get(StudioSequence, sequence_id)
        if sequence is None or sequence.project_id != project.id:
            raise ValueError("Sequence not found in this project")
    job = StudioGenerationJob(
        project_id=project.id,
        sequence_id=sequence_id,
        kind=estimate["kind"],
        vendor=estimate["vendor"],
        model=estimate["model"],
        prompt=estimate["prompt"],
        reference_asset_id=estimate["reference_asset_id"],
        variants=estimate["variants"],
        duration_ms=estimate["duration_ms"],
        estimate_hash=estimate_hash,
        estimated_cost_minor=estimate["estimated_cost_minor"],
        resumed_from_job_id=resumed_from_job_id,
    )
    db.add(job)
    db.flush()
    _job_event(
        db, "generation", job.id, "generation.queued", progress=0, message="Generation queued"
    )
    db.commit()
    return job


def serialize_generation_job(job: StudioGenerationJob) -> dict:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "sequence_id": job.sequence_id,
        "kind": job.kind,
        "vendor": job.vendor,
        "model": job.model,
        "prompt": job.prompt,
        "variants": job.variants,
        "duration_ms": job.duration_ms,
        "estimated_cost_minor": job.estimated_cost_minor,
        "status": job.status,
        "progress": job.progress,
        "result_asset_ids": json.loads(job.result_asset_ids_json),
        "cancel_requested": job.cancel_requested,
        "resumed_from_job_id": job.resumed_from_job_id,
        "error": job.error,
    }


def generation_resume_estimate(db: Session, job: StudioGenerationJob) -> dict:
    if job.status not in {"FAILED", "CANCELLED"}:
        raise RuntimeError("GENERATION_NOT_RESUMABLE")
    checkpoint = json.loads(job.checkpoint_json)
    amount = 0 if checkpoint else job.estimated_cost_minor
    project = db.get(StudioProject, job.project_id)
    config = {
        "resume_from_job_id": job.id,
        "project_id": job.project_id,
        "kind": job.kind,
        "vendor": job.vendor,
        "model": job.model,
        "prompt": job.prompt,
        "variants": job.variants,
        "duration_ms": job.duration_ms,
        "reference_asset_id": job.reference_asset_id,
        "estimated_cost_minor": amount,
        "checkpoint_asset_ids": checkpoint,
    }
    return {
        **config,
        "estimate_hash": hashlib.sha256(_dump(config).encode()).hexdigest(),
        "remaining_minor": max(0, project.budget_cap_minor - project.spent_minor),
        "within_budget": amount <= project.budget_cap_minor - project.spent_minor,
        "estimated": True,
    }


def create_resume_generation_job(
    db: Session, original: StudioGenerationJob, estimate: dict, estimate_hash: str
) -> StudioGenerationJob:
    if estimate["estimate_hash"] != estimate_hash:
        raise RuntimeError("STALE_ESTIMATE")
    project = db.get(StudioProject, original.project_id)
    checkpoint = estimate["checkpoint_asset_ids"]
    if checkpoint:
        job = StudioGenerationJob(
            project_id=original.project_id,
            sequence_id=original.sequence_id,
            kind=original.kind,
            vendor=original.vendor,
            model=original.model,
            prompt=original.prompt,
            reference_asset_id=original.reference_asset_id,
            variants=original.variants,
            duration_ms=original.duration_ms,
            estimate_hash=estimate_hash,
            estimated_cost_minor=0,
            status="SUCCEEDED",
            progress=100,
            result_asset_ids_json=_dump(checkpoint),
            checkpoint_json=_dump(checkpoint),
            resumed_from_job_id=original.id,
        )
        db.add(job)
        db.flush()
        _job_event(
            db,
            "generation",
            job.id,
            "generation.recovered",
            progress=100,
            message="Recovered durable provider outputs without another charge",
            asset_ids=checkpoint,
        )
        db.commit()
        return job
    return create_generation_job(
        db,
        project,
        {**estimate, "within_budget": estimate["within_budget"]},
        estimate_hash,
        original.sequence_id,
        resumed_from_job_id=original.id,
    )


def execute_generation_job(job_id: str) -> None:
    from app.repo import provider_catalog as catalog
    from app.repo.studio_runtime import generate_images, generate_videos, result_assets
    from app.studio import service

    with SessionLocal() as db:
        job = db.get(StudioGenerationJob, job_id)
        if job is None:
            return
        job.status = "RUNNING"
        job.progress = 3
        _job_event(
            db,
            "generation",
            job.id,
            "generation.started",
            progress=3,
            message=f"Starting {job.vendor} / {job.model}",
        )
        project = db.get(StudioProject, job.project_id)
        project.spent_minor += job.estimated_cost_minor
        db.commit()
        try:
            if settings.app_mode.upper() == "DEMO":
                if job.kind == "image":
                    records = [
                        service._demo_svg(job.project_id, job.id, "editor", index, job.prompt)
                        for index in range(job.variants)
                    ]
                else:
                    from app.repo.composer import create_demo_editor_clip

                    root = (
                        Path(settings.thikra_data_dir)
                        / "studio"
                        / "generated"
                        / job.project_id
                        / job.id
                    ).resolve()
                    root.mkdir(parents=True, exist_ok=True)
                    records = []
                    for index in range(job.variants):
                        path = create_demo_editor_clip(
                            root / f"video-{index}.mp4", (job.duration_ms or 4000) / 1000
                        )
                        payload = path.read_bytes()
                        records.append(
                            StudioAsset(
                                project_id=job.project_id,
                                name=f"Editor video {index + 1}",
                                asset_type="video",
                                content_type="video/mp4",
                                local_path=str(path),
                                size=len(payload),
                                sha256=hashlib.sha256(payload).hexdigest(),
                                source_kind="GENERATED",
                                duration_ms=job.duration_ms or 4000,
                                width=1280,
                                height=720,
                                has_audio=False,
                                analysis_status="PENDING",
                            )
                        )
                for record in records:
                    record.origin_execution_id = job.id
                    db.add(record)
                db.flush()
            else:
                entry = catalog.resolve(job.kind, job.vendor)
                secret = service.effective_provider_secret(job.vendor)
                if job.kind == "image":
                    result = generate_images(entry, job.model, job.prompt, job.variants, secret)
                else:
                    reference = (
                        db.get(StudioAsset, job.reference_asset_id)
                        if job.reference_asset_id
                        else None
                    )
                    reference_url = service._ensure_remote_asset(reference) if reference else None
                    result = generate_videos(
                        entry,
                        job.model,
                        job.prompt,
                        reference_url,
                        job.variants,
                        (job.duration_ms or 5000) / 1000,
                        secret,
                    )
                descriptors = service._persist_remote_assets(
                    db, job.project_id, "editor-generation", result_assets(result)
                )
                records = [db.get(StudioAsset, item["id"]) for item in descriptors]
                for record in records:
                    record.origin_execution_id = job.id
            ids = [record.id for record in records]
            job.checkpoint_json = _dump(ids)
            _job_event(
                db,
                "generation",
                job.id,
                "generation.checkpointed",
                progress=92,
                message="Provider outputs saved",
                asset_ids=ids,
            )
            if job.cancel_requested:
                job.status = "CANCELLED"
            else:
                job.status = "SUCCEEDED"
                job.progress = 100
                job.result_asset_ids_json = _dump(ids)
            db.commit()
            for asset_id in ids:
                prepare_asset(asset_id)
        except Exception as exc:
            job.status = "FAILED"
            job.error = str(exc)[:2000]
            _job_event(
                db,
                "generation",
                job.id,
                "generation.failed",
                progress=job.progress,
                message=job.error,
            )
            db.commit()


def caption_estimate(
    db: Session,
    project: StudioProject,
    sequence: StudioSequence,
    revision_id: str,
    language: str | None,
) -> dict:
    if settings.app_mode.upper() != "DEMO":
        from app.studio.service import effective_provider_secret

        if not effective_provider_secret("openai"):
            raise ValueError("OpenAI transcription is not connected in Settings")
    revision = db.get(SequenceRevision, revision_id)
    if revision is None or revision.sequence_id != sequence.id:
        raise ValueError("Sequence revision not found")
    document = upgrade_document(SequenceDocument.model_validate(json.loads(revision.timeline_json)))
    duration_ms = max((clip.start_ms + clip.duration_ms for clip in document.clips), default=0)
    estimated = max(1, (duration_ms + 59_999) // 60_000)
    config = {
        "project_id": project.id,
        "sequence_id": sequence.id,
        "revision_id": revision.id,
        "language": language,
        "model": "whisper-1",
        "duration_ms": duration_ms,
        "estimated_cost_minor": estimated,
    }
    return {
        **config,
        "estimate_hash": hashlib.sha256(_dump(config).encode()).hexdigest(),
        "remaining_minor": max(0, project.budget_cap_minor - project.spent_minor),
        "within_budget": estimated <= project.budget_cap_minor - project.spent_minor,
        "estimated": True,
    }


def create_caption_job(
    db: Session, project: StudioProject, estimate: dict, estimate_hash: str
) -> StudioCaptionJob:
    if estimate["estimate_hash"] != estimate_hash:
        raise RuntimeError("STALE_ESTIMATE")
    if not estimate["within_budget"]:
        raise RuntimeError("BUDGET_EXCEEDED")
    job = StudioCaptionJob(
        project_id=project.id,
        sequence_id=estimate["sequence_id"],
        revision_id=estimate["revision_id"],
        language=estimate["language"],
        model=estimate["model"],
        estimate_hash=estimate_hash,
        estimated_cost_minor=estimate["estimated_cost_minor"],
    )
    db.add(job)
    db.flush()
    _job_event(
        db, "caption", job.id, "caption.queued", progress=0, message="Caption transcription queued"
    )
    db.commit()
    return job


def serialize_caption_job(job: StudioCaptionJob) -> dict:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "sequence_id": job.sequence_id,
        "revision_id": job.revision_id,
        "model": job.model,
        "language": job.language,
        "estimated_cost_minor": job.estimated_cost_minor,
        "status": job.status,
        "progress": job.progress,
        "cues": json.loads(job.cues_json),
        "cancel_requested": job.cancel_requested,
        "error": job.error,
    }


def execute_caption_job(job_id: str) -> None:
    from app.repo import provider_catalog as catalog
    from app.repo.composer import extract_studio_sequence_audio
    from app.studio.service import effective_provider_secret

    with SessionLocal() as db:
        job = db.get(StudioCaptionJob, job_id)
        if job is None:
            return
        job.status = "RUNNING"
        job.progress = 5
        project = db.get(StudioProject, job.project_id)
        project.spent_minor += job.estimated_cost_minor
        _job_event(
            db,
            "caption",
            job.id,
            "caption.started",
            progress=5,
            message="Preparing the audible mix",
        )
        db.commit()
        try:
            revision = db.get(SequenceRevision, job.revision_id)
            document = upgrade_document(
                SequenceDocument.model_validate(json.loads(revision.timeline_json))
            )
            if settings.app_mode.upper() == "DEMO":
                duration = max(
                    (clip.start_ms + clip.duration_ms for clip in document.clips), default=4000
                )
                cue_length = min(3000, duration)
                cues = [
                    {
                        "id": "cue-1",
                        "start_ms": 0,
                        "end_ms": cue_length,
                        "text": "A deterministic demo caption — edit me before applying.",
                    }
                ]
            else:
                asset_ids = [clip.asset_id for clip in document.clips if clip.asset_id]
                assets = {
                    asset.id: {
                        "path": asset.local_path,
                        "url": asset.remote_url,
                        "has_audio": asset.has_audio,
                        "content_type": asset.content_type,
                    }
                    for asset in db.scalars(
                        select(StudioAsset).where(StudioAsset.id.in_(asset_ids))
                    )
                }
                with __import__("tempfile").TemporaryDirectory(prefix="thikra-caption-") as tmp:
                    mix = extract_studio_sequence_audio(
                        document.model_dump(mode="json"), assets, Path(tmp) / "mix.wav"
                    )
                    cues = catalog.transcribe_audio(
                        mix, effective_provider_secret("openai"), job.language
                    )
            if job.cancel_requested:
                job.status = "CANCELLED"
            else:
                job.cues_json = _dump(cues)
                job.status = "SUCCEEDED"
                job.progress = 100
                _job_event(
                    db,
                    "caption",
                    job.id,
                    "caption.completed",
                    progress=100,
                    message=f"{len(cues)} editable cues ready",
                )
            db.commit()
        except Exception as exc:
            job.status = "FAILED"
            job.error = str(exc)[:2000]
            _job_event(
                db, "caption", job.id, "caption.failed", progress=job.progress, message=job.error
            )
            db.commit()


def apply_caption_cues(
    db: Session,
    sequence: StudioSequence,
    base_revision_id: str,
    cues: list[dict],
) -> SequenceRevision:
    if sequence.current_revision_id != base_revision_id:
        raise RuntimeError("STALE_REVISION")
    revision = db.get(SequenceRevision, base_revision_id)
    document = upgrade_document(SequenceDocument.model_validate(json.loads(revision.timeline_json)))
    caption_track = next((track for track in document.tracks if track.kind == "caption"), None)
    if caption_track is None:
        raise ValueError("Sequence has no caption track")
    document.clips = [clip for clip in document.clips if clip.kind != "caption"]
    for index, cue in enumerate(cues):
        start = int(cue.get("start_ms", -1))
        end = int(cue.get("end_ms", -1))
        text = str(cue.get("text", "")).strip()
        if start < 0 or end <= start or end > 300_000 or not text:
            raise ValueError(f"Caption cue {index + 1} has invalid timing or text")
        document.clips.append(
            SequenceClip.model_validate(
                {
                    "id": str(cue.get("id") or f"cue-{index + 1}").replace(" ", "-")[:120],
                    "track_id": caption_track.id,
                    "kind": "caption",
                    "name": f"Caption {index + 1}",
                    "start_ms": start,
                    "duration_ms": end - start,
                    "transform": {"position_x": 0.5, "position_y": 0.86},
                    "text": {
                        "content": text,
                        "font_family": "Noto Sans Arabic",
                        "font_size": 48,
                        "font_weight": 700,
                        "background": "#00000099",
                        "position_y": 0.86,
                    },
                }
            )
        )
    document.captions_stale = False
    return create_revision(
        db, sequence, base_revision_id, document, "Apply automatic captions", source="CAPTION"
    )


def create_sequence_proposal(
    db: Session,
    sequence: StudioSequence,
    base_revision_id: str,
    prompt: str,
    selected_clip_ids: list[str],
) -> SequenceAgentProposal:
    if sequence.current_revision_id != base_revision_id:
        raise RuntimeError("STALE_REVISION")
    revision = db.get(SequenceRevision, base_revision_id)
    document = upgrade_document(SequenceDocument.model_validate(json.loads(revision.timeline_json)))
    known = {clip.id for clip in document.clips}
    if set(selected_clip_ids) - known:
        raise ValueError("Proposal selection contains unknown clips")
    if settings.app_mode.upper() == "DEMO":
        if selected_clip_ids:
            target = next(clip for clip in document.clips if clip.id == selected_clip_ids[0])
            output = SequenceProposalOutput(
                rationale="Tighten the selected edit while preserving the original source and timing history.",
                operations=[
                    SequenceProposalOperation(
                        id="op-polish",
                        type="update_clip",
                        clip_id=target.id,
                        patch={
                            "transition_in": "dissolve",
                            "transition_duration_ms": min(500, target.duration_ms // 2),
                        },
                        summary="Add a restrained dissolve to the selected clip",
                    )
                ],
            )
        else:
            title_track = next(track for track in document.tracks if track.kind == "text")
            output = SequenceProposalOutput(
                rationale="Introduce one concise opening title as a reversible editorial suggestion.",
                operations=[
                    SequenceProposalOperation(
                        id="op-title",
                        type="add_clip",
                        clip=SequenceClip.model_validate(
                            {
                                "id": f"agent-title-{sequence.current_revision_number + 1}",
                                "track_id": title_track.id,
                                "kind": "text",
                                "name": "Agent title",
                                "start_ms": 0,
                                "duration_ms": 2000,
                                "text": {
                                    "content": prompt[:80],
                                    "font_family": "Noto Sans Arabic",
                                    "font_size": 72,
                                    "font_weight": 700,
                                    "position_y": 0.5,
                                },
                            }
                        ),
                        summary="Add a two-second opening title",
                    )
                ],
            )
    else:
        from app.repo.pipelines import generate_sequence_proposal
        from app.studio.service import effective_provider_secret

        output = generate_sequence_proposal(
            prompt, document, selected_clip_ids, api_key=effective_provider_secret("openai")
        )
    proposal = SequenceAgentProposal(
        sequence_id=sequence.id,
        base_revision_id=base_revision_id,
        prompt=prompt,
        operations_json=_dump([item.model_dump(mode="json") for item in output.operations]),
        rationale=output.rationale,
    )
    db.add(proposal)
    db.commit()
    return proposal


def serialize_sequence_proposal(proposal: SequenceAgentProposal) -> dict:
    return {
        "id": proposal.id,
        "sequence_id": proposal.sequence_id,
        "base_revision_id": proposal.base_revision_id,
        "prompt": proposal.prompt,
        "operations": json.loads(proposal.operations_json),
        "rationale": proposal.rationale,
        "status": proposal.status,
        "created_at": proposal.created_at,
    }


def apply_sequence_proposal(
    db: Session,
    sequence: StudioSequence,
    proposal: SequenceAgentProposal,
    base_revision_id: str,
    operation_ids: list[str],
) -> SequenceRevision:
    if (
        sequence.current_revision_id != base_revision_id
        or proposal.base_revision_id != base_revision_id
    ):
        raise RuntimeError("STALE_REVISION")
    operations = [
        SequenceProposalOperation.model_validate(item)
        for item in json.loads(proposal.operations_json)
    ]
    by_id = {item.id: item for item in operations}
    selected = set(operation_ids)
    pending = list(selected)
    while pending:
        operation = by_id.get(pending.pop())
        if operation is None:
            raise ValueError("Unknown proposal operation")
        for dependency in operation.depends_on:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    revision = db.get(SequenceRevision, base_revision_id)
    payload = json.loads(revision.timeline_json)
    for operation in operations:
        if operation.id not in selected:
            continue
        if operation.type == "add_track" and operation.track:
            payload["tracks"].append(operation.track.model_dump(mode="json"))
        elif operation.type == "update_track" and operation.track_id:
            payload["tracks"] = [
                {**item, **operation.patch} if item["id"] == operation.track_id else item
                for item in payload["tracks"]
            ]
        elif operation.type == "remove_track" and operation.track_id:
            if any(item["track_id"] == operation.track_id for item in payload["clips"]):
                raise ValueError("Remove dependent clips before removing their track")
            payload["tracks"] = [
                item for item in payload["tracks"] if item["id"] != operation.track_id
            ]
        elif operation.type in {"add_clip", "add_caption"} and operation.clip:
            payload["clips"].append(operation.clip.model_dump(mode="json"))
        elif operation.type in {"update_clip", "update_caption", "move_clip"} and operation.clip_id:
            payload["clips"] = [
                {**item, **operation.patch} if item["id"] == operation.clip_id else item
                for item in payload["clips"]
            ]
        elif operation.type in {"remove_clip", "remove_caption"} and operation.clip_id:
            payload["clips"] = [
                item for item in payload["clips"] if item["id"] != operation.clip_id
            ]
    document = upgrade_document(SequenceDocument.model_validate(payload))
    revision = create_revision(
        db,
        sequence,
        base_revision_id,
        document,
        f"Agent edit: {proposal.prompt[:420]}",
        source="AGENT",
    )
    proposal.status = "APPLIED"
    db.commit()
    return revision
