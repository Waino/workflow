"""Tests for workflow.git_helpers."""

import re
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
    git_changed_files_not_in_bare,
    git_check_ref_format,
    git_commit_if_changed,
    git_commits_ahead_of_remote,
    git_commits_behind_bare,
    git_commits_between,
    git_commits_not_in_bare,
    git_current_branch,
    git_diff_branch,
    git_diff_text_not_in_bare,
    git_fetch_remote,
    git_index_lock_present,
    git_is_ancestor,
    git_is_valid_repo,
    git_list_remotes,
    git_push_refspec,
    git_ref_exists_remote,
    git_remote_url,
    git_rev_parse,
    git_status_porcelain,
    park_on_main,
    refresh_main,
    resolve_default_branch,
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


def test_no_origin_literal_in_workflow_package() -> None:
    """023.3: the bare remote is named BARE_REMOTE everywhere; 'origin' means canon/upstream now.

    023.5 gives ``"origin"`` its second, legitimate meaning: a code (or docs) repo's real
    upstream remote (intent decision 11), read by ``workflow sync`` and its callers. Those
    lines carry the marker below and are excluded; everywhere else a literal ``"origin"``
    would still mean the pre-023.3 name for the bare remote.
    """
    package_root = Path(__file__).parents[1] / "workflow"
    pattern = re.compile(r"""['"]origin['"]""")
    # The single sanctioned spelling, used only to detect/migrate the pre-023 shape.
    legacy_definition = 'LEGACY_BARE_REMOTE = "origin"'
    upstream_remote_marker = "# upstream remote, not bare (023.5)"
    offending = []
    for path in package_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").replace(legacy_definition, "")
        lines = [line for line in text.splitlines() if upstream_remote_marker not in line]
        if pattern.search("\n".join(lines)):
            offending.append(str(path.relative_to(package_root)))
    assert offending == []


def test_transfer_branch_to_main_rejects_canon_not_on_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(git_helpers, "git_current_branch", lambda _repo: "feat/other")

    with pytest.raises(WorkflowError, match="won't clobber a feature branch"):
        git_helpers.transfer_branch_to_main(tmp_path / "builder", tmp_path / "canon")


# ---------------------------------------------------------------------------
# transfer_branch_to_main: rebase path (real git repos)
# ---------------------------------------------------------------------------


def _setup_transfer_scenario(tmp_path: Path, branch: str) -> tuple[Path, Path, Path]:
    """Return (bare, source, canon): source has *branch* pushed, canon is a fresh clone on main."""
    seed = _init_repo(tmp_path / "seed")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(seed), str(bare)], cwd=tmp_path)
    source = tmp_path / "source"
    _git(["clone", "-o", "bare", str(bare), str(source)], cwd=tmp_path)
    _git(["checkout", "-b", branch], cwd=source)
    (source / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(["add", "."], cwd=source)
    _git(["commit", "-m", "feature work"], cwd=source)
    _git(["push", "-u", "bare", branch], cwd=source)
    canon = tmp_path / "canon"
    _git(["clone", "-o", "bare", str(bare), str(canon)], cwd=tmp_path)
    return bare, source, canon


def _advance_main_unrelated(bare: Path, tmp_path: Path, *, name: str = "pusher") -> None:
    """Push an unrelated commit to bare's main, diverging any branch cut before it."""
    pusher = tmp_path / name
    _git(["clone", "-o", "bare", str(bare), str(pusher)], cwd=tmp_path)
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
    _git(["clone", "-o", "bare", str(bare), str(pusher)], cwd=tmp_path)
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
    _git(["clone", "-o", "bare", str(bare), str(other_worktree)], cwd=tmp_path)
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
    _git(["fetch", "bare"], cwd=source)
    _git(["rebase", "bare/main"], cwd=source)
    assert git_status_porcelain(source) == ""
    return bare, source, canon


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_transfer_branch_to_main_falls_back_to_force_push_after_local_rebase(tmp_path: Path) -> None:
    """Resume scenario: rebased locally in a prior run, but that run's force-push failed."""
    _bare, source, canon = _rebase_locally_without_pushing(tmp_path)

    # branch_needs_rebase is now satisfied (local branch already sits on bare/main), so this
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
    _git(["clone", "-o", "bare", str(bare), str(other)], cwd=tmp_path)
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
    _git(["clone", "-o", "bare", str(bare), str(other)], cwd=tmp_path)
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


@pytest.mark.parametrize("mkdir", [pytest.param(True, id="non_repo"), pytest.param(False, id="missing_dir")])
def test_git_is_valid_repo_false(tmp_path: Path, *, mkdir: bool) -> None:
    path = tmp_path / "not_a_repo"
    if mkdir:
        path.mkdir()
    assert git_is_valid_repo(path) is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_ref_exists_remote(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(source), str(bare)], cwd=tmp_path)
    clone = tmp_path / "clone"
    _git(["clone", "-o", "bare", str(bare), str(clone)], cwd=tmp_path)

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
    _git(["clone", "-o", "bare", str(bare), str(clone)], cwd=tmp_path)

    assert git_remote_url(clone, "bare") == str(bare)


