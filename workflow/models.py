"""Core data models for the workflow tool."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - used at runtime by Pydantic
from enum import StrEnum
from pathlib import Path  # noqa: TC003 - used at runtime by Pydantic

from pydantic import BaseModel, Field, model_validator


class PlanState(StrEnum):
    """States in the plan state machine. Values match subdirectory names under docs/plans/."""

    INTENT = "intent"
    PLANNING = "planning"
    TODO = "todo"
    IMPLEMENTING = "implementing"
    CHECKING = "checking"
    DONE = "done"
    REJECTED = "rejected"
    REVIEW = "review"


class ReviewSubState(StrEnum):
    """Sub-states within the reviews directory."""

    CRITIQUE = "critique"
    PR = "pr"
    FULL = "full"


class Plan(BaseModel):
    """A plan in the workflow state machine."""

    sequence_number: list[int] = Field(min_length=1)
    """Hierarchical sequence number, e.g. [2] or [12, 1]."""

    slug: str
    """Human-readable identifier, e.g. 'pydantic_models'."""

    state: PlanState

    review_sub_state: ReviewSubState | None = None
    """Set when state is REVIEW to distinguish critique, pr, and full."""

    path: Path
    """Absolute path to the .md file."""

    @model_validator(mode="after")
    def validate_review_sub_state(self) -> Plan:
        """Enforce consistency between state and review_sub_state.

        - state == REVIEW requires review_sub_state to be set.
        - state != REVIEW requires review_sub_state to be None.
        """
        if self.state == PlanState.REVIEW and self.review_sub_state is None:
            msg = "review_sub_state must be set when state is REVIEW"
            raise ValueError(msg)
        if self.state != PlanState.REVIEW and self.review_sub_state is not None:
            msg = "review_sub_state must be None when state is not REVIEW"
            raise ValueError(msg)
        return self

    @property
    def sequence_number_str(self) -> str:
        """Dot-separated string representation, e.g. '002' or '012.1'."""
        parts = [str(n) for n in self.sequence_number]
        parts[0] = parts[0].zfill(3)
        return ".".join(parts)

    @property
    def name(self) -> str:
        """Full name, e.g. '002_pydantic_models'."""
        return f"{self.sequence_number_str}_{self.slug}"


class AuditLogEntry(BaseModel):
    """A single entry in the JSONL audit log."""

    timestamp: datetime
    actor: str
    """Agent identity: 'planner', 'builder', 'reviewer', 'human'."""

    command: str
    """The command executed, e.g. 'critique', 'subdivide', 'todo'."""

    input_plan: str | None = None
    output_plans: list[str] = []
