"""Tests for workflow/plan_resolver.py."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile
from unittest.mock import MagicMock, patch

import libtmux
import pytest

from workflow.audit import build_audit_command_repo_state, write_audit_log
from workflow.config import WorkflowConfig, load_config
from workflow.exceptions import AmbiguousPlanError, PlanCollisionError, PlanNotFoundError, WorkflowError
from workflow.models import AuditLogEntry, Plan, PlanState, Review, ReviewSubState
from workflow.names import RepoName
from workflow.plan_resolver import (
    _from_git_history,
    _from_tmux,
    _parse_git_log_output,
    find_all_plans,
    find_all_plans_in_repo,
    parse_plan_filename,
    parse_review_filename,
    resolve_plan,
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


def _make_config(tmp_path: Path) -> WorkflowConfig:
    """Build a workflow config rooted at tmp_path using the default YAML."""
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


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
    _ts_str = "2026-03-18T14-30-00Z"

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
            parse_review_filename("2026-03-18T14-30-00Z_0e6baa9", ReviewSubState.FULL)


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
        _make_review(tmp_path, "master", ReviewSubState.CRITIQUE, "002_plan_2026-03-18T14-30-00Z.md")
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
        _make_review(tmp_path, "master", ReviewSubState.FULL, "2026-03-18T14-30-00Z_0e6baa9.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert plans == []

    def test_malformed_filename_skipped(self, tmp_path: Path) -> None:
        # Files that don't match the NNN_slug pattern are silently ignored
        d = tmp_path / "master" / "docs" / "plans" / "planning"
        d.mkdir(parents=True)
        (d / "README.md").write_text("# readme\n")
        assert find_all_plans_in_repo(tmp_path, "master") == []

    def test_plan_is_not_review(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert isinstance(plans[0], Plan)
        assert not isinstance(plans[0], Review)

    def test_review_is_also_plan(self, tmp_path: Path) -> None:
        _make_review(tmp_path, "master", ReviewSubState.PR, "002_plan_0e6baa9.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert isinstance(plans[0], Plan)

    def test_path_is_relative_to_project_dir(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        plans = find_all_plans_in_repo(tmp_path, "master")
        assert plans[0].path == Path("master/docs/plans/intent/001_idea.md")


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
        _make_review(tmp_path, "planner", ReviewSubState.CRITIQUE, "002_plan_2026-03-18T14-30-00Z.md")
        _make_review(tmp_path, "reviewer", ReviewSubState.CRITIQUE, "002_plan_2026-03-19T10-00-00Z.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 2

    def test_validate_planner_and_reviewer_different_states(self, tmp_path: Path) -> None:
        # Plan in planner at CHECKING, same plan in reviewer at CHECKING too
        # (same slug + seq): find_all_plans keeps one (first wins on tie)
        # resolve_plan would raise AmbiguousPlanError, but that's not implemented yet
        _make_plan(tmp_path, "planner", PlanState.CHECKING, "002_foo.md")
        _make_plan(tmp_path, "reviewer", PlanState.CHECKING, "002_foo.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 1

    def test_dedup_rejected_beats_all(self, tmp_path: Path) -> None:
        # REJECTED has the highest rank and wins over any other state copy,
        # even if that copy is at a later pipeline stage in another repo.
        _make_plan(tmp_path, "master", PlanState.REJECTED, "002_foo.md")
        _make_plan(tmp_path, "builder", PlanState.IMPLEMENTING, "002_foo.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 1
        assert plans[0].state == PlanState.REJECTED

    def test_all_four_repos_combined(self, tmp_path: Path) -> None:
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "builder", PlanState.IMPLEMENTING, "003_bar.md")
        _make_plan(tmp_path, "reviewer", PlanState.CHECKING, "004_baz.md")
        plans = find_all_plans(tmp_path)
        assert len(plans) == 4
        assert {p.slug for p in plans} == {"idea", "foo", "bar", "baz"}


# ---------------------------------------------------------------------------
# resolve_plan
# ---------------------------------------------------------------------------


class TestResolvePlan:
    """End-to-end coverage of `resolve_plan` with a real config and tmp filesystem."""

    # --- fuzzy name forms ---

    def test_seq_only(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plan = resolve_plan("002", tmp_path, config=config)
        assert plan.slug == "pydantic_models"

    def test_name_only(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plan = resolve_plan("002_pydantic_models", tmp_path, config=config)
        assert plan.slug == "pydantic_models"

    def test_name_with_md_suffix(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plan = resolve_plan("002_pydantic_models.md", tmp_path, config=config)
        assert plan.slug == "pydantic_models"

    def test_state_and_name(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plan = resolve_plan("planning/002_pydantic_models", tmp_path, config=config)
        assert plan.state == PlanState.PLANNING

    def test_state_and_seq(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_idea.md")
        plan = resolve_plan("intent/001", tmp_path, config=config)
        assert plan.slug == "idea"

    def test_docs_plans_prefix(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plan = resolve_plan("docs/plans/planning/002_pydantic_models", tmp_path, config=config)
        assert plan.slug == "pydantic_models"

    def test_repo_prefix_in_path(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "002_foo.md")
        plan = resolve_plan("planner/docs/plans/planning/002_foo", tmp_path, config=config)
        assert plan.slug == "foo"

    def test_hierarchical_seq(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.TODO, "003.1_subtask.md")
        plan = resolve_plan("003.1", tmp_path, config=config)
        assert plan.slug == "subtask"
        assert plan.sequence_number == [3, 1]

    # --- subcommand constraint ---

    def test_subcommand_narrows_to_repo_and_state(self, tmp_path: Path) -> None:
        # 'check' is a script in implementing state with transition: checking and agent: builder.
        # A plan with the same seq exists in planner/planning AND builder/checking — the
        # subcommand picks the latter.
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "005_foo.md")
        _make_plan(tmp_path, "builder", PlanState.CHECKING, "005_foo.md")
        plan = resolve_plan("005", tmp_path, config=config, command_name="check")
        assert plan.path.parts[0] == "builder"
        assert plan.state == PlanState.CHECKING

    def test_subcommand_disagreement_raises_workflow_error(self, tmp_path: Path) -> None:
        # 'check' wants builder/checking, but the explicit path says planner — empty intersection.
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "005_foo.md")
        with pytest.raises(WorkflowError, match="Sources disagree"):
            resolve_plan("planner/005", tmp_path, config=config, command_name="check")

    # --- disambiguation ---

    def test_intent_vs_non_intent_prefers_non_intent(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.INTENT, "001_foo.md")
        _make_plan(tmp_path, "master", PlanState.PLANNING, "001_foo.md")
        plan = resolve_plan("001", tmp_path, config=config)
        assert plan.state == PlanState.PLANNING

    def test_two_non_intent_same_seq_ambiguous(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "master", PlanState.IMPLEMENTING, "002_foo.md")
        with pytest.raises(AmbiguousPlanError) as exc_info:
            resolve_plan("002", tmp_path, config=config)
        assert len(exc_info.value.candidates) == 2

    # --- error cases ---

    def test_not_found_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with pytest.raises(PlanNotFoundError) as exc_info:
            resolve_plan("999", tmp_path, config=config)
        assert "999" in exc_info.value.fuzzy_path

    def test_collision_propagates(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "002_foo.md")
        _make_plan(tmp_path, "master", PlanState.IMPLEMENTING, "002_bar.md")
        with pytest.raises(PlanCollisionError):
            resolve_plan("002", tmp_path, config=config)

    def test_relative_review_path_not_supported(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_review(tmp_path, "master", ReviewSubState.PR, "002_plan_0e6baa9.md")
        with pytest.raises(PlanNotFoundError):
            resolve_plan("reviews/pr/002_plan_0e6baa9", tmp_path, config=config)

    # --- absolute path ---

    def test_absolute_path_regular_plan(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        f = _make_plan(tmp_path, "master", PlanState.PLANNING, "002_pydantic_models.md")
        plan = resolve_plan(str(f), tmp_path, config=config)
        assert plan.slug == "pydantic_models"
        assert plan.state == PlanState.PLANNING
        assert plan.path == Path("master/docs/plans/planning/002_pydantic_models.md")

    def test_absolute_path_review(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        f = _make_review(tmp_path, "master", ReviewSubState.PR, "002_plan_0e6baa9.md")
        plan = resolve_plan(str(f), tmp_path, config=config)
        assert isinstance(plan, Review)
        assert plan.review_commit == "0e6baa9"

    def test_absolute_path_outside_project_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        outside = tmp_path.parent / "outside.md"
        outside.write_text("# outside\n")
        with pytest.raises(PlanNotFoundError):
            resolve_plan(str(outside), tmp_path, config=config)

    def test_absolute_path_nonexistent_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        missing = tmp_path / "master" / "docs" / "plans" / "planning" / "999_missing.md"
        with pytest.raises(PlanNotFoundError):
            resolve_plan(str(missing), tmp_path, config=config)

    def test_absolute_path_full_review_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        f = _make_review(tmp_path, "master", ReviewSubState.FULL, "2026-03-18T14-30-00Z_0e6baa9.md")
        with pytest.raises(PlanNotFoundError):
            resolve_plan(str(f), tmp_path, config=config)


# ---------------------------------------------------------------------------
# Multi-source helpers and integration with audit log
# ---------------------------------------------------------------------------


class TestAuditSource:
    """The audit-log source narrows resolution to the (repo, state) of the latest mention."""

    def test_audit_log_picks_most_recent_state(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        # Plan 005 exists in two locations; audit log says it was last 'check'-ed,
        # which puts it in reviewer/checking (§3.2 mapping fix).
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "005_foo.md")
        _make_plan(tmp_path, "reviewer", PlanState.CHECKING, "005_foo.md")
        write_audit_log(
            tmp_path,
            AuditLogEntry(
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                actor="reviewer",
                command="check",
                input_plan="005_foo",
            ),
        )
        plan = resolve_plan("005", tmp_path, config=config)
        assert plan.path.parts[0] == "reviewer"
        assert plan.state == PlanState.CHECKING

    def test_audit_log_ignores_unrelated_seq(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.PLANNING, "005_foo.md")
        write_audit_log(
            tmp_path,
            AuditLogEntry(
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                actor="builder",
                command="check",
                input_plan="999_bar",
            ),
        )
        plan = resolve_plan("005", tmp_path, config=config)
        assert plan.path.parts[0] == "master"
        assert plan.state == PlanState.PLANNING

    def test_audit_state_widens_to_forward_progression(self, tmp_path: Path) -> None:
        # The `build` prompt instructs the builder agent to move the plan from
        # implementing/ to checking/ without writing an audit entry. The audit
        # log therefore lags the actual state: its `implement` entry says
        # implementing, but the plan on disk is in checking. The resolver must
        # widen the audit's state to {implementing, …, rejected} so the actual
        # current state is still in the intersection.
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "builder", PlanState.CHECKING, "005_foo.md")
        write_audit_log(
            tmp_path,
            AuditLogEntry(
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                actor="human",
                command="implement",
                input_plan="005_foo",
            ),
        )
        plan = resolve_plan("005", tmp_path, config=config, command_name="check")
        assert plan.path.parts[0] == "builder"
        assert plan.state == PlanState.CHECKING


class TestSubcommandSource:
    """The subcommand source contributes (repos, states) from where the command appears."""

    def test_prompt_command_contributes_repos_only(self, tmp_path: Path) -> None:
        # 'ask' is a prompt — it contributes repos but no state constraint, so a
        # planning-state plan in planner is still resolvable under 'ask'.
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "004_cli.md")
        plan = resolve_plan("004", tmp_path, config=config, command_name="ask")
        assert plan.state == PlanState.PLANNING
        assert plan.path.parts[0] == "planner"

    def test_script_with_repo_override(self, tmp_path: Path) -> None:
        # 'push' has repo_override=master in both intent and todo. Plan exists in
        # master/intent, and a same-seq sibling in planner/planning would be
        # excluded by the master-only soft constraint.
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.INTENT, "005_quux.md")
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "005_quux.md")
        plan = resolve_plan("005", tmp_path, config=config, command_name="push")
        assert plan.path.parts[0] == "master"
        assert plan.state == PlanState.INTENT

    def test_also_valid_in_states_allows_planning_plan_for_implement(self, tmp_path: Path) -> None:
        """Implement has also_valid_in_states=[planning], so a planning-state plan resolves."""
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "007_feature.md")
        plan = resolve_plan("007", tmp_path, config=config, command_name="implement")
        assert plan.state == PlanState.PLANNING
        assert plan.path.parts[0] == "planner"


# ---------------------------------------------------------------------------
# §3.2 Audit mapping fix for 'check'
# ---------------------------------------------------------------------------


class TestAuditCheckMapping:
    """check now maps to (reviewer, checking) so accept resolution works."""

    def test_check_audit_entry_resolves_with_accept(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "reviewer", PlanState.CHECKING, "005_foo.md")
        write_audit_log(
            tmp_path,
            AuditLogEntry(
                timestamp=datetime(2026, 4, 1, tzinfo=UTC),
                actor="reviewer",
                command="check",
                input_plan="005_foo",
            ),
        )
        # Previously failed: audit said (builder, checking) but accept expects reviewer.
        plan = resolve_plan("005", tmp_path, config=config, command_name="accept")
        assert plan.path.parts[0] == "reviewer"
        assert plan.state == PlanState.CHECKING


# ---------------------------------------------------------------------------
# §3.3 Direct unit tests for _parse_git_log_output
# ---------------------------------------------------------------------------


class TestParseGitLogOutput:
    def test_rename_line_returns_new_path_state(self) -> None:
        output = "abc123 1700000000\nR100\tdocs/plans/planning/005_foo.md\tdocs/plans/implementing/005_foo.md"
        result = _parse_git_log_output(output)
        assert result is not None
        assert result.timestamp == 1700000000
        assert result.state == PlanState.IMPLEMENTING

    def test_add_line_returns_state(self) -> None:
        output = "abc123 1700000001\nA\tdocs/plans/todo/005_foo.md"
        result = _parse_git_log_output(output)
        assert result is not None
        assert result.state == PlanState.TODO

    def test_modify_line_returns_state(self) -> None:
        output = "abc123 1700000002\nM\tdocs/plans/checking/005_foo.md"
        result = _parse_git_log_output(output)
        assert result is not None
        assert result.state == PlanState.CHECKING

    def test_delete_then_add_same_basename_returns_add_path(self) -> None:
        output = "abc123 1700000003\nD\tdocs/plans/planning/005_foo.md\nA\tdocs/plans/implementing/005_foo.md"
        result = _parse_git_log_output(output)
        assert result is not None
        assert result.state == PlanState.IMPLEMENTING

    def test_malformed_header_returns_none(self) -> None:
        output = "not-a-hash-no-timestamp\nA\tdocs/plans/todo/005_foo.md"
        result = _parse_git_log_output(output)
        assert result is None

    def test_empty_output_returns_none(self) -> None:
        assert _parse_git_log_output("") is None
        assert _parse_git_log_output("   \n  ") is None

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
    def test_integration_real_git_repo(self, tmp_path: Path) -> None:
        """Create a real git repo, commit plan files, assert resolver tracks state."""
        repo = tmp_path / "master"
        repo.mkdir()
        git = shutil.which("git") or "git"
        subprocess.run([git, "init", str(repo)], check=True, capture_output=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [git, "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run([git, "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)  # noqa: S603
        # Commit initial plan in planning/
        plan_dir = repo / "docs" / "plans" / "planning"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "005_foo.md"
        plan_file.write_text("# plan\n")
        subprocess.run([git, "add", "."], cwd=repo, check=True, capture_output=True)  # noqa: S603
        subprocess.run([git, "commit", "-m", "add plan"], cwd=repo, check=True, capture_output=True)  # noqa: S603
        # Move to implementing/
        impl_dir = repo / "docs" / "plans" / "implementing"
        impl_dir.mkdir()
        subprocess.run(  # noqa: S603
            [git, "mv", "docs/plans/planning/005_foo.md", "docs/plans/implementing/005_foo.md"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603
            [git, "commit", "-m", "move to implementing"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        # Create a minimal project around this repo
        (tmp_path / ".workflow_root").touch()
        config = _make_config(tmp_path)
        plan = resolve_plan("005", tmp_path, config=config)
        assert plan.state == PlanState.IMPLEMENTING


# ---------------------------------------------------------------------------
# §3.4 Direct unit tests for _from_tmux / _active_tmux_repo
# ---------------------------------------------------------------------------


class TestTmuxSource:
    def _make_session(self, window_name: str) -> MagicMock:
        window = MagicMock()
        window.name = window_name
        session = MagicMock()
        session.active_window = window
        return session

    def test_server_not_alive_returns_none(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        server = MagicMock()
        server.is_alive.return_value = False
        with patch("workflow.plan_resolver.libtmux.Server", return_value=server):
            constraint = _from_tmux(config)
        assert constraint.repos is None

    def test_session_not_found_returns_none(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        server = MagicMock()
        server.is_alive.return_value = True
        server.sessions.get.return_value = None
        with patch("workflow.plan_resolver.libtmux.Server", return_value=server):
            constraint = _from_tmux(config)
        assert constraint.repos is None

    def test_window_name_not_a_repo_returns_none(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        server = MagicMock()
        server.is_alive.return_value = True
        server.sessions.get.return_value = self._make_session("random-window")
        with patch("workflow.plan_resolver.libtmux.Server", return_value=server):
            constraint = _from_tmux(config)
        assert constraint.repos is None

    def test_builder_window_returns_builder_and_master(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        server = MagicMock()
        server.is_alive.return_value = True
        server.sessions.get.return_value = self._make_session("builder")
        with patch("workflow.plan_resolver.libtmux.Server", return_value=server):
            constraint = _from_tmux(config)
        assert constraint.repos == frozenset({RepoName.builder, RepoName.master})

    def test_master_window_returns_master_only(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        server = MagicMock()
        server.is_alive.return_value = True
        server.sessions.get.return_value = self._make_session("master")
        with patch("workflow.plan_resolver.libtmux.Server", return_value=server):
            constraint = _from_tmux(config)
        assert constraint.repos == frozenset({RepoName.master})

    def test_server_raises_oserror_returns_none(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch("workflow.plan_resolver.libtmux.Server", side_effect=OSError("no server")):
            constraint = _from_tmux(config)
        assert constraint.repos is None

    def test_server_raises_libtmux_exception_returns_none(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch("workflow.plan_resolver.libtmux.Server", side_effect=libtmux.exc.LibTmuxException("err")):
            constraint = _from_tmux(config)
        assert constraint.repos is None


# ---------------------------------------------------------------------------
# §3.5 Fast-path enforcement: fully-qualified path skips git/tmux sources
# ---------------------------------------------------------------------------


class TestFastPathSkipsExpensiveSources:
    def test_absolute_like_path_skips_git_and_tmux(self, tmp_path: Path) -> None:
        """A path with explicit repo+state already fixed → git/tmux never called."""
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "005_foo.md")

        def _raise(*_args: object, **_kwargs: object) -> object:
            msg = "expensive source should not be called"
            raise AssertionError(msg)

        with (
            patch("workflow.plan_resolver._from_git_history", side_effect=_raise),
            patch("workflow.plan_resolver._from_tmux", side_effect=_raise),
        ):
            plan = resolve_plan("planner/planning/005", tmp_path, config=config)
        assert plan.state == PlanState.PLANNING
        assert plan.path.parts[0] == "planner"


# ---------------------------------------------------------------------------
# §3.6 Strengthen union-report message shape
# ---------------------------------------------------------------------------


class TestUnionReport:
    def test_disagreement_message_includes_source_labels(self, tmp_path: Path) -> None:
        """The disagreement error message includes per-source labels (path, subcommand)."""
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "planner", PlanState.PLANNING, "005_foo.md")
        with pytest.raises(WorkflowError) as exc_info:
            resolve_plan("planner/005", tmp_path, config=config, command_name="check")
        msg = exc_info.value.args[0]
        assert "Sources disagree" in msg
        assert ": path" in msg
        assert ": subcommand" in msg
        # Should list at least one cell with a repo/state combination
        assert "docs/plans/" in msg


# ---------------------------------------------------------------------------
# §4a Git source: provides_repo flag (opt-in repo constraint)
# ---------------------------------------------------------------------------


def _init_git_repo(repo: Path) -> None:
    """Initialise a git repo at *repo* with a fixed identity (test helper)."""
    git = shutil.which("git") or "git"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run([git, "init", str(repo)], check=True, capture_output=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [git, "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run([git, "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)  # noqa: S603


def _commit_plan(repo: Path, state: PlanState, name: str) -> None:
    """Add a plan file under *repo*/docs/plans/<state>/ and commit it (test helper)."""
    git = shutil.which("git") or "git"
    plan_dir = repo / "docs" / "plans" / state.value
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / name).write_text("# plan\n")
    subprocess.run([git, "add", "."], cwd=repo, check=True, capture_output=True)  # noqa: S603
    subprocess.run([git, "commit", "-m", f"add {name}"], cwd=repo, check=True, capture_output=True)  # noqa: S603


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
class TestGitProvidesRepoFlag:
    """_from_git_history contributes a repo only when provides_repo=True."""

    def test_default_returns_state_only(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path / "master")
        _commit_plan(tmp_path / "master", PlanState.IMPLEMENTING, "005_foo.md")
        constraint = _from_git_history(tmp_path, [5], "foo")
        assert constraint.repos is None
        assert constraint.states == frozenset({PlanState.IMPLEMENTING})

    def test_provides_repo_true_returns_repo_and_state(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path / "master")
        _commit_plan(tmp_path / "master", PlanState.IMPLEMENTING, "005_foo.md")
        constraint = _from_git_history(tmp_path, [5], "foo", provides_repo=True)
        assert constraint.repos == frozenset({RepoName.master})
        assert constraint.states == frozenset({PlanState.IMPLEMENTING})

    def test_ties_break_to_more_progressed_state(self, tmp_path: Path) -> None:
        # Both repos commit at the same %ct (one-second resolution); the more
        # progressed state (CHECKING > IMPLEMENTING) wins because tickets only
        # ever move forward.
        git = shutil.which("git") or "git"
        _init_git_repo(tmp_path / "master")
        _init_git_repo(tmp_path / "builder")
        env = {**os.environ, "GIT_COMMITTER_DATE": "1700000000 +0000", "GIT_AUTHOR_DATE": "1700000000 +0000"}
        for repo, state in ((tmp_path / "master", PlanState.IMPLEMENTING), (tmp_path / "builder", PlanState.CHECKING)):
            plan_dir = repo / "docs" / "plans" / state.value
            plan_dir.mkdir(parents=True, exist_ok=True)
            (plan_dir / "005_foo.md").write_text("# plan\n")
            subprocess.run([git, "add", "."], cwd=repo, check=True, capture_output=True)  # noqa: S603
            subprocess.run(  # noqa: S603
                [git, "commit", "-m", f"add {state.value}"],
                cwd=repo,
                check=True,
                capture_output=True,
                env=env,
            )
        constraint = _from_git_history(tmp_path, [5], "foo")
        assert constraint.states == frozenset({PlanState.CHECKING})


class TestResolvePlanForwardsGitProvidesRepo:
    """resolve_plan forwards the git_provides_repo flag to _from_git_history."""

    def test_default_is_false(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.TODO, "005_foo.md")
        with patch("workflow.plan_resolver._from_git_history", wraps=_from_git_history) as mock:
            resolve_plan("005", tmp_path, config=config)
        assert mock.call_args.kwargs["provides_repo"] is False

    def test_true_when_requested(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _make_plan(tmp_path, "master", PlanState.TODO, "005_foo.md")
        with patch("workflow.plan_resolver._from_git_history", wraps=_from_git_history) as mock:
            resolve_plan("005", tmp_path, config=config, git_provides_repo=True)
        assert mock.call_args.kwargs["provides_repo"] is True


# ---------------------------------------------------------------------------
# §4b Audit mapping is derived from the config, not hardcoded
# ---------------------------------------------------------------------------


class TestAuditCommandRepoStateBuilder:
    """build_audit_command_repo_state(config) reflects each command's transition target."""

    def test_transition_commands_map_to_target_state_and_owning_agent(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        mapping = build_audit_command_repo_state(config)
        assert mapping["plan"] == (RepoName.planner, PlanState.PLANNING)
        assert mapping["subdivide"] == (RepoName.planner, PlanState.PLANNING)
        assert mapping["todo"] == (RepoName.master, PlanState.TODO)
        assert mapping["implement"] == (RepoName.builder, PlanState.IMPLEMENTING)
        assert mapping["check"] == (RepoName.reviewer, PlanState.CHECKING)
        assert mapping["accept"] == (RepoName.master, PlanState.DONE)
        assert mapping["reject"] == (RepoName.master, PlanState.REJECTED)

    def test_commands_without_transition_are_absent(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        mapping = build_audit_command_repo_state(config)
        for cmd in ("new", "push", "ask", "build", "critique", "pr", "validate"):
            assert cmd not in mapping