def test_git_remote_url_returns_none_when_unset(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert git_remote_url(repo, "bare") is None


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
    _git(["clone", "-o", "bare", str(bare), str(clone)], cwd=tmp_path)
    return bare, clone


def _advance_bare_main(bare: Path, tmp_path: Path) -> str:
    """Push a new commit to bare's main from a second, throwaway clone. Returns the new SHA."""
    pusher = tmp_path / "pusher"
    _git(["clone", "-o", "bare", str(bare), str(pusher)], cwd=tmp_path)
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
def test_refresh_main_opportunistically_updates_bare_main_tracking_ref(tmp_path: Path) -> None:
    """The rebase path (git_helpers's fetch during resume classification) relies on this."""
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    new_sha = _advance_bare_main(bare, tmp_path)

    refresh_main(clone)

    assert _git(["rev-parse", "refs/remotes/bare/main"], cwd=clone).stdout.decode().strip() == new_sha


def test_refresh_main_warns_and_returns_when_no_bare_remote(
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
    _git(["clone", "-o", "bare", str(bare), str(builder)], cwd=tmp_path)
    _git(["checkout", "feat/x"], cwd=builder)
    # Real callers park reviewer/builder on 'main' before cleanup (git branch -d refuses to
    # delete the branch a worktree currently has checked out).
    _git(["checkout", "main"], cwd=source)
    _git(["checkout", "main"], cwd=builder)

    delete_merged_branch(canon, (source, builder), "feat/x")

    assert not git_branch_exists(source, "feat/x")
    assert not git_branch_exists(builder, "feat/x")
    assert not git_ref_exists_remote(canon, "feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_delete_merged_branch_skips_everything_when_still_checked_out(tmp_path: Path) -> None:
    _bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    transfer_branch_to_main(source, canon, branch="feat/x")
    assert git_current_branch(source) == "feat/x"

    delete_merged_branch(canon, (source,), "feat/x")

    assert git_branch_exists(source, "feat/x")
    assert git_ref_exists_remote(canon, "feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_delete_merged_branch_warns_without_raising_when_not_merged(tmp_path: Path) -> None:
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    other = tmp_path / "other"
    _git(["clone", "-o", "bare", str(bare), str(other)], cwd=tmp_path)
    del bare

    delete_merged_branch(canon, (source, other), "feat/x")  # must not raise

    assert git_branch_exists(source, "feat/x")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_delete_merged_branch_is_a_noop_when_already_deleted(tmp_path: Path) -> None:
    """Resumed cleanup: a branch already deleted from bare must not be reported as unmerged."""
    bare, source, canon = _setup_transfer_scenario(tmp_path, "feat/x")
    transfer_branch_to_main(source, canon, branch="feat/x")
    other = tmp_path / "other"
    _git(["clone", "-o", "bare", str(bare), str(other)], cwd=tmp_path)
    _git(["checkout", "main"], cwd=source)
    delete_merged_branch(canon, (source, other), "feat/x")
    del bare

    delete_merged_branch(canon, (source, other), "feat/x")  # must not raise


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_ahead_of_remote_counts_unpushed_commits(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "bare"
    _git(["clone", "--bare", str(source), str(bare)], cwd=tmp_path)
    clone = tmp_path / "clone"
    _git(["clone", "-o", "bare", str(bare), str(clone)], cwd=tmp_path)
    _git(["config", "user.email", "test@example.com"], cwd=clone)
    _git(["config", "user.name", "Test"], cwd=clone)

    assert git_commits_ahead_of_remote(clone, "main") == 0

    (clone / "local.txt").write_text("local\n", encoding="utf-8")
    _git(["add", "."], cwd=clone)
    _git(["commit", "-m", "local commit"], cwd=clone)

    assert git_commits_ahead_of_remote(clone, "main") == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_ahead_of_remote_raises_when_remote_branch_is_missing(tmp_path: Path) -> None:
    """Fail loudly: a silent 0 would tell the caller nothing would be lost."""
    repo = _init_repo(tmp_path / "repo")

    with pytest.raises(WorkflowError, match="cannot compare"):
        git_commits_ahead_of_remote(repo, "main")


# ---------------------------------------------------------------------------
# git_commits_not_in_bare
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_not_in_bare_zero_for_clean_clone(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)

    assert git_commits_not_in_bare(clone) == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_not_in_bare_counts_local_commits_on_main(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)
    for i in range(2):
        (clone / f"local{i}.txt").write_text("local\n", encoding="utf-8")
        _git(["add", "."], cwd=clone)
        _git(["commit", "-m", f"local commit {i}"], cwd=clone)

    assert git_commits_not_in_bare(clone) == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_not_in_bare_counts_brand_new_branch(tmp_path: Path) -> None:
    """A branch bare has never seen: its commit is reachable from no bare/* ref at all."""
    _bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    (clone / "new.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "."], cwd=clone)
    _git(["commit", "-m", "new branch commit"], cwd=clone)

    assert git_commits_not_in_bare(clone) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_not_in_bare_zero_after_push(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    (clone / "new.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "."], cwd=clone)
    _git(["commit", "-m", "new branch commit"], cwd=clone)
    _git(["push", "-u", "bare", "feat/x"], cwd=clone)
    _git(["fetch", "bare"], cwd=clone)

    assert git_commits_not_in_bare(clone) == 0


# ---------------------------------------------------------------------------
# git_status_porcelain(untracked=)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_status_porcelain_untracked_false_ignores_untracked_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "untracked.txt").write_text("stray\n", encoding="utf-8")

    assert git_status_porcelain(repo) != ""
    assert git_status_porcelain(repo, untracked=False) == ""


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_status_porcelain_untracked_false_still_reports_tracked_modifications(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    assert git_status_porcelain(repo, untracked=False) != ""


# ---------------------------------------------------------------------------
# git_push_refspec
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_push_refspec_pushes_named_branch(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    (clone / "new.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "."], cwd=clone)
    _git(["commit", "-m", "new branch commit"], cwd=clone)

    git_push_refspec(clone, "feat/x:refs/heads/feat/x")

    assert (
        _git(["rev-parse", "refs/heads/feat/x"], cwd=bare).stdout.decode().strip()
        == _git(
            ["rev-parse", "HEAD"],
            cwd=clone,
        )
        .stdout.decode()
        .strip()
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_push_refspec_dry_run_does_not_mutate(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    (clone / "new.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "."], cwd=clone)
    _git(["commit", "-m", "new branch commit"], cwd=clone)

    git_push_refspec(clone, "feat/x:refs/heads/feat/x", dry_run=True)

    assert git_rev_parse(bare, "refs/heads/feat/x") is None


# ---------------------------------------------------------------------------
# resolve_default_branch
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
@pytest.mark.parametrize(
    ("upstream_branch", "delete_origin_head", "expected"),
    [
        pytest.param("trunk", False, "trunk", id="prefers_origin_head"),
        # A clone that never got origin/HEAD set (e.g. a shallow/partial mirror).
        pytest.param("main", True, "main", id="falls_back_to_main"),
        pytest.param("master", True, "master", id="falls_back_to_master"),
        pytest.param("release/2.x", False, "release/2.x", id="keeps_slashes_in_branch_name"),
    ],
)
def test_resolve_default_branch(
    tmp_path: Path,
    upstream_branch: str,
    *,
    delete_origin_head: bool,
    expected: str,
) -> None:
    upstream = _init_repo(tmp_path / "upstream")
    _git(["branch", "-m", upstream_branch], cwd=upstream)
    clone = tmp_path / "clone"
    _git(["clone", str(upstream), str(clone)], cwd=tmp_path)
    if delete_origin_head:
        _git(["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=clone)

    assert resolve_default_branch(clone, "origin") == expected


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_resolve_default_branch_raises_when_unresolvable(tmp_path: Path) -> None:
    upstream = _init_repo(tmp_path / "upstream")
    _git(["branch", "-m", "trunk"], cwd=upstream)
    clone = tmp_path / "clone"
    _git(["clone", str(upstream), str(clone)], cwd=tmp_path)
    _git(["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=clone)

    with pytest.raises(WorkflowError, match="cannot resolve origin's default branch"):
        resolve_default_branch(clone, "origin")


def _commit_file(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-m", message], cwd=repo)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_resolve_default_branch_of_bare_remote(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)

    assert resolve_default_branch(clone, "bare") == "main"


# ---------------------------------------------------------------------------
# git_commits_between / git_commits_behind_bare
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_between_counts_the_range(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    base = git_rev_parse(repo, "HEAD")
    assert base is not None
    _commit_file(repo, "a.txt", "a")
    _commit_file(repo, "b.txt", "b")

    assert git_commits_between(repo, base, "HEAD") == 2
    assert git_commits_between(repo, "HEAD", base) == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_behind_bare_counts_only_the_branchs_own_counterpart(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _advance_bare_main(bare, tmp_path)
    # Unrelated parallel work on another bare branch must not count.
    pusher = tmp_path / "pusher"
    _git(["checkout", "-b", "feat/other"], cwd=pusher)
    _commit_file(pusher, "other.txt", "other")
    _git(["push", "bare", "feat/other"], cwd=pusher)
    _git(["fetch", "bare"], cwd=clone)

    assert git_commits_behind_bare(clone) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_commits_behind_bare_none_without_counterpart_or_on_detached_head(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/new"], cwd=clone)
    assert git_commits_behind_bare(clone) is None

    _git(["checkout", "--detach"], cwd=clone)
    assert git_commits_behind_bare(clone) is None


# ---------------------------------------------------------------------------
# git_changed_files_not_in_bare / git_diff_text_not_in_bare
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_not_in_bare_helpers_cover_only_unpushed_commits(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    _commit_file(clone, "pushed.txt", "pushed")
    _git(["push", "-u", "bare", "feat/x"], cwd=clone)
    _commit_file(clone, "b.txt", "b")
    _commit_file(clone, "a.txt", "a")

    assert git_changed_files_not_in_bare(clone) == ["a.txt", "b.txt"]
    diff = git_diff_text_not_in_bare(clone)
    assert "+a.txt" in diff
    assert "+b.txt" in diff
    assert "pushed.txt" not in diff


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_diff_helpers_keep_b_prefix_with_noprefix_config(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)
    _git(["config", "diff.noprefix", "true"], cwd=clone)
    _git(["checkout", "-b", "feat/x"], cwd=clone)
    _commit_file(clone, "a.txt", "a")

    assert "+++ b/a.txt" in git_diff_text_not_in_bare(clone)
    assert "+++ b/a.txt" in git_diff_branch(clone)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_not_in_bare_helpers_empty_for_a_clean_clone(tmp_path: Path) -> None:
    _bare, clone = _make_bare_and_clone(tmp_path)

    assert git_changed_files_not_in_bare(clone) == []
    assert git_diff_text_not_in_bare(clone) == ""


# ---------------------------------------------------------------------------
# git_check_ref_format / git_list_remotes / git_fetch_remote
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
@pytest.mark.parametrize(
    ("branch", "valid"),
    [
        ("main", True),
        ("feat/widget", True),
        ("release/2.x", True),
        ("", False),
        ("-foo", False),
        ("a..b", False),
        ("HEAD", False),
        ("@{-1}", False),
        ("with space", False),
        ("trailing/", False),
    ],
)
def test_git_check_ref_format(branch: str, *, valid: bool) -> None:
    assert git_check_ref_format(branch) is valid


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_list_remotes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert git_list_remotes(repo) == []

    _git(["remote", "add", "bare", str(tmp_path / "x")], cwd=repo)
    _git(["remote", "add", "origin", str(tmp_path / "y")], cwd=repo)
    assert git_list_remotes(repo) == ["bare", "origin"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_fetch_remote_prunes_deleted_branches(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["branch", "gone"], cwd=bare)
    git_fetch_remote(clone, "bare")
    assert git_rev_parse(clone, "refs/remotes/bare/gone") is not None

    _git(["update-ref", "-d", "refs/heads/gone"], cwd=bare)
    git_fetch_remote(clone, "bare")
    assert git_rev_parse(clone, "refs/remotes/bare/gone") is not None
    git_fetch_remote(clone, "bare", prune=True)
    assert git_rev_parse(clone, "refs/remotes/bare/gone") is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_git_fetch_remote_dry_run_does_not_fetch(tmp_path: Path) -> None:
    bare, clone = _make_bare_and_clone(tmp_path)
    _git(["branch", "new"], cwd=bare)

    git_fetch_remote(clone, "bare", dry_run=True)

    assert git_rev_parse(clone, "refs/remotes/bare/new") is None


@pytest.mark.parametrize(
    ("hook_path", "mode", "expected"),
    [
        (".git-pre-commit-hook", 0o755, True),
        (".git-pre-commit-hook", 0o644, False),
        (".git/hooks/pre-commit", 0o755, True),
        (None, 0o755, False),
    ],
    ids=["tracked_executable", "tracked_not_executable", "git_hooks_only", "none"],
)
def test_pre_commit_hook_present(tmp_path: Path, hook_path: str | None, mode: int, *, expected: bool) -> None:
    if hook_path is not None:
        hook = tmp_path / hook_path
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/bash\n", encoding="utf-8")
        hook.chmod(mode)

    assert git_helpers.pre_commit_hook_present(tmp_path) is expected
