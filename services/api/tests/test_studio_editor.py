"""Sequence validation, immutable history, assets, and render checkpoints."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.commerce import models as commerce_models  # noqa: F401
from app.studio import editor, service
from app.studio.models import StudioAsset, StudioGenerationJob
from app.studio.schemas import SequenceDocument
from app.thikra.database import Base
from app.thikra.models import Workspace


def _database() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Workspace(name="Editor Test", environment="DEMO"))
    db.commit()
    return db


def _project(db: Session):
    return service.create_project(
        db, name="Editor campaign", description="", budget=500, currency="USD", graph=None
    )


def test_sequence_revision_is_immutable_and_view_state_is_separate() -> None:
    db = _database()
    sequence = editor.create_sequence(db, _project(db), "Main edit", "landscape_1080")
    first_id = sequence.current_revision_id
    document = editor.default_document("portrait_720")
    revision = editor.create_revision(db, sequence, first_id, document, "Portrait cut")
    assert revision.number == 2
    assert revision.parent_revision_id == first_id
    assert sequence.current_revision_id == revision.id
    sequence.view_state_json = '{"playhead_ms":4200,"zoom":120,"selection":[]}'
    db.commit()
    assert sequence.current_revision_id == revision.id
    try:
        editor.create_revision(db, sequence, first_id, document, "Stale")
    except RuntimeError as exc:
        assert str(exc) == "STALE_REVISION"
    else:
        raise AssertionError("stale sequence write was accepted")


def test_timeline_limits_types_transitions_and_duration() -> None:
    base = editor.default_document().model_dump(mode="json")
    base["clips"] = [
        {
            "id": "title",
            "track_id": "titles",
            "kind": "text",
            "name": "Title",
            "start_ms": 0,
            "duration_ms": 2000,
            "transition_duration_ms": 1100,
            "text": {"content": "مرحبا"},
        }
    ]
    try:
        SequenceDocument.model_validate(base)
    except ValueError as exc:
        assert "half the clip" in str(exc)
    else:
        raise AssertionError("oversized transition was accepted")
    base["clips"][0]["transition_duration_ms"] = 0
    base["clips"][0]["track_id"] = "a1"
    try:
        SequenceDocument.model_validate(base)
    except ValueError as exc:
        assert "incompatible" in str(exc)
    else:
        raise AssertionError("text was accepted on an audio track")


def test_legacy_text_geometry_is_normalized_without_mutating_history() -> None:
    payload = editor.default_document().model_dump(mode="json")
    payload["schema_version"] = 1
    payload["clips"] = [
        {
            "id": "legacy-title",
            "track_id": "titles",
            "kind": "text",
            "name": "Legacy title",
            "start_ms": 0,
            "duration_ms": 1200,
            "text": {"content": "قديم", "position_x": 0.2, "position_y": 0.3},
        }
    ]
    legacy = SequenceDocument.model_validate(payload)
    normalized = editor.upgrade_document(legacy)
    assert legacy.schema_version == 1
    assert legacy.clips[0].transform.position_x == 0.5
    assert normalized.schema_version == 2
    assert normalized.clips[0].transform.position_x == 0.2
    assert normalized.clips[0].transform.position_y == 0.3


def test_source_ranges_and_unsupported_overlap_are_rejected() -> None:
    db = _database()
    project = _project(db)
    asset = StudioAsset(
        project_id=project.id,
        name="source.mp4",
        asset_type="video",
        content_type="video/mp4",
        local_path="C:/missing/source.mp4",
        size=20,
        sha256="a" * 64,
        duration_ms=4000,
        analysis_status="READY",
    )
    db.add(asset)
    db.commit()
    document = editor.default_document()
    clip = {
        "id": "clip-a",
        "track_id": "v1",
        "kind": "video",
        "name": "Source",
        "asset_id": asset.id,
        "start_ms": 0,
        "duration_ms": 3000,
        "source_in_ms": 2000,
    }
    payload = document.model_dump(mode="json")
    payload["clips"] = [clip]
    invalid = SequenceDocument.model_validate(payload)
    try:
        editor.validate_sources(db, project.id, invalid)
    except ValueError as exc:
        assert "source duration" in str(exc)
    else:
        raise AssertionError("out-of-range source trim was accepted")
    clip["duration_ms"] = 2000
    payload["clips"] = [clip, {**clip, "id": "clip-b", "start_ms": 1000}]
    overlapping = SequenceDocument.model_validate(payload)
    try:
        editor.validate_sources(db, project.id, overlapping)
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("unsupported overlap was accepted")


def test_identical_successful_render_is_reused_without_reencoding() -> None:
    db = _database()
    project = _project(db)
    sequence = editor.create_sequence(db, project, "Main edit", "square_720")
    revision = sequence.current_revision_id
    first = editor.create_render(
        db, sequence, db.get(editor.SequenceRevision, revision), "square_720"
    )
    first.status = "SUCCEEDED"
    first.output_asset_id = None
    db.commit()
    second = editor.create_render(
        db, sequence, db.get(editor.SequenceRevision, revision), "square_720"
    )
    assert second.status == "SUCCEEDED"
    assert second.progress == 100


def test_editor_generation_uses_catalog_models_budget_and_fresh_hash(monkeypatch) -> None:
    db = _database()
    project = _project(db)
    monkeypatch.setattr(service, "effective_provider_secret", lambda _vendor: "configured")
    estimate = editor.generation_estimate(
        db,
        project,
        kind="image",
        vendor="openai",
        model="gpt-image-1",
        prompt="A product insert",
        variants=3,
        duration_ms=None,
        reference_asset_id=None,
    )
    assert estimate["estimated_cost_minor"] == 54
    assert estimate["within_budget"]
    try:
        editor.create_generation_job(db, project, estimate, "0" * 64, None)
    except RuntimeError as exc:
        assert str(exc) == "STALE_ESTIMATE"
    else:
        raise AssertionError("stale editor estimate was accepted")


def test_caption_cues_apply_as_one_edit_and_become_fresh() -> None:
    db = _database()
    project = _project(db)
    sequence = editor.create_sequence(db, project, "Main edit", "portrait_1080")
    revision = editor.apply_caption_cues(
        db,
        sequence,
        sequence.current_revision_id,
        [
            {"id": "arabic-1", "start_ms": 0, "end_ms": 1500, "text": "أهلاً بكم"},
            {"id": "latin-2", "start_ms": 1700, "end_ms": 3000, "text": "Welcome"},
        ],
    )
    document = SequenceDocument.model_validate_json(revision.timeline_json)
    assert [clip.text.content for clip in document.clips if clip.kind == "caption"] == [
        "أهلاً بكم",
        "Welcome",
    ]
    assert document.captions_stale is False


def test_sequence_agent_proposal_requires_approval_and_rejects_stale(monkeypatch) -> None:
    db = _database()
    project = _project(db)
    monkeypatch.setattr(editor.settings, "app_mode", "DEMO")
    sequence = editor.create_sequence(db, project, "Main edit", "landscape_720")
    original = sequence.current_revision_id
    proposal = editor.create_sequence_proposal(
        db, sequence, original, "Add a short bilingual opening title", []
    )
    before = db.get(editor.SequenceRevision, original)
    assert not before.timeline_json.count("agent-title")
    revision = editor.apply_sequence_proposal(db, sequence, proposal, original, ["op-title"])
    assert "agent-title" in revision.timeline_json
    try:
        editor.apply_sequence_proposal(db, sequence, proposal, original, ["op-title"])
    except RuntimeError as exc:
        assert str(exc) == "STALE_REVISION"
    else:
        raise AssertionError("stale agent proposal was accepted")


def test_cancelled_generation_resume_reuses_checkpoint_without_charge() -> None:
    db = _database()
    project = _project(db)
    asset = StudioAsset(
        project_id=project.id,
        name="paid-output.png",
        asset_type="image",
        content_type="image/png",
        local_path="C:/missing/paid-output.png",
        size=10,
        sha256="c" * 64,
        source_kind="GENERATED",
        analysis_status="READY",
    )
    db.add(asset)
    db.flush()
    original = StudioGenerationJob(
        project_id=project.id,
        kind="image",
        vendor="openai",
        model="gpt-image-1",
        prompt="Already paid",
        variants=1,
        estimate_hash="e" * 64,
        estimated_cost_minor=18,
        status="CANCELLED",
        checkpoint_json=f'["{asset.id}"]',
    )
    db.add(original)
    db.commit()
    estimate = editor.generation_resume_estimate(db, original)
    resumed = editor.create_resume_generation_job(db, original, estimate, estimate["estimate_hash"])
    assert estimate["estimated_cost_minor"] == 0
    assert resumed.status == "SUCCEEDED"
    assert resumed.result_asset_ids_json == f'["{asset.id}"]'
