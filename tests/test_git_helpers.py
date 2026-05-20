"""Tests for workflow.git_helpers."""

from pathlib import Path

import pytest

from workflow import git_helpers
from workflow.exceptions import WorkflowError


def test_transfer_branch_to_main_runs_steps_in_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_repo = tmp_path / "builder"
    master_repo = tmp_path / "master"
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(git_helpers, "git_current_branch", lambda _repo: "feat/005_plan_state_and_git")
    monkeypatch.setattr(git_helpers, "git_push", lambda repo, *, dry_run=False: calls.append(("push", repo, dry_run)))
    monkeypatch.setattr(git_helpers, "git_pull", lambda repo, *, dry_run=False: calls.append(("pull", repo, dry_run)))
    monkeypatch.setattr(
        git_helpers,
        "git_merge",
        lambda repo, branch, *, dry_run=False: calls.append(("merge", repo, branch, dry_run)),
    )

    git_helpers.transfer_branch_to_main(source_repo, master_repo, dry_run=True)

    assert calls == [
        ("push", source_repo, True),
        ("pull", master_repo, True),
        # origin/ prefix because the branch is only a remote tracking ref in target repo
        ("merge", master_repo, "origin/feat/005_plan_state_and_git", True),
        ("push", master_repo, True),
    ]


def test_transfer_branch_to_main_rejects_main_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(git_helpers, "git_current_branch", lambda _repo: "main")

    with pytest.raises(WorkflowError, match="expected a feature branch"):
        git_helpers.transfer_branch_to_main(tmp_path / "builder", tmp_path / "master")


def test_git_merge_includes_double_dash_separator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """git_merge passes '--' before the branch name to prevent flag injection."""
    captured: list[list[str]] = []
    monkeypatch.setattr(git_helpers, "run_command", lambda cmd, **_kw: captured.append(cmd))

    git_helpers.git_merge(tmp_path, "feat/my-branch")

    assert len(captured) == 1
    assert "--" in captured[0]
    idx = captured[0].index("--")
    assert captured[0][idx + 1] == "feat/my-branch"
