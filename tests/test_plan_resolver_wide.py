"""Wide end-to-end matrix test for ``resolve_plan``.

Exercises every plan state, both prompt and script subcommands, and all
four resolver sources (subcommand, audit, git history, tmux) in a single
parametrized test driven by a sequence-number-only fuzzy input.
Companion to the focused per-source tests in ``tests/test_plan_resolver.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import copyfile
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ConfigDict

from tests.conftest import create_project_marker
from workflow.audit import write_audit_log
from workflow.config import WorkflowConfig, load_config
from workflow.exceptions import AmbiguousPlanError
from workflow.models import AuditLogEntry, PlanState
from workflow.names import WorktreeName
from workflow.plan_resolver import SourcesDisagreeError, resolve_plan

# ---------------------------------------------------------------------------
# Tmux silence (function-scoped, autouse): guarantees a dead libtmux server
# regardless of the developer's environment. The new file does not patch
# tmux per-test, so a single module-wide silencer is sufficient.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _silence_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = MagicMock()
    dead.is_alive.return_value = False
    monkeypatch.setattr("workflow.tmux_session.libtmux.Server", lambda: dead)


# ---------------------------------------------------------------------------
# Scenario / case models
# ---------------------------------------------------------------------------


class _AuditDraft(BaseModel):
    """Audit entry without a timestamp; ``_build_world`` assigns one."""

    model_config = ConfigDict(frozen=True)
    actor: str
    command: str
    input_plan: str | None = None
    output_plans: list[str] = []


class PlanScenario(BaseModel):
    """One plan's footprint in the test world."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    plan_id: str
    on_disk: list[tuple[WorktreeName, PlanState]]
    audit_entries: list[_AuditDraft] = []
    commit_in: list[WorktreeName] = []


