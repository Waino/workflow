"""Tests for workflow.commands.doctor."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from workflow.cli import main
from workflow.commands.doctor import (
    _active_branches,
    _check_bare,
    _check_worktree,
    _duplicate_plan_anomalies,
    _stack_anomalies,
)
from workflow.config import WORKFLOW_ROOT_MARKER, WorkflowConfig, load_config
from workflow.frontmatter import PlanFrontmatter, RepoBranch, render_frontmatter
from workflow.names import RepoName

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
        branches = {"docs": RepoBranch(branch=branch, base="abc1234")} if branch is not None else {}
        body = render_frontmatter(PlanFrontmatter(onto=onto, branches=branches)) + body
    (plan_dir / filename).write_text(body, encoding="utf-8")


def _config_for(tmp_path: Path) -> WorkflowConfig:
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
    check = _check_worktree(RepoName.canon, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert check.branch == "main"
    assert check.dirty is False
    assert check.anomalies == []


def test_check_worktree_canon_off_main_is_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "canon")
    _git(["checkout", "-b", "some-branch"], cwd=repo)
    check = _check_worktree(RepoName.canon, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert any("expected 'main'" in a for a in check.anomalies)


def test_check_worktree_builder_stray_branch_is_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "builder")
    _git(["checkout", "-b", "leftover"], cwd=repo)
    check = _check_worktree(RepoName.builder, repo, active_branches=set(), bare_path=tmp_path / "bare")
    assert any("stray branch" in a for a in check.anomalies)


def test_check_worktree_builder_active_feature_branch_is_not_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "builder")
    _git(["checkout", "-b", "feat/005_foo"], cwd=repo)
    check = _check_worktree(
        RepoName.builder,
        repo,
        active_branches={"feat/005_foo"},
        bare_path=tmp_path / "bare",
    )
    assert check.anomalies == []


def test_check_worktree_dirty_tree_is_an_anomaly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "canon")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    check = _check_worktree(RepoName.canon, repo, active_branches=set(), bare_path=tmp_path / "bare")
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

    check = _check_worktree(RepoName.canon, canon, active_branches=set(), bare_path=bare)

    assert "main differs from bare (stale or unpushed)" in check.anomalies


def test_check_worktree_rebase_pending_branch_is_a_note_not_an_anomaly(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=tmp_path)
    reviewer = tmp_path / "reviewer"
    _git(["clone", str(bare), str(reviewer)], cwd=tmp_path)
    _git(["checkout", "-b", "feat/005_foo"], cwd=reviewer)
    (reviewer / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(["add", "."], cwd=reviewer)
    _git(["commit", "-m", "feature work"], cwd=reviewer)
    _git(["push", "-u", "origin", "feat/005_foo"], cwd=reviewer)
    # Advance bare's main past the branch's base, without the branch being rebased.
    pusher = tmp_path / "pusher"
    _git(["clone", str(bare), str(pusher)], cwd=tmp_path)
    (pusher / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=pusher)
    _git(["commit", "-m", "advance main"], cwd=pusher)
    _git(["push"], cwd=pusher)
    # Isolate the rebase-pending note from the (also legitimate) stale-main anomaly: reviewer's
    # own main *is* up to date, only the feature branch itself has not been rebased onto it.
    _git(["fetch", "origin", "main:main"], cwd=reviewer)

    check = _check_worktree(
        RepoName.reviewer,
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

    branches = _active_branches(tmp_path)

    assert branches == {"feat/005_foo", "feat/006_bar", "plan/007_baz"}


def test_duplicate_plan_anomalies_flags_same_plan_in_two_state_dirs(tmp_path: Path) -> None:
    _init_repo(tmp_path / "canon")
    _make_plan(tmp_path / "canon", "todo", "005_foo.md")
    _make_plan(tmp_path / "canon", "implementing", "005_foo.md")

    anomalies = _duplicate_plan_anomalies(tmp_path)

    assert len(anomalies) == 1
    assert "foo" in anomalies[0]
    assert "todo" in anomalies[0]
    assert "implementing" in anomalies[0]


def test_duplicate_plan_anomalies_clean_when_one_copy_per_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path / "canon")
    _make_plan(tmp_path / "canon", "todo", "005_foo.md")

    assert _duplicate_plan_anomalies(tmp_path) == []


def test_duplicate_plan_anomalies_ignores_intent_and_review_siblings(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    _make_plan(canon, "todo", "005_foo.md")
    _make_plan(canon, "intent", "005_foo.md")
    review_dir = canon / "docs" / "plans" / "reviews" / "pr"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "005_foo_abcd.md").write_text("# review\n", encoding="utf-8")

    assert _duplicate_plan_anomalies(tmp_path) == []


def _make_doctor_config(tmp_path: Path) -> Path:
    src = Path(__file__).parent.parent / "docs" / "default_config.yaml"
    dest = tmp_path / "config.yaml"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    return dest


def test_doctor_cli_exits_zero_on_clean_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_doctor_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    canon = _init_repo(tmp_path / "canon")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=tmp_path)
    for repo in ("planner", "builder", "reviewer"):
        _git(["clone", str(bare), str(tmp_path / repo)], cwd=tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 0, result.output


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

    assert _stack_anomalies(tmp_path, _config_for(tmp_path)) == []


def test_stack_anomalies_flags_unresolvable_onto(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    builder = _init_repo(tmp_path / "builder")
    _make_plan(canon, "implementing", "006_bar.md", onto="999_missing", branch="feat/005_foo")
    _git(["checkout", "-b", "feat/005_foo"], cwd=builder)

    anomalies = _stack_anomalies(tmp_path, _config_for(tmp_path))

    assert anomalies == ["006_bar: onto='999_missing' does not resolve to a plan"]


def test_stack_anomalies_flags_missing_branch_for_an_in_flight_plan(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    _init_repo(tmp_path / "builder")
    _make_plan(canon, "implementing", "005_foo.md")
    _make_plan(canon, "checking", "006_bar.md", onto="005_foo", branch="feat/005_foo")

    anomalies = _stack_anomalies(tmp_path, _config_for(tmp_path))

    assert anomalies == ["006_bar: recorded branch 'feat/005_foo' exists nowhere"]


def test_stack_anomalies_ignores_a_deleted_branch_recorded_by_a_done_plan(tmp_path: Path) -> None:
    """`accept` deletes the shared branch; the done plan's frontmatter still names it."""
    canon = _init_repo(tmp_path / "canon")
    _init_repo(tmp_path / "builder")
    _make_plan(canon, "done", "005_foo.md")
    _make_plan(canon, "done", "006_bar.md", onto="005_foo", branch="feat/005_foo")

    assert _stack_anomalies(tmp_path, _config_for(tmp_path)) == []


def test_stack_anomalies_flags_a_branch_whose_plans_are_all_finished(tmp_path: Path) -> None:
    canon = _init_repo(tmp_path / "canon")
    builder = _init_repo(tmp_path / "builder")
    _make_plan(canon, "done", "005_foo.md")
    _git(["checkout", "-b", "feat/005_foo"], cwd=builder)

    anomalies = _stack_anomalies(tmp_path, _config_for(tmp_path))

    assert anomalies == ["feat/005_foo: exists but every plan on it is done/rejected"]
