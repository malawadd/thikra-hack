"""Public Studio graph, proposal, budget, and asset contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NodeType = Literal[
    "creative_brief",
    "reference_asset",
    "look_director",
    "image_generation",
    "asset_selector",
    "video_generation",
    "narration",
    "music",
    "composition",
    "verification",
    "export",
    "note",
    "group",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowNode(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    type: NodeType
    label: str = Field(min_length=1, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_generation(self):
        if self.type in {"image_generation", "video_generation"}:
            variants = self.config.get("variants", 1)
            if not isinstance(variants, int) or not 1 <= variants <= 4:
                raise ValueError("generation variants must be an integer from 1 to 4")
        return self


class WorkflowEdge(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    source: str
    source_port: str
    target: str
    target_port: str


class WorkflowGraph(StrictModel):
    schema_version: Literal[1] = 1
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=120)
    edges: list[WorkflowEdge] = Field(default_factory=list, max_length=300)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=2, max_length=240)
    description: str = Field(default="", max_length=2000)
    budget_cap_minor: int = Field(default=500, ge=0, le=1_000_000)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    graph: WorkflowGraph | None = None


class ProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=240)
    description: str | None = Field(default=None, max_length=2000)
    budget_cap_minor: int | None = Field(default=None, ge=0, le=1_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class RevisionCreate(StrictModel):
    base_revision_id: str
    graph: WorkflowGraph
    summary: str = Field(default="Manual edit", min_length=1, max_length=500)


class LayoutUpdate(StrictModel):
    positions: dict[str, dict[str, float]]
    viewport: dict[str, float]


class AgentProposalCreate(StrictModel):
    base_revision_id: str
    prompt: str = Field(min_length=2, max_length=4000)
    selected_node_ids: list[str] = Field(default_factory=list, max_length=40)
    asset_ids: list[str] = Field(default_factory=list, max_length=4)


class ProposalConfigPatch(StrictModel):
    text: str | None
    prompt_guidance: str | None
    variants: int | None
    vendor: str | None
    model: str | None
    duration_sec: float | None
    selected_index: int | None
    format: str | None


class ProposedWorkflowNode(StrictModel):
    id: str
    type: NodeType
    label: str
    config: ProposalConfigPatch


class ProposalOperation(StrictModel):
    id: str
    type: Literal["add_node", "update_node", "remove_node", "connect", "disconnect"]
    node_id: str | None
    node: ProposedWorkflowNode | None
    edge: WorkflowEdge | None
    config_patch: ProposalConfigPatch | None
    depends_on: list[str]
    summary: str = Field(max_length=300)


class WorkflowProposalOutput(StrictModel):
    rationale: str = Field(max_length=1200)
    estimated_cost_impact_minor: int = Field(ge=-1_000_000, le=1_000_000)
    operations: list[ProposalOperation] = Field(max_length=30)


class ProposalApply(StrictModel):
    base_revision_id: str
    operation_ids: list[str] = Field(min_length=1, max_length=30)


class EstimateRequest(StrictModel):
    revision_id: str
    target_node_ids: list[str] = Field(default_factory=list, max_length=120)
    force_rerun: bool = False


class ExecutionCreate(StrictModel):
    revision_id: str
    estimate_hash: str = Field(min_length=64, max_length=64)
    target_node_ids: list[str] = Field(default_factory=list, max_length=120)
    force_rerun: bool = False


class ResumeExecutionCreate(StrictModel):
    estimate_hash: str = Field(min_length=64, max_length=64)


class AnnotationCreate(StrictModel):
    asset_id: str
    kind: Literal["point", "rectangle", "timestamp"]
    geometry: dict[str, float] = Field(default_factory=dict)
    body: str = Field(min_length=1, max_length=1200)
    timestamp_ms: int | None = Field(default=None, ge=0)


class ProviderConnectionSet(StrictModel):
    secret: str = Field(min_length=6, max_length=1000)
