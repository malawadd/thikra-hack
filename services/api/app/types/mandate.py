"""Structured-output shape for semantic mandate extraction."""

from pydantic import BaseModel, ConfigDict


class MandateProposal(BaseModel):
    """Model-proposed fields; commercial limits remain user-authored."""

    model_config = ConfigDict(extra="forbid")

    objective: str
    target_audience: str
    language: str
    tone: str
    forbidden_elements: list[str]
    required_elements: list[str]
    claim_constraints: list[str]
    human_review_triggers: list[str]
