"""Tests for workflow.git_helpers."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workflow import git_helpers
from workflow.exceptions import WorkflowError
from workflow.git_helpers import (
    CommitResult,
    git_commit_if_changed,
    git_current_branch,
    git_index_lock_present,
    git_is_ancestor,
    git_is_valid_repo,
    git_ref_exists_remote,
    git_remote_url,
    park_on_main,
    refresh_main,
)

_GIT = shutil.which("git") or "git"


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([_GIT, *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "initial commit"], cwd=path)
    return path


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
    ]


def test_transfer_branch_to_main_rejects_canon_not_on_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(git_helpers, "git_current_branch", lambda _repo: "feat/other")

    with pytest.raises(WorkflowError, match="won't clobber a feature branch"):
        git_helpers.transfer_branch_to_main(tmp_path / "builder", tmp_path / "canon")


def test_transfer_branch_to_main_branch_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit branch= is used as the merge ref regardless of source_repo's current branch."""
    source_repo = tmp_path / "reviewer"
    canon_repo = tmp_path / "canon"
    merges: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        git_helpers,
        "git_current_branch",
        lambda repo: "main" if repo == canon_repo else "some-other-branch",
    )
    monkeypatch.setattr(git_helpers, "git_push", MagicMock())
    monkeypatch.setattr(git_helpers, "git_pull", MagicMock())
    monkeypatch.setattr(git_helpers, "git_checkout", MagicMock())
    monkeypatch.setattr(
        git_helpers,
        "git_merge",
        lambda _repo, branch, *, dry_run=False: merges.append((branch, dry_run)),
    )

    git_helpers.transfer_branch_to_main(source_repo, canon_repo, branch="feat/005_foo", dry_run=True)

    assert merges == [("origin/feat/005_foo", True)]


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


def test_git_commit_if_changed_returns_sentinel_when_untracked_files_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Git phrases the no-op differently when untracked (but unstaged) files exist."""
    exc = subprocess.CalledProcessError(
        1,
        ["git", "commit"],
        output=b'nothing added to commit but untracked files present (use "git add" to track)\n',
    )
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


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_is_valid_repo_true_for_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert git_is_valid_repo(repo) is True
    assert git_is_valid_repo(repo, bare=True) is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_is_valid_repo_true_for_bare_clone(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(source), str(bare)], cwd=tmp_path)
    assert git_is_valid_repo(bare, bare=True) is True
    assert git_is_valid_repo(bare, bare=False) is False


def test_git_is_valid_repo_false_for_non_repo(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    assert git_is_valid_repo(not_a_repo) is False


def test_git_is_valid_repo_false_for_missing_dir(tmp_path: Path) -> None:
    assert git_is_valid_repo(tmp_path / "missing") is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_ref_exists_remote(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(source), str(bare)], cwd=tmp_path)
    clone = tmp_path / "clone"
    _git(["clone", str(bare), str(clone)], cwd=tmp_path)

    assert git_ref_exists_remote(clone, "main") is True
    assert git_ref_exists_remote(clone, "does-not-exist") is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_is_ancestor(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(["rev-parse", "HEAD"], cwd=repo).stdout.decode().strip()
    (repo / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "second commit"], cwd=repo)
    head_sha = _git(["rev-parse", "HEAD"], cwd=repo).stdout.decode().strip()

    assert git_is_ancestor(repo, base_sha, head_sha) is True
    assert git_is_ancestor(repo, head_sha, base_sha) is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_remote_url_returns_configured_url(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(source), str(bare)], cwd=tmp_path)
    clone = tmp_path / "clone"
    _git(["clone", str(bare), str(clone)], cwd=tmp_path)

    assert git_remote_url(clone, "origin") == str(bare)


def test_git_remote_url_returns_none_when_unset(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert git_remote_url(repo, "origin") is None


def test_git_index_lock_present_false_by_default(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert git_index_lock_present(repo) is False


def test_git_index_lock_present_true_for_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".git" / "index.lock").touch()
    assert git_index_lock_present(repo) is True


def test_git_index_lock_present_true_for_bare(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(source), str(bare)], cwd=tmp_path)
    (bare / "index.lock").touch()
    assert git_index_lock_present(bare) is True


# ---------------------------------------------------------------------------
# refresh_main / park_on_main
# ---------------------------------------------------------------------------


def _make_bare_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """Return (bare, clone): a bare repo seeded from a throwaway source, and a real clone of it."""
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(source), str(bare)], cwd=tmp_path)
    clone = tmp_path / "clone"
    _git(["clone", str(bare), str(clone)], cwd=tmp_path)
    return bare, clone


def _advance_bare_main(bare: Path, tmp_path: Path) -> str:
    """Push a new commit to bare's main from a second, throwaway clone. Returns the new SHA."""
    pusher = tmp_path / "pusher"
    _git(["clone", str(bare), str(pusher)], cwd=tmp_path)
    (pusher / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=pusher)
    _git(["commit", "-m", "advance main"], cwd=pusher)
    _git(["push"], cwd=pusher)
    return _git(["rev-parse", "HEAD"], cwd=pusher).stdout.decode().strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_refresh_main_pulls_when_on_main(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    new_sha = _advance_bare_main(bare, tmp_path)

    refresh_main(clone)

    assert _git(["rev-parse", "HEAD"], cwd=clone).stdout.decode().strip() == new_sha


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_refresh_main_fetches_without_touching_tree_when_not_on_main(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    new_sha = _advance_bare_main(bare, tmp_path)

    refresh_main(clone)

    assert git_current_branch(clone) == "feat/x"
    assert _git(["rev-parse", "refs/heads/main"], cwd=clone).stdout.decode().strip() == new_sha


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_refresh_main_opportunistically_updates_origin_main_tracking_ref(tmp_path: Path) -> None:
    """The rebase path (git_helpers's fetch during resume classification) relies on this."""
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    new_sha = _advance_bare_main(bare, tmp_path)

    refresh_main(clone)

    assert _git(["rev-parse", "refs/remotes/origin/main"], cwd=clone).stdout.decode().strip() == new_sha


def test_refresh_main_warns_and_returns_when_no_origin_remote(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    refresh_main(repo)  # must not raise


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_park_on_main_checks_out_and_refreshes_when_clean(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    new_sha = _advance_bare_main(bare, tmp_path)

    park_on_main(clone)

    assert git_current_branch(clone) == "main"
    assert _git(["rev-parse", "HEAD"], cwd=clone).stdout.decode().strip() == new_sha


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_park_on_main_falls_back_to_refresh_when_dirty(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    (clone / "wip.txt").write_text("wip\n", encoding="utf-8")
    new_sha = _advance_bare_main(bare, tmp_path)

    park_on_main(clone)

    # Left on the dirty feature branch, but 'main' was still refreshed via fetch.
    assert git_current_branch(clone) == "feat/x"
    assert _git(["rev-parse", "refs/heads/main"], cwd=clone).stdout.decode().strip() == new_sha
