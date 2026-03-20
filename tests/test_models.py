"""Tests for workflow.models."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from workflow.models import AuditLogEntry, Plan, PlanState, ReviewSubState


class TestPlanState:
    def test_values_match_directory_names(self) -> None:
        assert PlanState.INTENT == "intent"
        assert PlanState.IMPLEMENTING == "implementing"
        assert PlanState.REVIEW == "review"

    def test_is_iterable(self) -> None:
        states = list(PlanState)
        assert len(states) == 8
        assert PlanState.INTENT in states


class TestReviewSubState:
    def test_values(self) -> None:
        assert ReviewSubState.CRITIQUE == "critique"
        assert ReviewSubState.PR == "pr"
        assert ReviewSubState.FULL == "full"


class TestPlan:
    def test_simple_sequence_number(self) -> None:
        plan = Plan(sequence_number=[2], slug="pydantic_models", state=PlanState.PLANNING, path=Path("/tmp/test.md"))
        assert plan.sequence_number_str == "002"
        assert plan.name == "002_pydantic_models"

    def test_hierarchical_sequence_number(self) -> None:
        plan = Plan(
            sequence_number=[12, 1],
            slug="frobnicator_backend",
            state=PlanState.PLANNING,
            path=Path("/tmp/t.md"),
        )
        assert plan.sequence_number_str == "012.1"
        assert plan.name == "012.1_frobnicator_backend"

    def test_review_with_sub_state(self) -> None:
        plan = Plan(
            sequence_number=[5],
            slug="review_pr",
            state=PlanState.REVIEW,
            review_sub_state=ReviewSubState.PR,
            path=Path("/tmp/r.md"),
        )
        assert plan.state == PlanState.REVIEW
        assert plan.review_sub_state == ReviewSubState.PR

    def test_review_sub_state_defaults_to_none(self) -> None:
        plan = Plan(sequence_number=[1], slug="idea", state=PlanState.INTENT, path=Path("/tmp/i.md"))
        assert plan.review_sub_state is None

    def test_empty_sequence_number_rejected(self) -> None:
        with pytest.raises(ValidationError, match="List should have at least 1 item"):
            Plan(sequence_number=[], slug="idea", state=PlanState.INTENT, path=Path("/tmp/i.md"))

    def test_review_state_requires_sub_state(self) -> None:
        with pytest.raises(ValidationError, match="review_sub_state must be set"):
            Plan(sequence_number=[5], slug="r", state=PlanState.REVIEW, path=Path("/tmp/r.md"))

    def test_non_review_state_rejects_sub_state(self) -> None:
        with pytest.raises(ValidationError, match="review_sub_state must be None"):
            Plan(
                sequence_number=[5],
                slug="r",
                state=PlanState.INTENT,
                review_sub_state=ReviewSubState.PR,
                path=Path("/tmp/r.md"),
            )


class TestAuditLogEntry:
    def test_minimal(self) -> None:
        entry = AuditLogEntry(
            timestamp=datetime(2026, 3, 18, 14, 30, tzinfo=UTC),
            actor="planner",
            command="critique",
        )
        assert entry.input_plan is None
        assert entry.output_plans == []

    def test_full(self) -> None:
        entry = AuditLogEntry(
            timestamp=datetime(2026, 3, 18, 14, 30, tzinfo=UTC),
            actor="planner",
            command="subdivide",
            input_plan="001_idea",
            output_plans=["002_pydantic_models", "003_core_algorithm"],
        )
        assert entry.input_plan == "001_idea"
        assert len(entry.output_plans) == 2

    def test_json_roundtrip(self) -> None:
        entry = AuditLogEntry(
            timestamp=datetime(2026, 3, 18, 14, 30, tzinfo=UTC),
            actor="human",
            command="todo",
            input_plan="002_pydantic_models",
        )
        json_str = entry.model_dump_json()
        restored = AuditLogEntry.model_validate_json(json_str)
        assert restored == entry
