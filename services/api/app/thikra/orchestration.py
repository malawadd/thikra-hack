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
from app.thikra.service import PENDING_STORYBOARD_NARRATION, mandate_schema
from app.thikra.verification import inspect_file


async def _pipeline_result(
    pipeline, *, timeout: int, best_effort: bool = False
) -> tuple[object, list[dict[str, object]]]:
    """Return a pipeline result and a sanitized record of failed steps.

    B2 deliberately keeps video, narration, and music best-effort so one
    provider outage does not abort sibling work.  That must not make a failed
    *required* video invisible to the commercial workflow, however.  Keep the
    stream's failure evidence so the caller can make that distinction.
    """
    result = None
    failures: list[dict[str, object]] = []
    async for event in pipeline.astream(
        sink=sink(),
        timeout=timeout,
        fail_fast=not best_effort,
        raise_on_failure=not best_effort,
    ):
        if getattr(event, "type", None) == "step.failed":
            failures.append(
                {
                    "step_index": getattr(event, "step_index", None),
                    "provider": getattr(event, "provider", None),
                    "model": getattr(event, "model", None),
                    "error": str(getattr(event, "error", None) or "Provider step failed")[:500],
                }
            )
        candidate = getattr(event, "result", None)
        if candidate is not None:
            result = candidate
    if result is None:
        raise RuntimeError("Genblaze pipeline completed without a result")
    return result, failures


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


def _merge_storyboard_with_planned_scenes(spec, planned_scenes) -> object:
    """Keep visual constraints while preserving real spoken copy.

    ``Scene.prompt`` is visual-generation input. Its narration placeholder is
    replaced by the storyboard writer's concise voiceover, while an explicit
    human narration edit remains authoritative.
    """
    return spec.model_copy(
        update={
            "scenes": [
                scene.model_copy(
                    update={
                        "image_prompt": planned.prompt,
                        "narration": (
                            scene.narration
                            if planned.narration == PENDING_STORYBOARD_NARRATION
                            else planned.narration
                        ),
                    }
                )
                for scene, planned in zip(spec.scenes, planned_scenes, strict=False)
            ]
        }
    )


