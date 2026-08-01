"""Connect accountable Thikra runs to the existing Genblaze media path."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.repo import provider_catalog as catalog
from app.repo.composer import compose_final
from app.repo.pipelines import (
    backend,
    build_keyframe_pipeline,
    build_media_pipeline,
    build_reference_pipeline,
    generate_storyboard,
    sink,
    snap_scene_durations,
)
from app.thikra.audit import append_event, canonical_json
from app.thikra.database import SessionLocal
from app.thikra.models import Asset as DBAsset
from app.thikra.models import Evaluation, EvaluationResult, GenerationRun, MandateVersion, Scene
from app.thikra.verification import inspect_file


async def _pipeline_result(pipeline, *, timeout: int, best_effort: bool = False):
    result = None
    async for event in pipeline.astream(
        sink=sink(),
        timeout=timeout,
        fail_fast=not best_effort,
        raise_on_failure=not best_effort,
    ):
        candidate = getattr(event, "result", None)
        if candidate is not None:
            result = candidate
    if result is None:
        raise RuntimeError("Genblaze pipeline completed without a result")
    return result


def _record(run_id: str, event_type: str, message: str, related: list[str] | None = None) -> None:
    with SessionLocal() as db:
        run = db.get(GenerationRun, run_id)
        if run is None:
            return
        append_event(
            db,
            workspace_id=run.workspace_id,
            run_id=run.id,
            event_type=event_type,
            actor_type="AGENT",
            actor_id="genblaze-orchestrator",
            payload={"message": message, "simulated": False},
            related_object_ids=related or [run.id],
        )
        db.commit()


def _persist_delivery(
    run_id: str, spec, b1_result, b2_result, final_asset, notices: list[str]
) -> None:
    with SessionLocal() as db:
        run = db.get(GenerationRun, run_id)
        if run is None:
            return
        scenes = list(
            db.scalars(select(Scene).where(Scene.run_id == run.id).order_by(Scene.position))
        )

        def add_asset(
            scene: Scene | None, asset, asset_type: str, provider: str, model: str
        ) -> DBAsset:
            object_key = backend().key_from_url(asset.url) or asset.url
            record = DBAsset(
                run_id=run.id,
                scene_id=scene.id if scene else None,
                asset_type=asset_type,
                provider=provider,
                model=model,
                object_key=object_key,
                content_type=asset.media_type,
                size=asset.size_bytes or 0,
                sha256=asset.sha256 or "0" * 64,
                manifest_object_key=None,
                payment_record_id=run.payment_record_id,
                approval_state="PENDING",
                cost_minor=0,
            )
            db.add(record)
            db.flush()
            return record

        selection = json.loads(run.provider_selection_json)
        for index, scene in enumerate(scenes):
            scene.status = "COMPLETED"
            scene.verification_state = "PENDING"
            if index < len(b1_result.run.steps):
                assets = b1_result.run.steps[index].assets or []
                if assets:
                    choice = selection["image"]
                    add_asset(scene, assets[0], "image", choice["vendor"], choice["model"])
            video_index, voice_index = index * 2, index * 2 + 1
            if video_index < len(b2_result.run.steps):
                assets = b2_result.run.steps[video_index].assets or []
                if assets:
                    choice = selection["video"]
                    add_asset(scene, assets[0], "video", choice["vendor"], choice["model"])
            if voice_index < len(b2_result.run.steps):
                assets = b2_result.run.steps[voice_index].assets or []
                if assets:
                    choice = selection["tts"]
                    add_asset(scene, assets[0], "narration", choice["vendor"], choice["model"])

        final_record = add_asset(None, final_asset, "final", "ffmpeg", "composition")
        evaluation = Evaluation(
            run_id=run.id, asset_id=final_record.id, layer="DETERMINISTIC", status="REVIEW_REQUIRED"
        )
        db.add(evaluation)
        db.flush()
        key = backend().key_from_url(final_asset.url)
        file_checks: list[dict] = []
        if key:
            with tempfile.TemporaryDirectory(prefix="thikra-verify-") as folder:
                target = Path(folder) / "final.mp4"
                target.write_bytes(backend().get(key))
                mandate = db.scalar(
                    select(MandateVersion).where(
                        MandateVersion.mandate_id == run.mandate_id,
                        MandateVersion.version == run.mandate_version,
                    )
                )
                policy = json.loads(mandate.schema_json)
                ratio = tuple(int(part) for part in policy["required_aspect_ratio"].split(":"))
                resolution = tuple(
                    int(part) for part in policy["required_resolution"].lower().split("x")
                )
                file_checks = inspect_file(
                    target,
                    expected_resolution=resolution,
                    expected_aspect_ratio=ratio,
                    require_audio=True,
                )
        for check in file_checks:
            db.add(
                EvaluationResult(
                    evaluation_id=evaluation.id,
                    check_name=check["check_name"],
                    status=check["status"],
                    explanation=check["explanation"],
                    evidence_json=canonical_json(check["evidence"]),
                    confidence_basis="Pillow/ffprobe deterministic inspection",
                )
            )
        for notice in notices:
            db.add(
                EvaluationResult(
                    evaluation_id=evaluation.id,
                    check_name="Composition degradation",
                    status="WARNING",
                    explanation=notice,
                    evidence_json="{}",
                    confidence_basis="Genblaze/composer observable output",
                )
            )
        db.add(
            EvaluationResult(
                evaluation_id=evaluation.id,
                check_name="Final human approval",
                status="REVIEW_REQUIRED",
                explanation="The confirmed mandate requires principal approval before acceptance.",
                evidence_json="{}",
                confidence_basis="mandate policy",
            )
        )
        run.status = "HUMAN_REVIEW"
        run.current_stage = "Human review requested"
        run.human_escalation = True
        append_event(
            db,
            workspace_id=run.workspace_id,
            run_id=run.id,
            event_type="evaluation.completed",
            actor_type="AGENT",
            actor_id="verification-engine",
            payload={"deterministic_checks": len(file_checks), "degradation_notices": len(notices)},
            related_object_ids=[run.id, final_record.id, evaluation.id],
        )
        db.commit()


async def execute_generation_run(run_id: str) -> None:
    """Run the preserved B0/B1/B2/composition path and persist accountability context."""
    try:
        with SessionLocal() as db:
            run = db.get(GenerationRun, run_id)
            if run is None:
                return
            scenes = list(
                db.scalars(select(Scene).where(Scene.run_id == run.id).order_by(Scene.position))
            )
            selection = json.loads(run.provider_selection_json)
            prompt = "\n".join(scene.prompt for scene in scenes)

        chat_choice = selection["chat"]
        image_choice = selection["image"]
        video_choice = selection["video"]
        tts_choice = selection["tts"]
        music_choice = selection["music"]
        image_entry = catalog.resolve(catalog.IMAGE, image_choice["vendor"])
        video_entry = catalog.resolve(catalog.VIDEO, video_choice["vendor"])
        tts_entry = catalog.resolve(catalog.TTS, tts_choice["vendor"])
        music_entry = catalog.resolve(catalog.MUSIC, music_choice["vendor"])

        _record(
            run_id,
            "storyboard.generation.started",
            "OpenAI structured storyboard generation started",
        )
        spec, _ = await asyncio.to_thread(generate_storyboard, prompt, chat_choice["model"])
        spec = spec.model_copy(
            update={
                "scenes": [
                    scene.model_copy(
                        update={"image_prompt": planned.prompt, "narration": planned.narration}
                    )
                    for scene, planned in zip(spec.scenes, scenes, strict=False)
                ]
            }
        )
        spec = spec.model_copy(
            update={"total_duration_sec": sum(scene.duration_sec for scene in spec.scenes)}
        )
        spec = snap_scene_durations(spec, video_entry)
        _record(
            run_id,
            "storyboard.generated",
            "Confirmed scene prompts compiled into a Genblaze storyboard",
        )

        _record(run_id, "generation.keyframes.started", "Reference and keyframe pipelines started")
        b0_result = await _pipeline_result(
            build_reference_pipeline(spec, image_entry, image_choice["model"]), timeout=240
        )
        b1_result = await _pipeline_result(
            build_keyframe_pipeline(spec, image_entry, image_choice["model"], b0_result),
            timeout=600,
        )
        _record(
            run_id, "generation.keyframes.completed", "Keyframes stored by the Genblaze B2 sink"
        )

        _record(
            run_id, "generation.media.started", "Video, narration, and music generation started"
        )
        b2_result = await _pipeline_result(
            build_media_pipeline(
                spec,
                b1_result,
                video_entry=video_entry,
                video_model=video_choice["model"],
                tts_entry=tts_entry,
                tts_model=tts_choice["model"],
                music_entry=music_entry,
                music_model=music_choice["model"],
            ),
            timeout=settings.max_run_duration_sec,
            best_effort=True,
        )
        _record(run_id, "composition.started", "ffmpeg composition started")
        final_asset, notices = await asyncio.to_thread(compose_final, b2_result, b1_result, spec)
        await asyncio.to_thread(
            _persist_delivery, run_id, spec, b1_result, b2_result, final_asset, notices
        )
        _record(run_id, "human_review.requested", "Stored delivery is ready for principal review")
    except Exception as exc:
        with SessionLocal() as db:
            run = db.get(GenerationRun, run_id)
            if run is None:
                return
            run.status = "FAILED"
            run.current_stage = "Generation failed"
            run.failure_reason = str(exc)[:2000]
            append_event(
                db,
                workspace_id=run.workspace_id,
                run_id=run.id,
                event_type="generation.failed",
                actor_type="SYSTEM",
                actor_id="thikra-orchestrator",
                payload={"message": str(exc)[:500]},
                related_object_ids=[run.id],
            )
            db.commit()
