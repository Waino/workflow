"""Core data models for the workflow tool."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - used at runtime by Pydantic
from enum import StrEnum
from pathlib import Path  # noqa: TC003 - used at runtime by Pydantic

from pydantic import BaseModel


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

    sequence_number: list[int]
    """Hierarchical sequence number, e.g. [2] or [12, 1]."""

    slug: str
    """Human-readable identifier, e.g. 'pydantic_models'."""

    state: PlanState

    review_sub_state: ReviewSubState | None = None
    """Set when state is REVIEW to distinguish critique, pr, and full."""

    path: Path
    """Absolute path to the .md file."""

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
