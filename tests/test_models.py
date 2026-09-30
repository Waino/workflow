"""Tests for workflow.models."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from workflow.models import AuditLogEntry, Plan, PlanState, Review, ReviewSubState


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
        plan = Plan(
            sequence_number=[2],
            slug="pydantic_models",
            state=PlanState.PLANNING,
            path=Path("canon/docs/plans/planning/002_pydantic_models.md"),
        )
        assert plan.sequence_number_str == "002"
        assert plan.name == "002_pydantic_models"

    def test_hierarchical_sequence_number(self) -> None:
        plan = Plan(
            sequence_number=[12, 1],
            slug="frobnicator_backend",
            state=PlanState.PLANNING,
            path=Path("planner/docs/plans/planning/012.1_frobnicator_backend.md"),
        )
        assert plan.sequence_number_str == "012.1"
        assert plan.name == "012.1_frobnicator_backend"

    def test_absolute_path_resolves_against_project_dir(self) -> None:
        plan = Plan(
            sequence_number=[2],
            slug="foo",
            state=PlanState.PLANNING,
            path=Path("planner/docs/plans/planning/002_foo.md"),
        )
        assert plan.absolute_path(Path("/tmp/project")) == Path("/tmp/project/planner/docs/plans/planning/002_foo.md")

    def test_empty_sequence_number_rejected(self) -> None:
        with pytest.raises(ValidationError, match="List should have at least 1 item"):
            Plan(
                sequence_number=[],
                slug="idea",
                state=PlanState.INTENT,
                path=Path("canon/docs/plans/intent/001_idea.md"),
            )

    def test_plan_rejects_review_state(self) -> None:
        with pytest.raises(ValidationError, match="Use Review, not Plan"):
            Plan(
                sequence_number=[5],
                slug="r",
                state=PlanState.REVIEW,
                path=Path("reviewer/docs/plans/reviews/pr/005_r.md"),
            )


class TestReview:
    _ts = datetime(2026, 3, 18, 14, 30, tzinfo=UTC)

    def _pr(self, **kwargs: object) -> Review:
        return Review(
            sequence_number=[2],
            slug="pydantic_models",
            path=Path("reviewer/docs/plans/reviews/pr/002_pydantic_models_0e6baa9.md"),
            review_sub_state=ReviewSubState.PR,
            review_commit="0e6baa9",
            **kwargs,  # type: ignore[arg-type]
        )

    def _critique(self, **kwargs: object) -> Review:
        return Review(
            sequence_number=[2],
            slug="pydantic_models",
            path=Path("reviewer/docs/plans/reviews/critique/002_pydantic_models_2026-03-18T14-30-00Z.md"),
            review_sub_state=ReviewSubState.CRITIQUE,
            review_timestamp=self._ts,
            **kwargs,  # type: ignore[arg-type]
        )

    def test_pr_review_valid(self) -> None:
        r = self._pr()
        assert r.state == PlanState.REVIEW
        assert r.review_sub_state == ReviewSubState.PR
        assert r.review_commit == "0e6baa9"
        assert r.review_timestamp is None

    def test_critique_review_valid(self) -> None:
        r = self._critique()
        assert r.review_sub_state == ReviewSubState.CRITIQUE
        assert r.review_timestamp == self._ts
        assert r.review_commit is None

    def test_pr_review_name(self) -> None:
        assert self._pr().name == "002_pydantic_models_0e6baa9"

    def test_critique_review_name(self) -> None:
        assert self._critique().name == "002_pydantic_models_2026-03-18T14-30-00Z"

    def test_review_is_subclass_of_plan(self) -> None:
        assert isinstance(self._pr(), Plan)

    def test_review_state_defaults_to_review(self) -> None:
        assert self._pr().state == PlanState.REVIEW

    def _full(self, **kwargs: object) -> Review:
        return Review(
            sequence_number=[0],
            slug="",
            path=Path("reviewer/docs/plans/reviews/full/2026-03-18T14-30-00Z_0e6baa9.md"),
            review_sub_state=ReviewSubState.FULL,
            review_timestamp=self._ts,
            review_commit="0e6baa9",
            **kwargs,  # type: ignore[arg-type]
        )

    def test_full_review_valid(self) -> None:
        r = self._full()
        assert r.state == PlanState.REVIEW
        assert r.review_sub_state == ReviewSubState.FULL
        assert r.review_timestamp == self._ts
        assert r.review_commit == "0e6baa9"

    def test_full_review_name(self) -> None:
        assert self._full().name == "2026-03-18T14-30-00Z_0e6baa9"

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            pytest.param(
                {
                    "sequence_number": [2],
                    "slug": "pydantic_models",
                    "path": Path("reviewer/docs/plans/reviews/pr/002_pydantic_models.md"),
                    "review_sub_state": ReviewSubState.PR,
                },
                "PR review requires review_commit",
                id="pr_review_requires_commit",
            ),
            pytest.param(
                {
                    "sequence_number": [2],
                    "slug": "pydantic_models",
                    "path": Path("reviewer/docs/plans/reviews/critique/002_pydantic_models.md"),
                    "review_sub_state": ReviewSubState.CRITIQUE,
                },
                "CRITIQUE review requires review_timestamp",
                id="critique_review_requires_timestamp",
            ),
            pytest.param(
                {
                    "sequence_number": [0],
                    "slug": "",
                    "path": Path("reviewer/docs/plans/reviews/full/2026-03-18T14-30-00Z_0e6baa9.md"),
                    "review_sub_state": ReviewSubState.FULL,
                    "review_commit": "0e6baa9",
                },
                "FULL review requires review_timestamp",
                id="full_review_requires_timestamp",
            ),
            pytest.param(
                {
                    "sequence_number": [0],
                    "slug": "",
                    "path": Path("reviewer/docs/plans/reviews/full/2026-03-18T14-30-00Z_0e6baa9.md"),
                    "review_sub_state": ReviewSubState.FULL,
                    "review_timestamp": _ts,
                },
                "FULL review requires review_commit",
                id="full_review_requires_commit",
            ),
            pytest.param(
                {
                    "sequence_number": [2],
                    "slug": "pydantic_models",
                    "path": Path("reviewer/docs/plans/reviews/pr/002_pydantic_models_0e6baa9.md"),
                    "state": PlanState.CHECKING,
                    "review_sub_state": ReviewSubState.PR,
                    "review_commit": "0e6baa9",
                },
                r"Review\.state must be REVIEW",
                id="review_rejects_non_review_state",
            ),
        ],
    )
    def test_invalid_review_raises(self, kwargs: dict[str, object], match: str) -> None:
        with pytest.raises(ValidationError, match=match):
            Review(**kwargs)  # type: ignore[arg-type]


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


class TestPlanWorktree:
    @pytest.mark.parametrize(
        ("path", "worktree", "in_worktree"),
        [
            ("planner/docs/plans/planning/001_a.md", "planner", "docs/plans/planning/001_a.md"),
            ("canon/foo/docs/plans/todo/001_a.md", "canon", "foo/docs/plans/todo/001_a.md"),
            ("reviewer/docs/plans/reviews/pr/001_a_x.md", "reviewer", "docs/plans/reviews/pr/001_a_x.md"),
        ],
    )
    def test_splits_path(self, path: str, worktree: str, in_worktree: str) -> None:
        plan = Plan(sequence_number=[1], slug="a", state=PlanState.PLANNING, path=Path(path))
        assert plan.worktree == worktree
        assert plan.path_in_worktree == Path(in_worktree)

    @pytest.mark.parametrize("path", ["", "docs/plans/planning/001_a.md", "nobody/docs/001_a.md"])
    def test_prefixless_path_raises(self, path: str) -> None:
        plan = Plan(sequence_number=[1], slug="a", state=PlanState.PLANNING, path=Path(path))
        with pytest.raises(ValueError, match="worktree"):
            _ = plan.worktree
        with pytest.raises(ValueError, match="worktree"):
            _ = plan.path_in_worktree
