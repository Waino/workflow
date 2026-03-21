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

    path: Path
    """Absolute path to the .md file."""

    @model_validator(mode="after")
    def state_must_not_be_review(self) -> Plan:
        """Prevent plain Plan from being created with state=REVIEW.

        Review documents must use the Review subclass instead.
        """
        if self.state == PlanState.REVIEW:
            msg = "Use Review, not Plan, for review documents"
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


class Review(Plan):
    """A review document under docs/plans/reviews/{sub_state}/."""

    state: PlanState = PlanState.REVIEW
    review_sub_state: ReviewSubState
    """The kind of review: critique, pr, or full."""

    review_timestamp: datetime | None = None
    """Set for CRITIQUE reviews: the timestamp suffix in the filename."""

    review_commit: str | None = None
    """Set for PR reviews: the abbreviated commit hash suffix in the filename."""

    @model_validator(mode="after")
    def state_must_not_be_review(self) -> Review:  # type: ignore[override]
        """No-op: overrides Plan's validator so state=REVIEW is accepted here."""
        return self

    @model_validator(mode="after")
    def validate_review_fields(self) -> Review:
        """Enforce that state is REVIEW and that the right timestamp/commit is present."""
        if self.state != PlanState.REVIEW:
            msg = "Review.state must be REVIEW"
            raise ValueError(msg)
        if self.review_sub_state == ReviewSubState.CRITIQUE and self.review_timestamp is None:
            msg = "CRITIQUE review requires review_timestamp"
            raise ValueError(msg)
        if self.review_sub_state == ReviewSubState.PR and self.review_commit is None:
            msg = "PR review requires review_commit"
            raise ValueError(msg)
        if self.review_sub_state == ReviewSubState.FULL:
            if self.review_timestamp is None:
                msg = "FULL review requires review_timestamp"
                raise ValueError(msg)
            if self.review_commit is None:
                msg = "FULL review requires review_commit"
                raise ValueError(msg)
        return self

    @property
    def name(self) -> str:
        """Review document name.

        CRITIQUE: '002_pydantic_models_2026-03-18T14-30-00Z'
        PR:       '002_pydantic_models_0e6baa9'
        FULL:     '2026-03-18T14-30-00Z_0e6baa9'  (no plan identifier; full-codebase review)
        """
        base = f"{self.sequence_number_str}_{self.slug}"
        if self.review_sub_state == ReviewSubState.PR and self.review_commit is not None:
            return f"{base}_{self.review_commit}"
        if self.review_sub_state == ReviewSubState.CRITIQUE and self.review_timestamp is not None:
            return f"{base}_{self.review_timestamp.strftime('%Y-%m-%dT%H-%M-%SZ')}"
        if self.review_sub_state == ReviewSubState.FULL and self.review_timestamp is not None and self.review_commit is not None:
            return f"{self.review_timestamp.strftime('%Y-%m-%dT%H-%M-%SZ')}_{self.review_commit}"
        return base


class AuditLogEntry(BaseModel):
    """A single entry in the JSONL audit log."""

    timestamp: datetime
    actor: str
    """Agent identity: 'planner', 'builder', 'reviewer', 'human'."""

    command: str
    """The command executed, e.g. 'critique', 'subdivide', 'todo'."""

    input_plan: str | None = None
    output_plans: list[str] = []
