"""Tests for workflow.commands.doctor."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.conftest import create_project_marker, make_layout
from workflow.audit import AUDIT_LOG_FILENAME
from workflow.cli import main
from workflow.commands.doctor import (
    _active_branches,
    _audit_anomalies,
    _audit_notes,
    _bare_staleness_notes,
    _check_bare,
    _check_worktree,
    _duplicate_plan_anomalies,
    _legacy_marker_anomalies,
    _stack_anomalies,
)
from workflow.config import WorkflowConfig, load_config
from workflow.frontmatter import PlanFrontmatter, RepoBranch, render_frontmatter
from workflow.layout import Layout
from workflow.metadata import LEGACY_ROOT_MARKER, CodeRepoMeta, ProjectMetadata, ProjectMode, write_metadata
from workflow.models import AuditLogEntry
from workflow.names import DOCS_REPO, WorktreeName

_GIT = shutil.which("git") or "git"


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([_GIT, *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603


def _init_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", branch], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "initial commit"], cwd=path)
    return path


def _make_plan(
    repo_path: Path,
    state: str,
    filename: str = "005_foo.md",
    *,
    onto: str | None = None,
    branch: str | None = None,
) -> None:
    plan_dir = repo_path / "docs" / "plans" / state
    plan_dir.mkdir(parents=True, exist_ok=True)
    body = "# 005: Foo\n\n## Goal\nDo stuff.\n"
    if onto is not None or branch is not None:
        branches: dict[str, RepoBranch | None] = (
            {"docs": RepoBranch(branch=branch, base="abc1234")} if branch is not None else {}
        )
        body = render_frontmatter(PlanFrontmatter(onto=onto, branches=branches)) + body
    (plan_dir / filename).write_text(body, encoding="utf-8")


def _config_for(tmp_path: Path) -> WorkflowConfig:
    create_project_marker(tmp_path)
    shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def test_check_bare_missing_is_an_anomaly(tmp_path: Path) -> None:
    check = _check_bare(tmp_path / "bare")
    assert check.exists is False
    assert check.anomalies == ["missing"]


def test_check_bare_valid(tmp_path: Path) -> None:
    worktree = _init_repo(tmp_path / "src")
    bare_path = tmp_path / "bare"
    _git(["clone", "--bare", str(worktree), str(bare_path)], cwd=tmp_path)
    check = _check_bare(bare_path)
    assert check.exists is True
    assert check.valid is True
    assert check.anomalies == []


def test_check_bare_invalid_reports_anomaly(tmp_path: Path) -> None:
    bare_path = tmp_path / "bare"
    bare_path.mkdir()
    (bare_path / "not_a_repo.txt").write_text("junk\n", encoding="utf-8")
    check = _check_bare(bare_path)
    assert check.anomalies == ["not a valid bare repo"]


def test_check_worktree_clean_main_has_no_anomalies(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "canon")
    _git(["remote", "add", "bare", str(tmp_path / "bare")], cwd=repo)
    check = _check_worktree(WorktreeName.canon, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert check.branch == "main"
    assert check.dirty is False
    assert check.anomalies == []


def test_check_worktree_canon_off_main_is_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "canon")
    _git(["checkout", "-b", "some-branch"], cwd=repo)
    check = _check_worktree(WorktreeName.canon, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert any("expected 'main'" in a for a in check.anomalies)


def test_check_worktree_builder_stray_branch_is_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "builder")
    _git(["checkout", "-b", "leftover"], cwd=repo)
    check = _check_worktree(WorktreeName.builder, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert any("stray branch" in a for a in check.anomalies)


def test_check_worktree_builder_active_feature_branch_is_not_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "builder")
    _git(["remote", "add", "bare", str(tmp_path / "bare")], cwd=repo)
    _git(["checkout", "-b", "feat/005_foo"], cwd=repo)
    check = _check_worktree(
        WorktreeName.builder,
        repo,
        active_branches={"feat/005_foo"},
        bare_path=tmp_path / "bare",
    )
    assert check.anomalies == []


def test_check_worktree_no_bare_remote_is_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "canon")
    check = _check_worktree(WorktreeName.canon, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert "no 'bare' remote configured" in check.anomalies


def test_check_worktree_leftover_origin_pointing_at_bare_is_an_anomaly(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=tmp_path)
    _git(["remote", "add", "origin", str(bare)], cwd=canon)

    check = _check_worktree(WorktreeName.canon, canon, active_branches=set(), bare_path=bare)

    assert "'origin' remote points at bare; run scripts/migrate_023.py" in check.anomalies


def test_check_worktree_unrelated_origin_is_not_a_bare_remote_anomaly(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=tmp_path)
    upstream = tmp_path / "upstream"
    _git(["clone", "--bare", str(canon), str(upstream)], cwd=tmp_path)
    _git(["remote", "add", "origin", str(upstream)], cwd=canon)
    _git(["remote", "add", "bare", str(bare)], cwd=canon)

    check = _check_worktree(WorktreeName.canon, canon, active_branches=set(), bare_path=bare)

    assert check.anomalies == []


def test_check_worktree_dirty_tree_is_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "canon")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    check = _check_worktree(WorktreeName.canon, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert "uncommitted changes" in check.anomalies


def test_check_worktree_stale_main_is_an_anomaly(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=tmp_path)
    # Advance bare's main independently, without the worktree ever pulling.
    pusher = tmp_path / "pusher"
    _git(["clone", str(bare), str(pusher)], cwd=tmp_path)
    (pusher / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=pusher)
    _git(["commit", "-m", "advance main"], cwd=pusher)
    _git(["push"], cwd=pusher)

    check = _check_worktree(WorktreeName.canon, canon, active_branches=set(), bare_path=bare)

    assert "main differs from bare (stale or unpushed)" in check.anomalies


def test_check_worktree_rebase_pending_branch_is_a_note_not_an_anomaly(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=tmp_path)
    reviewer = tmp_path / "reviewer"
    _git(["clone", "-o", "bare", str(bare), str(reviewer)], cwd=tmp_path)
    _git(["checkout", "-b", "feat/005_foo"], cwd=reviewer)
    (reviewer / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(["add", "."], cwd=reviewer)
    _git(["commit", "-m", "feature work"], cwd=reviewer)
    _git(["push", "-u", "bare", "feat/005_foo"], cwd=reviewer)
    # Advance bare's main past the branch's base, without the branch being rebased.
    pusher = tmp_path / "pusher"
    _git(["clone", str(bare), str(pusher)], cwd=tmp_path)
    (pusher / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=pusher)
    _git(["commit", "-m", "advance main"], cwd=pusher)
    _git(["push"], cwd=pusher)
    # Isolate the rebase-pending note from the (also legitimate) stale-main anomaly: reviewer's
    # own main *is* up to date, only the feature branch itself has not been rebased onto it.
    _git(["fetch", "bare", "main:main"], cwd=reviewer)

    check = _check_worktree(
        WorktreeName.reviewer,
        reviewer,
        active_branches={"feat/005_foo"},
        bare_path=bare,
    )

    assert check.anomalies == []
    assert any("accept will rebase" in note for note in check.notes)


def test_active_branches_only_includes_planning_implementing_and_checking(tmp_path: Path) -> None:
    _init_repo(tmp_path / "builder")
    _make_plan(tmp_path / "builder", "implementing", "005_foo.md")
    _init_repo(tmp_path / "reviewer")
    _make_plan(tmp_path / "reviewer", "checking", "006_bar.md")
    _init_repo(tmp_path / "planner")
    _make_plan(tmp_path / "planner", "planning", "007_baz.md")

    branches = _active_branches(make_layout(tmp_path))

    assert branches == {"feat/005_foo", "feat/006_bar", "plan/007_baz"}


def test_duplicate_plan_anomalies_flags_same_plan_in_two_state_dirs(tmp_path: Path) -> None:
    _init_repo(tmp_path / "canon")
    _make_plan(tmp_path / "canon", "todo", "005_foo.md")
    _make_plan(tmp_path / "canon", "implementing", "005_foo.md")

    anomalies = _duplicate_plan_anomalies(make_layout(tmp_path))

    assert len(anomalies) == 1
    assert "foo" in anomalies[0]
    assert "todo" in anomalies[0]
    assert "implementing" in anomalies[0]


def test_duplicate_plan_anomalies_clean_when_one_copy_per_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path / "canon")
    _make_plan(tmp_path / "canon", "todo", "005_foo.md")

    assert _duplicate_plan_anomalies(make_layout(tmp_path)) == []


def test_duplicate_plan_anomalies_ignores_intent_and_review_siblings(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    _make_plan(canon, "todo", "005_foo.md")
    _make_plan(canon, "intent", "005_foo.md")
    review_dir = canon / "docs" / "plans" / "reviews" / "pr"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "005_foo_abcd.md").write_text("# review\n", encoding="utf-8")

    assert _duplicate_plan_anomalies(make_layout(tmp_path)) == []


def _make_doctor_config(tmp_path: Path) -> Path:
    src = Path(__file__).parent.parent / "docs" / "default_config.yaml"
    dest = tmp_path / "config.yaml"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    create_project_marker(tmp_path)
    return dest


def test_doctor_cli_exits_zero_on_clean_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_doctor_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    canon = _init_repo(tmp_path / "canon")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=tmp_path)
    _git(["remote", "add", "bare", str(bare)], cwd=canon)
    for repo in ("planner", "builder", "reviewer"):
        _git(["clone", "-o", "bare", str(bare), str(tmp_path / repo)], cwd=tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 0, result.output


def test_legacy_marker_anomalies_flags_leftover_marker(tmp_path: Path) -> None:
    create_project_marker(tmp_path)
    (tmp_path / LEGACY_ROOT_MARKER).touch()

    assert _legacy_marker_anomalies(tmp_path) == ["legacy .workflow_root marker present; run scripts/migrate_023.py"]


def test_legacy_marker_anomalies_clean_without_marker(tmp_path: Path) -> None:
    create_project_marker(tmp_path)

    assert _legacy_marker_anomalies(tmp_path) == []


def test_doctor_cli_exits_nonzero_and_reports_anomaly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_doctor_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    for repo in ("canon", "planner", "builder", "reviewer"):
        _init_repo(tmp_path / repo)
    _git(["checkout", "-b", "leftover"], cwd=tmp_path / "builder")

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 1
    assert "stray branch" in result.output


def test_stack_anomalies_clean_for_a_live_stack(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    builder = _init_repo(tmp_path / "builder")
    _make_plan(canon, "implementing", "005_foo.md")
    _make_plan(canon, "implementing", "006_bar.md", onto="005_foo", branch="feat/005_foo")
    _git(["checkout", "-b", "feat/005_foo"], cwd=builder)

    assert _stack_anomalies(make_layout(tmp_path)) == []


def test_stack_anomalies_flags_unresolvable_onto(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    builder = _init_repo(tmp_path / "builder")
    _make_plan(canon, "implementing", "006_bar.md", onto="999_missing", branch="feat/005_foo")
    _git(["checkout", "-b", "feat/005_foo"], cwd=builder)

    anomalies = _stack_anomalies(make_layout(tmp_path))

    assert anomalies == ["006_bar: onto='999_missing' does not resolve to a plan"]


def test_stack_anomalies_flags_missing_branch_for_an_in_flight_plan(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    _init_repo(tmp_path / "builder")
    _make_plan(canon, "implementing", "005_foo.md")
    _make_plan(canon, "checking", "006_bar.md", onto="005_foo", branch="feat/005_foo")

    anomalies = _stack_anomalies(make_layout(tmp_path))

    assert anomalies == ["006_bar: recorded branch 'feat/005_foo' exists nowhere"]


def test_stack_anomalies_ignores_a_deleted_branch_recorded_by_a_done_plan(tmp_path: Path) -> None:
    """`accept` deletes the shared branch; the done plan's frontmatter still names it."""
    canon = _init_repo(tmp_path / "canon")
    _init_repo(tmp_path / "builder")
    _make_plan(canon, "done", "005_foo.md")
    _make_plan(canon, "done", "006_bar.md", onto="005_foo", branch="feat/005_foo")

    assert _stack_anomalies(make_layout(tmp_path)) == []