class ResolverCase(BaseModel):
    """One parametrize row driving ``resolve_plan``."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    case_id: str
    plan_id: str
    command: str | None = None
    fuzzy_input: str | None = None
    git_provides_worktree: bool = False
    expected_repo: WorktreeName | None = None
    expected_state: PlanState | None = None
    expected_error: type[Exception] | None = None


# ---------------------------------------------------------------------------
# World builder
# ---------------------------------------------------------------------------

_GIT_BASE_TS = 1_700_000_000  # start of synthetic commit timestamps (Unix epoch)
_AUDIT_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _git(repo_path: Path, *args: str, env: dict[str, str] | None = None) -> None:
    """Run ``git`` inside *repo_path*, raising on non-zero exit."""
    git = shutil.which("git") or "git"
    subprocess.run([git, "-C", str(repo_path), *args], check=True, capture_output=True, env=env)  # noqa: S603


def _ensure_git_init(repo_path: Path) -> None:
    if (repo_path / ".git").exists():
        return
    git = shutil.which("git") or "git"
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run([git, "init", "-q", "-b", "main", str(repo_path)], check=True, capture_output=True)  # noqa: S603
    _git(repo_path, "config", "user.email", "test@example.com")
    _git(repo_path, "config", "user.name", "Test")


def _commit_at(repo_path: Path, ts: int, message: str) -> None:
    _ensure_git_init(repo_path)
    _git(repo_path, "add", "-A")
    env = {**os.environ, "GIT_COMMITTER_DATE": f"{ts} +0000", "GIT_AUTHOR_DATE": f"{ts} +0000"}
    _git(repo_path, "commit", "-q", "-m", message, env=env)


def _build_world(world: Path, scenarios: list[PlanScenario]) -> None:
    """Materialise *scenarios* into *world* (filesystem + git + audit log).

    Each scenario's markdown files are written first, then committed in the
    order specified by ``commit_in`` (later commits get a strictly newer
    ``%ct``). Audit entries are appended with monotonically increasing
    timestamps so ``_from_audit_log``'s "most recent" lookup is stable.
    """
    git_ts = _GIT_BASE_TS
    audit_step = 0
    for scenario in scenarios:
        for repo, state in scenario.on_disk:
            d = world / repo.value / "docs" / "plans" / state.value
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{scenario.plan_id}.md").write_text(f"# {scenario.plan_id}\n")
        for repo in scenario.commit_in:
            git_ts += 1
            _commit_at(world / repo.value, git_ts, f"{scenario.plan_id} in {repo.value}")
        for draft in scenario.audit_entries:
            write_audit_log(
                world,
                AuditLogEntry(
                    timestamp=_AUDIT_BASE + timedelta(seconds=audit_step),
                    actor=draft.actor,
                    command=draft.command,
                    input_plan=draft.input_plan,
                    output_plans=draft.output_plans,
                ),
            )
            audit_step += 1


# ---------------------------------------------------------------------------
# Scenario matrix: synced older plans (in every clone the pipeline reaches)
# and WIP plans (one canonical clone, optional stale copies). See plan 015.
# ---------------------------------------------------------------------------

_ALL_REPOS: list[WorktreeName] = [WorktreeName.canon, WorktreeName.planner, WorktreeName.builder, WorktreeName.reviewer]

_SCENARIOS: list[PlanScenario] = [
    # Synced older plans -----------------------------------------------------
    PlanScenario(
        plan_id="001_idea",
        on_disk=[(r, PlanState.INTENT) for r in _ALL_REPOS],
        # `new` and `push` have no audit mapping → audit source stays silent.
        audit_entries=[
            _AuditDraft(actor="human", command="new", input_plan="001_idea"),
            _AuditDraft(actor="human", command="push", input_plan="001_idea"),
        ],
        commit_in=_ALL_REPOS,
    ),
    PlanScenario(
        plan_id="002_bad_idea",
        on_disk=[(r, PlanState.REJECTED) for r in _ALL_REPOS],
        audit_entries=[_AuditDraft(actor="human", command="reject", input_plan="002_bad_idea")],
        commit_in=_ALL_REPOS,
    ),
    PlanScenario(
        plan_id="003_early",
        on_disk=[(r, PlanState.DONE) for r in _ALL_REPOS],
        audit_entries=[_AuditDraft(actor="human", command="accept", input_plan="003_early")],
        commit_in=_ALL_REPOS,
    ),
    PlanScenario(
        plan_id="004_flawed",
        on_disk=[(WorktreeName.builder, PlanState.CHECKING), (WorktreeName.reviewer, PlanState.CHECKING)],
        audit_entries=[_AuditDraft(actor="reviewer", command="check", input_plan="004_flawed")],
        commit_in=[WorktreeName.builder, WorktreeName.reviewer],
    ),
    PlanScenario(
        plan_id="005_next_in_line",
        on_disk=[(WorktreeName.canon, PlanState.TODO)],
        audit_entries=[_AuditDraft(actor="human", command="todo", input_plan="005_next_in_line")],
        commit_in=[WorktreeName.canon],
    ),
    PlanScenario(
        plan_id="006_draft",
        on_disk=[(WorktreeName.planner, PlanState.PLANNING)],
        audit_entries=[_AuditDraft(actor="planner", command="plan", input_plan="006_draft")],
        commit_in=[WorktreeName.planner],
    ),
    # WIP plans --------------------------------------------------------------
    # Row 15: canon is the canonical (newer) copy; planner has an older stale
    # intent copy. Committing planner first then canon makes canon's %ct
    # win when git_provides_worktree=True.
    PlanScenario(
        plan_id="007_newly_created",
        on_disk=[(WorktreeName.canon, PlanState.INTENT), (WorktreeName.planner, PlanState.INTENT)],
        audit_entries=[],
        commit_in=[WorktreeName.planner, WorktreeName.canon],
    ),
    PlanScenario(
        plan_id="008_newly_planned",
        on_disk=[(WorktreeName.planner, PlanState.PLANNING)],
        audit_entries=[_AuditDraft(actor="planner", command="plan", input_plan="008_newly_planned")],
        commit_in=[WorktreeName.planner],
    ),
    PlanScenario(
        plan_id="009_not_pulled",
        on_disk=[(WorktreeName.canon, PlanState.TODO)],
        audit_entries=[_AuditDraft(actor="human", command="todo", input_plan="009_not_pulled")],
        commit_in=[WorktreeName.canon],
    ),
    # Row 18: builder canonical, canon stale. Commit canon first so builder
    # has the newer %ct (canonical-last rule from plan 015 §Risks).
    PlanScenario(
        plan_id="010_active_build",
        on_disk=[(WorktreeName.builder, PlanState.IMPLEMENTING), (WorktreeName.canon, PlanState.IMPLEMENTING)],
        audit_entries=[_AuditDraft(actor="builder", command="implement", input_plan="010_active_build")],
        commit_in=[WorktreeName.canon, WorktreeName.builder],
    ),
    # Row 19: reviewer canonical, builder stale (same canonical-last rule).
    PlanScenario(
        plan_id="011_under_review",
        on_disk=[(WorktreeName.reviewer, PlanState.CHECKING), (WorktreeName.builder, PlanState.CHECKING)],
        audit_entries=[_AuditDraft(actor="reviewer", command="check", input_plan="011_under_review")],
        commit_in=[WorktreeName.builder, WorktreeName.reviewer],
    ),
    PlanScenario(
        plan_id="012_merged_without_review",
        on_disk=[(WorktreeName.canon, PlanState.DONE)],
        audit_entries=[_AuditDraft(actor="human", command="accept", input_plan="012_merged_without_review")],
        commit_in=[WorktreeName.canon],
    ),
    PlanScenario(
        plan_id="013_another_bad_idea",
        on_disk=[(WorktreeName.canon, PlanState.REJECTED)],
        audit_entries=[_AuditDraft(actor="human", command="reject", input_plan="013_another_bad_idea")],
        commit_in=[WorktreeName.canon],
    ),
    # Row 22: audit lags on-disk state. Plan is at checking/ but audit's most
    # recent mapped entry is `implement` (→ implementing). `_from_audit_log`
    # must widen forward so the intersection still includes checking.
    PlanScenario(
        plan_id="014_post_build_mv",
        on_disk=[(WorktreeName.builder, PlanState.CHECKING)],
        audit_entries=[_AuditDraft(actor="builder", command="implement", input_plan="014_post_build_mv")],
        commit_in=[WorktreeName.builder],
    ),
]


# ---------------------------------------------------------------------------
# Case matrix
# ---------------------------------------------------------------------------


def _ok(
    case_id: str,
    plan_id: str,
    command: str | None,
    repo: WorktreeName,
    state: PlanState,
    **kw: object,
) -> ResolverCase:
    return ResolverCase(
        case_id=case_id,
        plan_id=plan_id,
        command=command,
        expected_repo=repo,
        expected_state=state,
        **kw,  # type: ignore[arg-type]
    )


def _err(case_id: str, plan_id: str, command: str | None, error: type[Exception], **kw: object) -> ResolverCase:
    return ResolverCase(
        case_id=case_id,
        plan_id=plan_id,
        command=command,
        expected_error=error,
        **kw,  # type: ignore[arg-type]
    )


_CASES: list[ResolverCase] = [
    # --- synced intent/001_idea --------------------------------------------
    # Row 1: ambiguous — `ask` repos={planner, builder}, git state={intent};
    # filesystem yields planner+builder INTENT copies and the intent-vs-non-intent
    # disambiguation rule does not apply (all candidates are INTENT).
    # NB: changing find_all_plans to dedup at FS level would silently flip this
    # to a successful resolution — see plan 015 risks §Ambiguity row.
    _err("synced-001-ask-ambiguous", "001_idea", "ask", AmbiguousPlanError),
    _ok(
        "synced-001-ask-repo-hint",
        "001_idea",
        "ask",
        WorktreeName.planner,
        PlanState.INTENT,
        fuzzy_input="planner/001",
    ),
    _ok("synced-001-critique", "001_idea", "critique", WorktreeName.planner, PlanState.INTENT),
    _ok("synced-001-plan", "001_idea", "plan", WorktreeName.planner, PlanState.INTENT),
    _ok("synced-001-subdivide", "001_idea", "subdivide", WorktreeName.planner, PlanState.INTENT),
    # --- synced rejected/002, done/003, checking/004 -----------------------
    _err("synced-002-rejected-ask", "002_bad_idea", "ask", SourcesDisagreeError),
    _ok(
        "synced-002-rejected-ask-hints",
        "002_bad_idea",
        "ask",
        WorktreeName.planner,
        PlanState.REJECTED,
        fuzzy_input="planner/002",
    ),
    _err("synced-003-done-ask", "003_early", "ask", SourcesDisagreeError),
    _err("synced-004-checking-ask", "004_flawed", "ask", SourcesDisagreeError),
    _ok(
        "synced-004-checking-ask-hints",
        "004_flawed",
        "ask",
        WorktreeName.reviewer,
        PlanState.CHECKING,
        fuzzy_input="reviewer/004",
    ),
    _ok("synced-004-pr", "004_flawed", "pr", WorktreeName.reviewer, PlanState.CHECKING),
    _ok("synced-004-fix", "004_flawed", "fix", WorktreeName.reviewer, PlanState.CHECKING),
    # --- synced todo/005, planning/006 -------------------------------------
    _err("synced-005-todo-ask", "005_next_in_line", "ask", SourcesDisagreeError),
    _err("synced-005-todo-critique", "005_next_in_line", "critique", SourcesDisagreeError),
    _ok("synced-006-planning-ask", "006_draft", "ask", WorktreeName.planner, PlanState.PLANNING),
    _ok("synced-006-planning-critique", "006_draft", "critique", WorktreeName.planner, PlanState.PLANNING),
    _ok("synced-006-planning-subdivide", "006_draft", "subdivide", WorktreeName.planner, PlanState.PLANNING),
    # --- WIP plans ----------------------------------------------------------
    # Row 15: push with git_provides_worktree=True; git ties repos to canon
    # (newer %ct), subcommand's worktree_override=canon confirms.
    _ok(
        "wip-007-push-git-provides-repo",
        "007_newly_created",
        "push",
        WorktreeName.canon,
        PlanState.INTENT,
        git_provides_worktree=True,
    ),
    _ok("wip-008-planning-critique", "008_newly_planned", "critique", WorktreeName.planner, PlanState.PLANNING),
    # Row 17 exercises `implement`'s `also_valid_in_states=[planning]`.
    _ok("wip-009-todo-implement", "009_not_pulled", "implement", WorktreeName.canon, PlanState.TODO),
    _ok("wip-010-implementing-build", "010_active_build", "build", WorktreeName.builder, PlanState.IMPLEMENTING),
    _ok("wip-011-checking-pr", "011_under_review", "pr", WorktreeName.reviewer, PlanState.CHECKING),
    _err("wip-012-done-pr", "012_merged_without_review", "pr", SourcesDisagreeError),
    # no ok-with-hints for 012: it is done, but only exists in canon. Must be pushed before reviewer can see it.
    _err("wip-013-rejected-ask", "013_another_bad_idea", "ask", SourcesDisagreeError),
    # Row 22: `_from_audit_log` widening safety net (audit `implement` lags
    # an on-disk move into checking/; widened state set must include CHECKING).
    _ok("wip-014-audit-widening", "014_post_build_mv", "check", WorktreeName.builder, PlanState.CHECKING),
]


# ---------------------------------------------------------------------------
# Module-scoped world / config: built once, reused across all rows.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    w = tmp_path_factory.mktemp("workflow_world")
    create_project_marker(w)
    _build_world(w, _SCENARIOS)
    return w


@pytest.fixture(scope="module")
def config(world: Path) -> WorkflowConfig:
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", world / "config.yaml")
    return load_config(world / "config.yaml", project_dir=world)


# ---------------------------------------------------------------------------
# The single parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.case_id)
def test_wide_resolver_matrix(case: ResolverCase, world: Path, config: WorkflowConfig) -> None:  # noqa: ARG001
    # world: unused directly (resolve_plan takes config only) but required to trigger the
    # module-scoped fixture that populates config.project_dir's filesystem.
    seq = case.plan_id.split("_", 1)[0]  # "001_idea" → "001"
    fuzzy = case.fuzzy_input if case.fuzzy_input is not None else seq
    if case.expected_error is not None:
        with pytest.raises(case.expected_error):
            resolve_plan(
                fuzzy,
                config=config,
                command_name=case.command,
                git_provides_worktree=case.git_provides_worktree,
            )
        return
    plan = resolve_plan(
        fuzzy,
        config=config,
        command_name=case.command,
        git_provides_worktree=case.git_provides_worktree,
    )
    assert case.expected_repo is not None
    assert case.expected_state is not None
    assert plan.path.parts[0] == case.expected_repo.value
    assert plan.state == case.expected_state
