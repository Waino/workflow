"""Tests for workflow.git_helpers."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workflow import git_helpers
from workflow.exceptions import WorkflowError
from workflow.git_helpers import CommitResult, git_commit_if_changed


def test_transfer_branch_to_main_runs_steps_in_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_repo = tmp_path / "builder"
    canon_repo = tmp_path / "canon"
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        git_helpers,
        "git_current_branch",
        lambda repo: "feat/005_plan_state_and_git" if repo == source_repo else "main",
    )
    monkeypatch.setattr(git_helpers, "git_push", lambda repo, *, dry_run=False: calls.append(("push", repo, dry_run)))
    monkeypatch.setattr(git_helpers, "git_pull", lambda repo, *, dry_run=False: calls.append(("pull", repo, dry_run)))
    monkeypatch.setattr(
        git_helpers,
        "git_merge",
        lambda repo, branch, *, dry_run=False: calls.append(("merge", repo, branch, dry_run)),
    )

    git_helpers.transfer_branch_to_main(source_repo, canon_repo, dry_run=True)

    assert calls == [
        ("push", source_repo, True),
        ("pull", canon_repo, True),
        # origin/ prefix because the branch is only a remote tracking ref in target repo
        ("merge", canon_repo, "origin/feat/005_plan_state_and_git", True),
        ("push", canon_repo, True),
        ("pull", source_repo, True),
    ]


def test_transfer_branch_to_main_rejects_main_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(git_helpers, "git_current_branch", lambda _repo: "main")

    with pytest.raises(WorkflowError, match="expected a feature branch"):
        git_helpers.transfer_branch_to_main(tmp_path / "builder", tmp_path / "canon")


def test_git_merge_includes_double_dash_separator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """git_merge passes '--' before the branch name to prevent flag injection."""
    captured: list[list[str]] = []
    monkeypatch.setattr(git_helpers, "run_command", lambda cmd, **_kw: captured.append(cmd))

    git_helpers.git_merge(tmp_path, "feat/my-branch")

    assert len(captured) == 1
    assert "--" in captured[0]
    idx = captured[0].index("--")
    assert captured[0][idx + 1] == "feat/my-branch"


def test_git_commit_if_changed_returns_committed_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b""))
    monkeypatch.setattr(git_helpers.subprocess, "run", fake)

    result = git_commit_if_changed(tmp_path, "msg")
    assert result is CommitResult.COMMITTED


def test_git_commit_if_changed_returns_sentinel_on_nothing_to_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    exc = subprocess.CalledProcessError(1, ["git", "commit"], output=b"nothing to commit, working tree clean\n")
    fake = MagicMock(side_effect=exc)
    monkeypatch.setattr(git_helpers.subprocess, "run", fake)

    result = git_commit_if_changed(tmp_path, "msg")
    assert result is CommitResult.NOTHING_TO_COMMIT


def test_git_commit_if_changed_reraises_on_real_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exc = subprocess.CalledProcessError(128, ["git", "commit"], output=b"", stderr=b"fatal: not a git repository")
    fake = MagicMock(side_effect=exc)
    monkeypatch.setattr(git_helpers.subprocess, "run", fake)

    with pytest.raises(subprocess.CalledProcessError):
        git_commit_if_changed(tmp_path, "msg")


def test_git_commit_if_changed_dry_run_does_not_invoke_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(git_helpers, "run_command", lambda cmd, **_kw: calls.append(cmd))
    fake_subprocess = MagicMock()
    monkeypatch.setattr(git_helpers.subprocess, "run", fake_subprocess)

    result = git_commit_if_changed(tmp_path, "msg", dry_run=True, no_verify=True)
    assert result is CommitResult.COMMITTED
    assert calls == [["git", "commit", "-m", "msg", "--no-verify"]]
    fake_subprocess.assert_not_called()