def test_stack_anomalies_flags_a_branch_whose_plans_are_all_finished(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    builder = _init_repo(tmp_path / "builder")
    _make_plan(canon, "done", "005_foo.md")
    _git(["checkout", "-b", "feat/005_foo"], cwd=builder)

    anomalies = _stack_anomalies(make_layout(tmp_path))

    assert anomalies == ["feat/005_foo: exists but every plan on it is done/rejected"]


# ---------------------------------------------------------------------------
# _bare_staleness_notes
# ---------------------------------------------------------------------------


def _oob_layout(tmp_path: Path, repos: list[str]) -> Layout:
    return Layout(
        project_dir=tmp_path,
        metadata=ProjectMetadata(mode=ProjectMode.oob, code_repos=[CodeRepoMeta(name=repo) for repo in repos]),
    )


def _make_oob_code_repo(tmp_path: Path, repo: str) -> tuple[Path, Path]:
    """Create canon/<repo> as an upstream-tracking clone, and bare/<repo> seeded from it. Returns (canon, bare).

    The upstream itself is a *bare* repo (a push-able target), matching the real shape
    of a code repo's ``origin``; a plain worktree upstream refuses a push to its
    checked-out branch.
    """
    seed = _init_repo(tmp_path / f"{repo}-seed")
    upstream = tmp_path / f"{repo}-upstream.git"
    _git(["init", "--bare", "-b", "main", str(upstream)], cwd=tmp_path)
    _git(["push", str(upstream), "main"], cwd=seed)
    canon_repo = tmp_path / "canon" / repo
    _git(["clone", str(upstream), str(canon_repo)], cwd=tmp_path)
    bare_repo = tmp_path / "bare" / repo
    _git(["clone", "--bare", str(canon_repo), str(bare_repo)], cwd=tmp_path)
    return canon_repo, bare_repo


def test_bare_staleness_notes_empty_when_bare_matches_origin(tmp_path: Path) -> None:
    canon_repo, _bare_repo = _make_oob_code_repo(tmp_path, "mypackage")

    assert _bare_staleness_notes(_oob_layout(tmp_path, ["mypackage"])) == []
    del canon_repo


def test_bare_staleness_notes_flags_behind_upstream(tmp_path: Path) -> None:
    canon_repo, _bare_repo = _make_oob_code_repo(tmp_path, "mypackage")
    (canon_repo / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=canon_repo)
    _git(["commit", "-m", "advance upstream"], cwd=canon_repo)
    _git(["push"], cwd=canon_repo)
    _git(["fetch", "origin"], cwd=canon_repo)

    notes = _bare_staleness_notes(_oob_layout(tmp_path, ["mypackage"]))

    assert notes == ["mypackage: bare is 1 commit(s) behind upstream (main); run workflow sync"]


def test_bare_staleness_notes_silent_when_bare_holds_unpushed_local_work(tmp_path: Path) -> None:
    """Not the 'behind upstream' case this note covers -- ancestry runs the other way."""
    canon_repo, bare_repo = _make_oob_code_repo(tmp_path, "mypackage")
    _git(["clone", "-o", "bare", str(bare_repo), str(tmp_path / "agent-clone")], cwd=tmp_path)
    agent_clone = tmp_path / "agent-clone"
    (agent_clone / "local.txt").write_text("local\n", encoding="utf-8")
    _git(["add", "."], cwd=agent_clone)
    _git(["commit", "-m", "agent work"], cwd=agent_clone)
    _git(["push", "bare", "HEAD:main"], cwd=agent_clone)
    del canon_repo

    assert _bare_staleness_notes(_oob_layout(tmp_path, ["mypackage"])) == []


def test_bare_staleness_notes_skips_repo_with_unresolvable_default_branch(tmp_path: Path) -> None:
    canon_repo, _bare_repo = _make_oob_code_repo(tmp_path, "mypackage")
    _git(["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=canon_repo)
    _git(["branch", "-m", "trunk"], cwd=canon_repo)

    assert _bare_staleness_notes(_oob_layout(tmp_path, ["mypackage"])) == []


def test_doctor_cli_prints_bare_staleness_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config_for(tmp_path)
    canon_repo, _bare_repo = _make_oob_code_repo(tmp_path, "mypackage")
    (canon_repo / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=canon_repo)
    _git(["commit", "-m", "advance upstream"], cwd=canon_repo)
    _git(["push"], cwd=canon_repo)
    _git(["fetch", "origin"], cwd=canon_repo)
    docs_repo = _init_repo(tmp_path / "canon" / DOCS_REPO)
    del docs_repo

    write_metadata(tmp_path, ProjectMetadata(mode=ProjectMode.oob, code_repos=[CodeRepoMeta(name="mypackage")]))
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "doctor"])

    assert "Bare staleness" in result.output
    assert "mypackage" in result.output
    assert "run workflow sync" in result.output


def _write_audit(tmp_path: Path, *entries: tuple[str, str, str | None]) -> WorkflowConfig:
    config = _config_for(tmp_path)
    lines = [
        AuditLogEntry(timestamp=datetime(2026, 1, 1, tzinfo=UTC), actor=actor, command=command, input_plan=plan)
        for actor, command, plan in entries
    ]
    (tmp_path / AUDIT_LOG_FILENAME).write_text("".join(e.model_dump_json() + "\n" for e in lines), encoding="utf-8")
    return config


def _audit(tmp_path: Path, *entries: tuple[str, str]) -> list[str]:
    config = _write_audit(tmp_path, *((actor, command, "005_foo") for actor, command in entries))
    return _audit_anomalies(config, tmp_path)


def test_audit_anomalies_flags_foreign_transition(tmp_path: Path) -> None:
    anomalies = _audit(tmp_path, ("builder", "plan"))

    assert len(anomalies) == 1
    assert "builder plan 005_foo: state-changing prompt sent to an agent that does not own it" in anomalies[0]


@pytest.mark.parametrize(
    ("actor", "command"),
    [
        ("planner", "plan"),
        ("builder", "critique"),
        ("human", "start"),
        ("human", "todo"),
        ("builder", "rebase"),
        ("planner", "bogus"),
    ],
)
def test_audit_anomalies_accepts_legitimate_entries(tmp_path: Path, actor: str, command: str) -> None:
    assert _audit(tmp_path, (actor, command)) == []


@pytest.mark.parametrize(
    ("state", "plan", "noted"),
    [
        pytest.param("planning", "005_foo", True, id="active_plan"),
        pytest.param("done", "005_foo", False, id="done_plan"),
        pytest.param("rejected", "005_foo", False, id="rejected_plan"),
        pytest.param("planning", "006_gone", False, id="missing_plan"),
        pytest.param("planning", None, False, id="stateless"),
    ],
)
def test_audit_notes_list_unknown_commands_only_for_active_plans(
    tmp_path: Path,
    state: str,
    plan: str | None,
    *,
    noted: bool,
) -> None:
    config = _write_audit(tmp_path, ("planner", "bogus", plan), ("planner", "critique", plan))
    _make_plan(tmp_path / "canon", state)

    notes = _audit_notes(config, config.layout())

    assert notes == (["2026-01-01T00:00:00+00:00 planner bogus 005_foo: unknown command"] if noted else [])
