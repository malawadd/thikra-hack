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


SequencePreset = Literal[
    "landscape_720", "landscape_1080", "portrait_720", "portrait_1080",
    "square_720", "square_1080",
]
TrackKind = Literal["visual", "text", "caption", "audio"]
ClipKind = Literal["video", "image", "text", "caption", "audio"]


class SequenceTrack(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    kind: TrackKind
    order: int = Field(ge=0, le=15)
    locked: bool = False
    hidden: bool = False
    muted: bool = False


class VisualTransform(StrictModel):
    fit: Literal["fit", "fill"] = "fill"
    position_x: float = Field(default=0.5, ge=0, le=1)
    position_y: float = Field(default=0.5, ge=0, le=1)
    scale: float = Field(default=1, ge=0.05, le=10)
    rotation: float = Field(default=0, ge=-360, le=360)
    opacity: float = Field(default=1, ge=0, le=1)
    fade_in_ms: int = Field(default=0, ge=0, le=30_000)
    fade_out_ms: int = Field(default=0, ge=0, le=30_000)
    ken_burns: bool = False


class AudioSettings(StrictModel):
    gain_db: float = Field(default=0, ge=-60, le=24)
    muted: bool = False
    fade_in_ms: int = Field(default=0, ge=0, le=30_000)
    fade_out_ms: int = Field(default=0, ge=0, le=30_000)
    role: Literal["source", "narration", "music", "other"] = "other"


class TextSettings(StrictModel):
    content: str = Field(default="", max_length=4000)
    font_family: Literal["Noto Sans", "Noto Sans Arabic", "Noto Serif"] = "Noto Sans"
    font_size: int = Field(default=56, ge=10, le=240)
    font_weight: Literal[400, 500, 600, 700, 800] = 600
    color: str = Field(default="#ffffff", pattern=r"^#[0-9A-Fa-f]{6}$")
    background: str = Field(default="#00000000", pattern=r"^#[0-9A-Fa-f]{8}$")
    align: Literal["left", "center", "right"] = "center"
    position_x: float = Field(default=0.5, ge=0, le=1)
    position_y: float = Field(default=0.82, ge=0, le=1)


class SequenceClip(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    track_id: str
    kind: ClipKind
    name: str = Field(default="Clip", min_length=1, max_length=260)
    asset_id: str | None = None
    start_ms: int = Field(ge=0, le=300_000)
    duration_ms: int = Field(gt=0, le=300_000)
    source_in_ms: int = Field(default=0, ge=0, le=300_000)
    transition_in: Literal["cut", "dissolve", "fade_black"] = "cut"
    transition_out: Literal["cut", "dissolve", "fade_black"] = "cut"
    transition_duration_ms: int = Field(default=0, ge=0, le=10_000)
    transform: VisualTransform = Field(default_factory=VisualTransform)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    text: TextSettings | None = None

    @model_validator(mode="after")
    def validate_clip(self):
        if self.kind in {"video", "image", "audio"} and not self.asset_id:
            raise ValueError(f"{self.kind} clips require an asset_id")
        if self.kind in {"text", "caption"} and self.text is None:
            raise ValueError(f"{self.kind} clips require text settings")
        if self.transition_duration_ms > self.duration_ms // 2:
            raise ValueError("transition duration cannot exceed half the clip")
        return self


class SequenceDocument(StrictModel):
    schema_version: Literal[1] = 1
    preset: SequencePreset = "landscape_1080"
    background: str = Field(default="#05070a", pattern=r"^#[0-9A-Fa-f]{6}$")
    tracks: list[SequenceTrack] = Field(min_length=1, max_length=16)
    clips: list[SequenceClip] = Field(default_factory=list, max_length=500)
    duck_music_under_narration: bool = True
    captions_stale: bool = False

    @model_validator(mode="after")
    def validate_timeline(self):
        track_ids = [track.id for track in self.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("track ids must be unique")
        if len({track.order for track in self.tracks}) != len(self.tracks):
            raise ValueError("track order values must be unique")
        clip_ids = [clip.id for clip in self.clips]
        if len(clip_ids) != len(set(clip_ids)):
            raise ValueError("clip ids must be unique")
        by_track = {track.id: track for track in self.tracks}
        compatible = {
            "visual": {"video", "image"}, "text": {"text"},
            "caption": {"caption"}, "audio": {"audio"},
        }
        for clip in self.clips:
            track = by_track.get(clip.track_id)
            if track is None:
                raise ValueError(f"clip {clip.id} references a missing track")
            if clip.kind not in compatible[track.kind]:
                raise ValueError(f"clip {clip.id} is incompatible with {track.kind} track")
            if clip.start_ms + clip.duration_ms > 300_000:
                raise ValueError("sequence duration cannot exceed five minutes")
        return self


class SequenceCreate(StrictModel):
    name: str = Field(default="Main edit", min_length=1, max_length=240)
    preset: SequencePreset = "landscape_1080"


class SequenceRevisionCreate(StrictModel):
    base_revision_id: str
    document: SequenceDocument
    summary: str = Field(default="Timeline edit", min_length=1, max_length=500)


class SequenceViewUpdate(StrictModel):
    playhead_ms: int = Field(default=0, ge=0, le=300_000)
    zoom: float = Field(default=80, ge=8, le=800)
    selection: list[str] = Field(default_factory=list, max_length=500)
    panel_layout: dict[str, float] = Field(default_factory=dict)


class SequenceRestore(StrictModel):
    base_revision_id: str
    revision_id: str


class RenderCreate(StrictModel):
    revision_id: str
    preset: SequencePreset
    confirm: Literal[True]


class RenderResume(StrictModel):
    confirm: Literal[True]


class EditorGenerationEstimate(StrictModel):
    kind: Literal["image", "video"]
    vendor: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=2, max_length=4000)
    variants: int = Field(default=1, ge=1, le=4)
    duration_ms: int | None = Field(default=None, ge=1000, le=30_000)
    reference_asset_id: str | None = None


class EditorGenerationCreate(EditorGenerationEstimate):
    estimate_hash: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]
    sequence_id: str | None = None


class EditorJobResume(StrictModel):
    estimate_hash: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]


class CaptionEstimate(StrictModel):
    revision_id: str
    language: str | None = Field(default=None, max_length=20)


class CaptionCreate(CaptionEstimate):
    estimate_hash: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]


class CaptionApply(StrictModel):
    base_revision_id: str
    cues: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class SequenceProposalCreate(StrictModel):
    base_revision_id: str
    prompt: str = Field(min_length=2, max_length=4000)
    selected_clip_ids: list[str] = Field(default_factory=list, max_length=100)


class SequenceProposalOperation(StrictModel):
    id: str
    type: Literal[
        "add_track", "update_track", "remove_track", "add_clip", "update_clip",
        "move_clip", "remove_clip", "add_caption", "update_caption", "remove_caption",
    ]
    track_id: str | None = None
    clip_id: str | None = None
    track: SequenceTrack | None = None
    clip: SequenceClip | None = None
    patch: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=300)


class SequenceProposalOutput(StrictModel):
    rationale: str = Field(max_length=1200)
    operations: list[SequenceProposalOperation] = Field(max_length=40)


class SequenceProposalApply(StrictModel):
    base_revision_id: str
    operation_ids: list[str] = Field(min_length=1, max_length=40)
