"""Tests for workflow/plan_resolver.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - used at runtime in helper functions

import pytest

from workflow.exceptions import PlanCollisionError
from workflow.models import Plan, PlanState, Review, ReviewSubState
from workflow.plan_resolver import (
    find_all_plans,
    find_all_plans_in_repo,
    parse_plan_filename,
    parse_review_filename,
)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _make_plan(root: Path, repo: str, state: PlanState, filename: str) -> Path:
    """Create a stub plan markdown file and return its path."""
    d = root / repo / "docs" / "plans" / state.value
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(f"# {filename}\n")
    return p


def _make_review(root: Path, repo: str, sub_state: ReviewSubState, filename: str) -> Path:
    """Create a stub review markdown file and return its path."""
    d = root / repo / "docs" / "plans" / "reviews" / sub_state.value
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(f"# {filename}\n")
    return p


# ---------------------------------------------------------------------------
# parse_plan_filename
# ---------------------------------------------------------------------------


class TestParsePlanFilename:
    def test_simple(self) -> None:
        seq, slug = parse_plan_filename("002_pydantic_models")
        assert seq == [2]
        assert slug == "pydantic_models"

    def test_slug_with_underscores(self) -> None:
        seq, slug = parse_plan_filename("003_foo_bar_baz")
        assert seq == [3]
        assert slug == "foo_bar_baz"

    def test_hierarchical_sequence_number(self) -> None:
        seq, slug = parse_plan_filename("012.1_impl")
        assert seq == [12, 1]
        assert slug == "impl"

    def test_zero_padded_sequence_number(self) -> None:
        seq, slug = parse_plan_filename("001_idea")
        assert seq == [1]
        assert slug == "idea"

    def test_missing_underscore_raises(self) -> None:
        with pytest.raises(ValueError, match="not enough values to unpack"):
            parse_plan_filename("nounderscore")


# ---------------------------------------------------------------------------
# parse_review_filename
# ---------------------------------------------------------------------------


class TestParseReviewFilename:
    _ts = datetime(2026, 3, 18, 14, 30, tzinfo=UTC)
    _ts_str = "2026-03-18T14:30:00Z"

    def test_critique_simple(self) -> None:
        seq, slug, ts, commit = parse_review_filename(f"002_pydantic_models_{self._ts_str}", ReviewSubState.CRITIQUE)
        assert seq == [2]
        assert slug == "pydantic_models"
        assert ts == self._ts
        assert commit is None

    def test_critique_slug_with_underscores(self) -> None:
        seq, slug, ts, _commit = parse_review_filename(f"003_foo_bar_{self._ts_str}", ReviewSubState.CRITIQUE)
        assert seq == [3]
        assert slug == "foo_bar"
        assert ts == self._ts

    def test_pr_simple(self) -> None:
        seq, slug, ts, commit = parse_review_filename("002_pydantic_models_0e6baa9", ReviewSubState.PR)
        assert seq == [2]
        assert slug == "pydantic_models"
        assert ts is None
        assert commit == "0e6baa9"

    def test_pr_slug_with_underscores(self) -> None:
        seq, slug, _ts, commit = parse_review_filename("003_foo_bar_0e6baa9", ReviewSubState.PR)
        assert seq == [3]
        assert slug == "foo_bar"
        assert commit == "0e6baa9"

    def test_critique_invalid_timestamp_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid critique timestamp"):
            parse_review_filename("002_plan_NOTADATE", ReviewSubState.CRITIQUE)

    def test_pr_invalid_commit_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid PR commit hash"):
            parse_review_filename("002_plan_ZZZZZZZZ", ReviewSubState.PR)

    def test_full_raises(self) -> None:
        with pytest.raises(ValueError, match="Full reviews"):
            parse_review_filename("2026-03-18T14:30:00Z_0e6baa9", ReviewSubState.FULL)


# ---------------------------------------------------------------------------
# find_all_plans_in_repo
# ---------------------------------------------------------------------------


class TestFindAllPlansInRepo:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "master" / "docs" / "plans").mkdir(parents=True)
        assert find_all_plans_in_repo(tmp_path, "master") == []

    def test_missing_repo_returns_empty(self, tmp_path: Path) -> None:
        assert find_all_plans_in_repo(tmp_path, "master") == []

    def test_single_intent_plan(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert len(plans) == 1
        assert plans[0].slug == "idea"
        assert plans[0].state == PlanState.INTENT
        assert plans[0].sequence_number == [1]

    def test_multiple_states(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert len(plans) == 2
        assert {p.slug for p in plans} == {"idea", "pydantic_models"}

    def test_collision_raises(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "master", PlanState.IMPLEMENTING, "002_bar.md")
        with pytest.raises(PlanCollisionError) as exc_info:
            find_all_plans_in_repo(tmp_path, "master")
        assert exc_info.value.sequence_number == "002"
        assert len(exc_info.value.colliding) == 2

    def test_same_slug_different_states_no_collision(self, tmp_path: Path) -> None:
        # Same plan progressed — appears in two state dirs with same slug: not a collision
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "master", PlanState.IMPLEMENTING, "002_foo.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert len(plans) == 2

    def test_intent_plans_never_collide(self, tmp_path: Path) -> None:
        # Intent plans are excluded from collision detection
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea_a.md")
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea_b.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert len(plans) == 2

    def test_critique_review_included(self, tmp_path: Path) -> None:
        _make_review(tmp_path, "master", ReviewSubState.CRITIQUE, "002_plan_2026-03-18T14:30:00Z.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert len(plans) == 1
        assert isinstance(plans[0], Review)
        assert plans[0].review_sub_state == ReviewSubState.CRITIQUE

    def test_pr_review_included(self, tmp_path: Path) -> None:
        _make_review(tmp_path, "master", ReviewSubState.PR, "002_plan_0e6baa9.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert len(plans) == 1
        assert isinstance(plans[0], Review)
        assert plans[0].review_commit == "0e6baa9"

    def test_full_review_skipped(self, tmp_path: Path) -> None:
        _make_review(tmp_path, "master", ReviewSubState.FULL, "2026-03-18T14:30:00Z_0e6baa9.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert plans == []

    def test_plan_is_not_review(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert isinstance(plans[0], Plan)
        assert not isinstance(plans[0], Review)

    def test_review_is_also_plan(self, tmp_path: Path) -> None:
        _make_review(tmp_path, "master", ReviewSubState.PR, "002_plan_0e6baa9.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert isinstance(plans[0], Plan)

    def test_path_is_absolute(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert plans[0].path.is_absolute()


# ---------------------------------------------------------------------------
# find_all_plans
# ---------------------------------------------------------------------------


class TestFindAllPlans:
    def test_single_repo(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 1
        assert plans[0].slug == "pydantic_models"

    def test_missing_repos_skipped(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        # planner, builder, reviewer dirs do not exist
        plans = find_all_plans(tmp_path)
        assert len(plans) == 1

    def test_dedup_prefers_most_progressed_state(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "builder", PlanState.IMPLEMENTING, "002_foo.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 1
        assert plans[0].state == PlanState.IMPLEMENTING

    def test_dedup_keeps_first_on_equal_state(self, tmp_path: Path) -> None:
        # Same plan at same state in two repos: first repo in _REPOS order wins silently
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "002_foo.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 1

    def test_reviews_not_deduplicated(self, tmp_path: Path) -> None:
        # Different timestamps → different names → both included
        _make_review(tmp_path, "planner", ReviewSubState.CRITIQUE, "002_plan_2026-03-18T14:30:00Z.md")
        _make_review(tmp_path, "reviewer", ReviewSubState.CRITIQUE, "002_plan_2026-03-19T10:00:00Z.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 2

    def test_doublecheck_planner_and_reviewer_different_states(self, tmp_path: Path) -> None:
        # Plan in planner at CHECKING, same plan in reviewer at CHECKING too
        # (same slug + seq): find_all_plans keeps one (first wins on tie)
        # resolve_plan would raise AmbiguousPlanError, but that's not implemented yet
        _make_plan(tmp_path, "planner", PlanState.CHECKING, "002_foo.md")
        _make_plan(tmp_path, "reviewer", PlanState.CHECKING, "002_foo.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 1

    def test_all_four_repos_combined(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "builder", PlanState.IMPLEMENTING, "003_bar.md")
        _make_plan(tmp_path, "reviewer", PlanState.CHECKING, "004_baz.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 4
        assert {p.slug for p in plans} == {"idea", "foo", "bar", "baz"}