def _persist_storyboard_narration(run_id: str, spec) -> None:
    """Store the actual TTS script as accountable scene evidence."""
    with SessionLocal() as db:
        scenes = list(
            db.scalars(select(Scene).where(Scene.run_id == run_id).order_by(Scene.position))
        )
        for planned, generated in zip(scenes, spec.scenes, strict=False):
            planned.narration = generated.narration
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
        video_assets = [
            asset
            for step in b2_result.run.steps
            for asset in (step.assets or [])
            if (asset.media_type or "").lower().startswith("video/")
        ]
        narration_assets = [
            asset
            for step in b2_result.run.steps
            for asset in (step.assets or [])
            if (asset.media_type or "").lower().startswith("audio/")
        ][: len(scenes)]
        for index, scene in enumerate(scenes):
            scene.status = "COMPLETED"
            scene.verification_state = "PENDING"
            generated_video = False
            if index < len(b1_result.run.steps):
                assets = b1_result.run.steps[index].assets or []
                if assets:
                    choice = selection["image"]
                    add_asset(scene, assets[0], "image", choice["vendor"], choice["model"])
            if index < len(video_assets):
                choice = selection["video"]
                add_asset(scene, video_assets[index], "video", choice["vendor"], choice["model"])
                generated_video = True
            if index < len(narration_assets):
                choice = selection["tts"]
                add_asset(
                    scene,
                    narration_assets[index],
                    "narration",
                    choice["vendor"],
                    choice["model"],
                )
            if not generated_video:
                scene.verification_state = "FAIL"

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
        for scene in scenes:
            if scene.verification_state == "FAIL":
                db.add(
                    EvaluationResult(
                        evaluation_id=evaluation.id,
                        check_name="Required generated video",
                        status="FAIL",
                        explanation=(
                            f"Scene {scene.position} has no provider-generated video asset; "
                            "a keyframe fallback cannot satisfy a video order."
                        ),
                        evidence_json=canonical_json({"scene_id": scene.id}),
                        confidence_basis="Genblaze pipeline output",
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
            mandate_id = run.mandate_id
            mandate_version_number = run.mandate_version
            mandate_version = db.scalar(
                select(MandateVersion).where(
                    MandateVersion.mandate_id == mandate_id,
                    MandateVersion.version == mandate_version_number,
                )
            )
            mandate_policy = mandate_schema(mandate_version)
            canvas = tuple(
                int(part) for part in mandate_policy["required_resolution"].lower().split("x")
            )
            storyboard_seed = (
                f"{prompt}\n\nRequired spoken language: "
                f"{mandate_policy['required_language']}."
            )

        chat_choice = selection["chat"]
        image_choice = selection["image"]
        video_choice = selection["video"]
        tts_choice = selection["tts"]
        music_choice = selection.get("music")
        image_entry = catalog.resolve(catalog.IMAGE, image_choice["vendor"])
        video_entry = catalog.resolve(catalog.VIDEO, video_choice["vendor"])
        tts_entry = catalog.resolve(catalog.TTS, tts_choice["vendor"])
        music_entry = (
            catalog.resolve(catalog.MUSIC, music_choice["vendor"]) if music_choice else None
        )

        _record(
            run_id,
            "storyboard.generation.started",
            "OpenAI structured storyboard generation started",
        )
        scene_count = len(scenes)
        scene_duration = 5
        if scene_count == 1:
            scene_duration = int(mandate_policy["required_duration_sec"])
        spec, _ = await asyncio.to_thread(
            generate_storyboard,
            storyboard_seed,
            chat_choice["model"],
            scene_count=scene_count,
            scene_duration=scene_duration,
        )
        spec = _merge_storyboard_with_planned_scenes(spec, scenes)
        spec = spec.model_copy(
            update={"total_duration_sec": sum(scene.duration_sec for scene in spec.scenes)}
        )
        _persist_storyboard_narration(run_id, spec)
        spec = snap_scene_durations(spec, video_entry)
        _record(
            run_id,
            "storyboard.generated",
            "Confirmed scene prompts compiled into a Genblaze storyboard",
        )

        _record(run_id, "generation.keyframes.started", "Reference and keyframe pipelines started")
        b0_result, _ = await _pipeline_result(
            build_reference_pipeline(spec, image_entry, image_choice["model"]), timeout=240
        )
        b1_result, _ = await _pipeline_result(
            build_keyframe_pipeline(spec, image_entry, image_choice["model"], b0_result),
            timeout=600,
        )
        _record(
            run_id, "generation.keyframes.completed", "Keyframes stored by the Genblaze B2 sink"
        )

        _record(
            run_id, "generation.media.started", "Video, narration, and music generation started"
        )
        b2_result, b2_failures = await _pipeline_result(
            build_media_pipeline(
                spec,
                b1_result,
                video_entry=video_entry,
                video_model=video_choice["model"],
                tts_entry=tts_entry,
                tts_model=tts_choice["model"],
                music_entry=music_entry,
                music_model=music_choice["model"] if music_choice else None,
            ),
            timeout=settings.max_run_duration_sec,
            best_effort=True,
        )
        video_failures = [
            failure
            for failure in b2_failures
            if failure["provider"] == video_entry.vendor and failure["model"] == video_choice["model"]
        ]
        generated_videos = [
            asset
            for step in b2_result.run.steps
            for asset in (step.assets or [])
            if (asset.media_type or "").lower().startswith("video/")
        ]
        missing_video_scenes = list(range(len(generated_videos) + 1, len(scenes) + 1))
        if video_failures:
            _record(
                run_id,
                "generation.video.failed",
                "Required provider video step failed",
            )
        if missing_video_scenes:
            details = "; ".join(
                str(failure.get("error", "Provider step failed")) for failure in video_failures
            )
            raise RuntimeError(
                "Required provider-generated video is missing for scene(s) "
                f"{', '.join(map(str, missing_video_scenes))}. "
                f"{details or 'The provider returned no durable video asset.'}"
            )
        _record(run_id, "composition.started", "ffmpeg composition started")
        final_asset, notices = await asyncio.to_thread(
            compose_final, b2_result, b1_result, spec, canvas
        )
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
