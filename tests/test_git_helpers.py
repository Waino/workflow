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
    TransferResult,
    delete_merged_branch,
    git_branch_delete,
    git_branch_exists,
    git_commit_if_changed,
    git_current_branch,
    git_index_lock_present,
    git_is_ancestor,
    git_is_valid_repo,
    git_ref_exists_remote,
    git_remote_url,
    git_rev_parse,
    git_status_porcelain,
    park_on_main,
    refresh_main,
    transfer_branch_to_main,
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


def test_transfer_branch_to_main_rejects_canon_not_on_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(git_helpers, "git_current_branch", lambda _repo: "feat/other")

    with pytest.raises(WorkflowError, match="won't clobber a feature branch"):
        git_helpers.transfer_branch_to_main(tmp_path / "builder", tmp_path / "canon")


# ---------------------------------------------------------------------------
# transfer_branch_to_main: rebase path (real git repos)
# ---------------------------------------------------------------------------


def _setup_transfer_scenario(tmp_path: Path, branch: str) -> tuple[Path, Path, Path]:
    """Return (bare, source, canon): source has *branch* pushed, canon is a fresh clone on main."""
    origin = _init_repo(tmp_path / "origin")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(origin), str(bare)], cwd=tmp_path)
    source = tmp_path / "source"
    _git(["clone", str(bare), str(source)], cwd=tmp_path)
    _git(["checkout", "-b", branch], cwd=source)
    (source / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(["add", "."], cwd=source)
    _git(["commit", "-m", "feature work"], cwd=source)
    _git(["push", "-u", "origin", branch], cwd=source)
    canon = tmp_path / "canon"
    _git(["clone", str(bare), str(canon)], cwd=tmp_path)
    return bare, source, canon


def _advance_main_unrelated(bare: Path, tmp_path: Path, *, name: str = "pusher") -> None:
    """Push an unrelated commit to bare's main, diverging any branch cut before it."""
    pusher = tmp_path / name
    _git(["clone", str(bare), str(pusher)], cwd=tmp_path)
    (pusher / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    _git(["add", "."], cwd=pusher)
    _git(["commit", "-m", "unrelated main commit"], cwd=pusher)
    _git(["push"], cwd=pusher)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_fast_forward_with_real_repos(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")

    result = transfer_branch_to_main(source, canon, branch="feat/x")

    assert result is TransferResult.FAST_FORWARD
    assert (canon / "feature.txt").exists()
    bare_main = _git(["rev-parse", "refs/heads/main"], cwd=bare).stdout.decode().strip()
    canon_main = _git(["rev-parse", "HEAD"], cwd=canon).stdout.decode().strip()
    assert bare_main == canon_main


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_rebase_refused_without_allow_rebase(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)

    with pytest.raises(WorkflowError, match="workflow rebase"):
        transfer_branch_to_main(source, canon, branch="feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_rebase_succeeds(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)

    result = transfer_branch_to_main(source, canon, branch="feat/x", allow_rebase=True)

    assert result is TransferResult.REBASED
    assert (canon / "feature.txt").exists()
    assert (canon / "unrelated.txt").exists()
    bare_main = _git(["rev-parse", "refs/heads/main"], cwd=bare).stdout.decode().strip()
    canon_main = _git(["rev-parse", "HEAD"], cwd=canon).stdout.decode().strip()
    assert bare_main == canon_main


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_rebase_conflict_aborts_and_raises(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    # Conflicting edit to the same file on main.
    pusher = tmp_path / "pusher"
    _git(["clone", str(bare), str(pusher)], cwd=tmp_path)
    (pusher / "feature.txt").write_text("main version\n", encoding="utf-8")
    _git(["add", "."], cwd=pusher)
    _git(["commit", "-m", "conflicting main commit"], cwd=pusher)
    _git(["push"], cwd=pusher)
    _git(["checkout", "feat/x"], cwd=source)
    head_before = _git(["rev-parse", "HEAD"], cwd=source).stdout.decode().strip()

    with pytest.raises(WorkflowError, match=r"feature\.txt"):
        transfer_branch_to_main(source, canon, branch="feat/x", allow_rebase=True)

    # The rebase must have been aborted: source is untouched and not mid-rebase.
    assert _git(["rev-parse", "HEAD"], cwd=source).stdout.decode().strip() == head_before
    assert not (source / ".git" / "rebase-apply").exists()
    assert not (source / ".git" / "rebase-merge").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_rebase_dry_run_does_not_mutate(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)
    source_head_before = _git(["rev-parse", "HEAD"], cwd=source).stdout.decode().strip()
    bare_main_before = _git(["rev-parse", "refs/heads/main"], cwd=bare).stdout.decode().strip()

    result = transfer_branch_to_main(source, canon, branch="feat/x", allow_rebase=True, dry_run=True)

    assert result is TransferResult.REBASED
    assert _git(["rev-parse", "HEAD"], cwd=source).stdout.decode().strip() == source_head_before
    assert _git(["rev-parse", "refs/heads/main"], cwd=bare).stdout.decode().strip() == bare_main_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_rebase_refuses_dirty_tree(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)
    (source / "dirty.txt").write_text("wip\n", encoding="utf-8")

    with pytest.raises(WorkflowError, match="uncommitted changes"):
        transfer_branch_to_main(source, canon, branch="feat/x", allow_rebase=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_rebase_refuses_checked_out_elsewhere(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)
    other_worktree = tmp_path / "other"
    _git(["clone", str(bare), str(other_worktree)], cwd=tmp_path)
    _git(["checkout", "feat/x"], cwd=other_worktree)

    with pytest.raises(WorkflowError, match=str(other_worktree)):
        transfer_branch_to_main(
            source,
            canon,
            branch="feat/x",
            allow_rebase=True,
            other_worktrees=[other_worktree],
        )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_post_rebase_verify_runs_before_push(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)
    calls: list[str] = []

    transfer_branch_to_main(
        source,
        canon,
        branch="feat/x",
        allow_rebase=True,
        post_rebase_verify=lambda: calls.append("verified"),
    )

    assert calls == ["verified"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_post_rebase_verify_failure_blocks_push(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)
    bare_branch_before = _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout.decode().strip()

    def _failing_verify() -> None:
        msg = "hook failed"
        raise WorkflowError(msg)

    with pytest.raises(WorkflowError, match="hook failed"):
        transfer_branch_to_main(source, canon, branch="feat/x", allow_rebase=True, post_rebase_verify=_failing_verify)

    # Rebase happened locally (that's what verify is checking), but nothing was pushed.
    assert _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout.decode().strip() == bare_branch_before


def _rebase_locally_without_pushing(tmp_path: Path, branch: str = "feat/x") -> tuple[Path, Path, Path]:
    """Set up the resume scenario: *branch* rebased locally, its force-push never landed."""
    bare, source, canon = _setup_transfer_scenario(tmp_path, branch)
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", branch], cwd=source)
    _git(["fetch", "origin"], cwd=source)
    _git(["rebase", "origin/main"], cwd=source)
    assert git_status_porcelain(source) == ""
    return bare, source, canon


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_falls_back_to_force_push_after_local_rebase(tmp_path: Path) -> None:
    """Resume scenario: rebased locally in a prior run, but that run's force-push failed."""
    _bare, source, canon = _rebase_locally_without_pushing(tmp_path)

    # branch_needs_rebase is now satisfied (local branch already sits on origin/main), so this
    # must succeed via the fast-forward path's force-push fallback, with allow_rebase left False.
    result = transfer_branch_to_main(source, canon, branch="feat/x", force_push=True)

    assert result is TransferResult.FAST_FORWARD
    assert (canon / "unrelated.txt").exists()
    assert (canon / "feature.txt").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_force_push_fallback_needs_the_flag(tmp_path: Path) -> None:
    """The fallback rewrites published history, so it must never happen without --force-push."""
    bare, source, canon = _rebase_locally_without_pushing(tmp_path)
    remote_before = _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout

    with pytest.raises(WorkflowError, match="--force-push"):
        transfer_branch_to_main(source, canon, branch="feat/x")

    assert _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout == remote_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_force_push_refused_when_remote_has_own_commits(tmp_path: Path) -> None:
    """--force-push must not overwrite a remote commit that exists nowhere locally."""
    bare, source, canon = _rebase_locally_without_pushing(tmp_path)
    # A second clone adds a commit to the remote branch that `source` has never seen.
    other = tmp_path / "other"
    _git(["clone", str(bare), str(other)], cwd=tmp_path)
    _git(["checkout", "feat/x"], cwd=other)
    (other / "other_work.txt").write_text("other\n", encoding="utf-8")
    _git(["add", "."], cwd=other)
    _git(["commit", "-m", "work only on the remote branch"], cwd=other)
    _git(["push"], cwd=other)
    remote_before = _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout

    with pytest.raises(WorkflowError, match="would lose them"):
        transfer_branch_to_main(source, canon, branch="feat/x", force_push=True)

    assert _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout == remote_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_force_push_fallback_reruns_verification(tmp_path: Path) -> None:
    """The interrupted run never verified its rebase, so the resume must verify before publishing."""
    bare, source, canon = _rebase_locally_without_pushing(tmp_path)
    remote_before = _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout

    def _failing_verify() -> None:
        msg = "hook failed"
        raise WorkflowError(msg)

    with pytest.raises(WorkflowError, match="hook failed"):
        transfer_branch_to_main(
            source,
            canon,
            branch="feat/x",
            force_push=True,
            post_rebase_verify=_failing_verify,
        )

    assert _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout == remote_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_rebase_refused_when_remote_has_own_commits(tmp_path: Path) -> None:
    """The rebase path force-pushes too, so it checks for remote-only commits before rebasing."""
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    _advance_main_unrelated(bare, tmp_path)
    _git(["checkout", "feat/x"], cwd=source)
    other = tmp_path / "other"
    _git(["clone", str(bare), str(other)], cwd=tmp_path)
    _git(["checkout", "feat/x"], cwd=other)
    (other / "other_work.txt").write_text("other\n", encoding="utf-8")
    _git(["add", "."], cwd=other)
    _git(["commit", "-m", "work only on the remote branch"], cwd=other)
    _git(["push"], cwd=other)
    source_head_before = _git(["rev-parse", "HEAD"], cwd=source).stdout

    with pytest.raises(WorkflowError, match="Pull them into"):
        transfer_branch_to_main(source, canon, branch="feat/x", allow_rebase=True)

    assert _git(["rev-parse", "HEAD"], cwd=source).stdout == source_head_before


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


def test_refresh_main_warns_and_returns_when_no_origin_remote(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path / "repo")

    refresh_main(repo)  # must not raise

    assert "skipping refresh_main" in capsys.readouterr().err


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


# ---------------------------------------------------------------------------
# git_rev_parse / delete_merged_branch
# ---------------------------------------------------------------------------


def test_git_rev_parse_returns_sha_for_known_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    expected = _git(["rev-parse", "HEAD"], cwd=repo).stdout.decode().strip()
    assert git_rev_parse(repo, "main") == expected


def test_git_rev_parse_returns_none_for_unknown_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert git_rev_parse(repo, "refs/heads/does-not-exist") is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_branch_delete_removes_merged_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(["checkout", "-b", "feat/x"], cwd=repo)
    _git(["checkout", "main"], cwd=repo)
    _git(["merge", "--ff-only", "feat/x"], cwd=repo)

    git_branch_delete(repo, "feat/x")

    assert not git_branch_exists(repo, "feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_branch_delete_refuses_unmerged_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(["checkout", "-b", "feat/x"], cwd=repo)
    (repo / "unmerged.txt").write_text("wip\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "unmerged work"], cwd=repo)
    _git(["checkout", "main"], cwd=repo)

    with pytest.raises(subprocess.CalledProcessError):
        git_branch_delete(repo, "feat/x")
    assert git_branch_exists(repo, "feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_delete_merged_branch_removes_locally_and_on_bare(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    transfer_branch_to_main(source, canon, branch="feat/x")
    builder = tmp_path / "builder"
    _git(["clone", str(bare), str(builder)], cwd=tmp_path)
    _git(["checkout", "feat/x"], cwd=builder)
    # Real callers park reviewer/builder on 'main' before cleanup (git branch -d refuses to
    # delete the branch a worktree currently has checked out).
    _git(["checkout", "main"], cwd=source)
    _git(["checkout", "main"], cwd=builder)

    delete_merged_branch(canon, source, builder, "feat/x")

    assert not git_branch_exists(source, "feat/x")
    assert not git_branch_exists(builder, "feat/x")
    assert not git_ref_exists_remote(canon, "feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_delete_merged_branch_warns_without_raising_when_not_merged(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    other = tmp_path / "other"
    _git(["clone", str(bare), str(other)], cwd=tmp_path)
    del bare

    delete_merged_branch(canon, source, other, "feat/x")  # must not raise

    assert git_branch_exists(source, "feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_delete_merged_branch_is_a_noop_when_already_deleted(tmp_path: Path) -> None:
    """Resumed cleanup: a branch already deleted from bare must not be reported as unmerged."""
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    transfer_branch_to_main(source, canon, branch="feat/x")
    other = tmp_path / "other"
    _git(["clone", str(bare), str(other)], cwd=tmp_path)
    _git(["checkout", "main"], cwd=source)
    delete_merged_branch(canon, source, other, "feat/x")
    del bare

    delete_merged_branch(canon, source, other, "feat/x")  # must not raise
