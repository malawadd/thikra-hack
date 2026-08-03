"""Studio graph, revision, proposal, budget, cache, and execution tests."""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.commerce import models as commerce_models  # noqa: F401
from app.studio import service
from app.studio.graph import default_graph, validate_graph
from app.studio.models import NodeExecution, StudioProject, WorkflowExecution
from app.studio.schemas import WorkflowGraph
from app.thikra.database import Base
from app.thikra.models import Workspace


def _database() -> tuple[Session, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    db.add(Workspace(name="Studio Test", environment="DEMO"))
    db.commit()
    return db, factory


def test_graph_rejects_cycles_and_bad_ports() -> None:
    document = default_graph().model_dump(mode="json")
    document["edges"].append(
        {
            "id": "cycle",
            "source": "export",
            "source_port": "delivery",
            "target": "look",
            "target_port": "brief",
        }
    )
    errors = validate_graph(WorkflowGraph.model_validate(document))
    assert "Workflow graph must be acyclic" in errors
    assert any("Port type mismatch" in item for item in errors)


def test_semantic_revisions_are_immutable_and_stale_writes_fail() -> None:
    db, _ = _database()
    project = service.create_project(
        db, name="Campaign", description="", budget=500, currency="USD", graph=None
    )
    original = project.current_revision_id
    graph = default_graph()
    graph.nodes[0].config["text"] = "A precise new direction"
    revision = service.create_revision(db, project, original, graph, "Refine brief")
    assert revision.number == 2
    assert project.current_revision_id == revision.id
    try:
        service.create_revision(db, project, original, graph, "Stale edit")
    except RuntimeError as exc:
        assert str(exc) == "STALE_REVISION"
    else:
        raise AssertionError("stale revision was accepted")


def test_partial_agent_apply_includes_dependencies(monkeypatch) -> None:
    db, _ = _database()
    monkeypatch.setattr(service.settings, "app_mode", "DEMO")
    project = service.create_project(
        db, name="Campaign", description="", budget=500, currency="USD", graph=None
    )
    proposal = service.create_proposal(
        db, project, project.current_revision_id, "Use a tactile violet product look", []
    )
    revision = service.apply_proposal(
        db,
        project,
        proposal,
        project.current_revision_id,
        ["op-variants"],
    )
    graph = WorkflowGraph.model_validate(service._load(revision.graph_json, {}))
    brief = next(node for node in graph.nodes if node.id == "brief")
    image = next(node for node in graph.nodes if node.id == "image")
    assert "tactile violet" in brief.config["text"]
    assert image.config["variants"] == 4


def test_budget_confirmation_and_demo_execution_cache(tmp_path, monkeypatch) -> None:
    db, factory = _database()
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service.settings, "thikra_data_dir", str(tmp_path))
    monkeypatch.setattr(service.settings, "app_mode", "DEMO")
    project = service.create_project(
        db, name="Campaign", description="", budget=1000, currency="USD", graph=None
    )
    revision = db.get(service.WorkflowRevision, project.current_revision_id)
    estimate = service.estimate_revision(revision, [], False)
    assert estimate["estimated_cost_minor"] > 0
    execution = service.create_execution(db, project, revision, estimate, False)
    service.execute_workflow(execution.id)
    db.expire_all()
    finished = db.get(WorkflowExecution, execution.id)
    assert finished.status == "SUCCEEDED"
    assert db.scalar(
        select(NodeExecution).where(
            NodeExecution.execution_id == execution.id,
            NodeExecution.node_type == "image_generation",
            NodeExecution.status == "SUCCEEDED",
        )
    )

    second = service.create_execution(db, project, revision, estimate, False)
    service.execute_workflow(second.id)
    db.expire_all()
    cached = list(
        db.scalars(
            select(NodeExecution).where(
                NodeExecution.execution_id == second.id,
                NodeExecution.status == "CACHED",
            )
        )
    )
    assert cached
    assert db.get(StudioProject, project.id).spent_minor == estimate["estimated_cost_minor"]


def test_targeted_execution_runs_target_and_descendants(tmp_path, monkeypatch) -> None:
    db, factory = _database()
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service.settings, "thikra_data_dir", str(tmp_path))
    monkeypatch.setattr(service.settings, "app_mode", "DEMO")
    project = service.create_project(
        db, name="Campaign", description="", budget=2000, currency="USD", graph=None
    )
    revision = db.get(service.WorkflowRevision, project.current_revision_id)
    full = service.estimate_revision(revision, [], False)
    first = service.create_execution(db, project, revision, full, False)
    service.execute_workflow(first.id)

    targeted = service.estimate_revision(revision, ["image"], True)
    assert "brief" not in targeted["execution_node_ids"]
    assert {"image", "select", "video", "compose", "verify", "export"}.issubset(
        targeted["execution_node_ids"]
    )
    rerun = service.create_execution(db, project, revision, targeted, True)
    service.execute_workflow(rerun.id)
    db.expire_all()
    records = list(
        db.scalars(select(NodeExecution).where(NodeExecution.execution_id == rerun.id))
    )
    assert {record.node_id for record in records} == set(targeted["execution_node_ids"])
    assert db.get(WorkflowExecution, rerun.id).status == "SUCCEEDED"


def test_failed_branch_blocks_dependents_but_independent_nodes_continue(
    tmp_path, monkeypatch
) -> None:
    db, factory = _database()
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service.settings, "thikra_data_dir", str(tmp_path))
    monkeypatch.setattr(service.settings, "app_mode", "DEMO")
    document = default_graph().model_dump(mode="json")
    document["nodes"].append(
        {"id": "independent", "type": "music", "label": "Independent music", "config": {}}
    )
    project = service.create_project(
        db,
        name="Campaign",
        description="",
        budget=2000,
        currency="USD",
        graph=WorkflowGraph.model_validate(document),
    )
    revision = db.get(service.WorkflowRevision, project.current_revision_id)
    estimate = service.estimate_revision(revision, [], True)
    execution = service.create_execution(db, project, revision, estimate, True)

    def fail_image(*_args, **_kwargs):
        raise RuntimeError("fixture image failure")

    monkeypatch.setattr(service, "_demo_svg", fail_image)
    service.execute_workflow(execution.id)
    db.expire_all()
    records = {
        item.node_id: item
        for item in db.scalars(
            select(NodeExecution).where(NodeExecution.execution_id == execution.id)
        )
    }
    assert records["image"].status == "FAILED"
    assert records["select"].status == "BLOCKED"
    assert records["independent"].status == "SUCCEEDED"
    assert db.get(WorkflowExecution, execution.id).status == "FAILED"


def test_resume_reuses_success_and_provider_checkpoint(tmp_path, monkeypatch) -> None:
    db, factory = _database()
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service.settings, "thikra_data_dir", str(tmp_path))
    monkeypatch.setattr(service.settings, "app_mode", "DEMO")
    project = service.create_project(
        db, name="Campaign", description="", budget=2000, currency="USD", graph=None
    )
    revision = db.get(service.WorkflowRevision, project.current_revision_id)
    estimate = service.estimate_revision(revision, [], False)
    original = service.create_execution(db, project, revision, estimate, False)

    def fail_image(*_args, **_kwargs):
        raise RuntimeError("local persistence failed after the provider completed")

    monkeypatch.setattr(service, "_demo_svg", fail_image)
    service.execute_workflow(original.id)
    db.expire_all()
    assert db.get(WorkflowExecution, original.id).status == "FAILED"

    original = db.get(WorkflowExecution, original.id)
    service._event(
        db,
        original,
        "node.provider_completed",
        "image",
        {
            "assets": [
                {
                    "url": "https://durable.example/image.png",
                    "media_type": "image/png",
                    "size_bytes": 42,
                    "sha256": "a" * 64,
                }
            ]
        },
    )
    db.commit()
    resume_estimate = service.estimate_resume(db, original)
    assert resume_estimate["recoverable_node_ids"] == ["image"]
    assert "brief" in resume_estimate["reused_node_ids"]
    assert not any(
        item["node_id"] == "image" for item in resume_estimate["line_items"]
    )

    resumed = service.create_resume_execution(db, project, original, resume_estimate)
    assert resumed.resumed_from_execution_id == original.id
    recovered = db.scalar(
        select(NodeExecution).where(
            NodeExecution.execution_id == resumed.id,
            NodeExecution.node_id == "image",
        )
    )
    assert recovered.status == "CACHED"
    assert recovered.charged_minor == 0
    service.execute_workflow(resumed.id)
    db.expire_all()
    assert db.get(WorkflowExecution, resumed.id).status == "SUCCEEDED"
