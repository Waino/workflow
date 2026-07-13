"""Git helper functions for the workflow CLI.

Provides thin wrappers around git commands that respect dry-run mode.
All helpers that run git operations use cwd=repo_path and the remote named 'origin'.

Read-only guard queries (status/existence/ancestry checks used as preconditions or
resume postconditions) always run for real, even in dry-run mode, so resume previews
stay accurate. Only helpers that perform mutations honor dry_run.
"""

from __future__ import annotations

import enum
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from workflow.shell import error_console, run_command, run_condition_command
from workflow.steps import require

GIT_REVPARSE_NO_UPSTREAM = 128


class CommitResult(enum.Enum):
    """Outcome of :func:`git_commit_if_changed`."""

    COMMITTED = "committed"
    NOTHING_TO_COMMIT = "nothing_to_commit"


def git_pull(repo_path: Path, *, dry_run: bool = False, skip_if_no_upstream: bool = False) -> None:
    """Run ``git pull`` in *repo_path*.

    In dry-run mode prints the command without executing it.
    Raises :class:`subprocess.CalledProcessError` on non-zero exit.
    """
    if skip_if_no_upstream:
        returncode = run_condition_command(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=repo_path,
        )
        if returncode == GIT_REVPARSE_NO_UPSTREAM:
            error_console.print("[yellow]\\[!] Branch has no upstream, skipping pull.[/yellow]")
            return
    run_command(["git", "pull"], cwd=repo_path, dry_run=dry_run)


def git_current_branch(repo_path: Path) -> str:
    """Return the name of the current branch in *repo_path*."""
    result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    assert result is not None
    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    return stdout.strip()


def git_commit_hash(repo_path: Path, *, short: bool = True) -> str:
    """Return the current commit hash in *repo_path*."""
    command = ["git", "rev-parse"]
    if short:
        command.append("--short")
    command.append("HEAD")
    result = run_command(command, cwd=repo_path, capture_output=True, text=True)
    assert result is not None
    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    return stdout.strip()


def git_push(repo_path: Path, *, dry_run: bool = False) -> None:
    """Run ``git push origin`` in *repo_path*."""
    run_command(["git", "push", "origin"], cwd=repo_path, dry_run=dry_run)


def git_fetch(repo_path: Path, *, dry_run: bool = False) -> None:
    """Run ``git fetch origin`` in *repo_path*."""
    run_command(["git", "fetch", "origin"], cwd=repo_path, dry_run=dry_run)


def git_status(repo_path: Path, *, dry_run: bool = False) -> None:
    """Print ``git status`` (short, with branch header) for *repo_path*."""
    run_command(["git", "status", "--short", "--branch"], cwd=repo_path, dry_run=dry_run)


def git_status_porcelain(repo_path: Path) -> str:
    """Return ``git status --porcelain`` output for *repo_path* (empty == clean tree).

    Read-only guard query: always runs for real, even in dry-run (see module docstring).
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode().strip()


def git_branch_exists(repo_path: Path, branch: str) -> bool:
    """Return whether *branch* exists locally in *repo_path*.

    Read-only guard query: always runs for real, even in dry-run (see module docstring).
    """
    result = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def git_ref_exists_remote(repo_path: Path, ref: str) -> bool:
    """Return whether ``origin`` has a branch named *ref* in *repo_path*.

    Queries the remote directly (``git ls-remote --heads``) rather than relying on
    local remote-tracking refs, since a prior ``fetch`` cannot be assumed. Read-only
    guard query: always runs for real, even in dry-run.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "ls-remote", "--exit-code", "--heads", "origin", ref],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def git_is_ancestor(repo_path: Path, ancestor: str, descendant: str) -> bool:
    """Return whether *ancestor* is an ancestor of (or equal to) *descendant* in *repo_path*.

    Read-only guard query: always runs for real, even in dry-run.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def git_config_get(repo_path: Path, key: str) -> str | None:
    """Return the configured value for *key* in *repo_path*, or ``None`` if unset.

    Read-only guard query: always runs for real, even in dry-run.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "config", "--get", key],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode().strip()


def git_remote_url(repo_path: Path, name: str) -> str | None:
    """Return the configured URL for remote *name* in *repo_path*, or ``None`` if unset.

    Read-only guard query: always runs for real, even in dry-run.
    """
    return git_config_get(repo_path, f"remote.{name}.url")


def git_index_lock_present(repo_path: Path) -> bool:
    """Return whether a leftover ``index.lock`` is present in *repo_path*'s git dir.

    Handles both worktree repos (lock at ``.git/index.lock``) and bare repos (lock at
    ``index.lock`` directly under *repo_path*). Read-only guard query: always runs for
    real, even in dry-run.
    """
    git_dir = repo_path / ".git"
    lock_dir = git_dir if git_dir.is_dir() else repo_path
    return (lock_dir / "index.lock").exists()


def git_is_valid_repo(repo_path: Path, *, bare: bool = False) -> bool:
    """Return whether *repo_path* is a valid git repository, matching the expected *bare*-ness.

    Read-only guard query: always runs for real, even in dry-run.
    """
    if not repo_path.is_dir():
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--is-bare-repository"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return (result.stdout.decode().strip() == "true") == bare


def git_merge(repo_path: Path, branch: str, *, dry_run: bool = False) -> None:
    """Merge *branch* into the current branch in *repo_path*."""
    run_command(["git", "merge", "--ff-only", "--", branch], cwd=repo_path, dry_run=dry_run)


def git_checkout(repo_path: Path, branch: str, *, create: bool = False, dry_run: bool = False) -> None:
    """Check out *branch* in *repo_path*, optionally creating it with ``-b``."""
    args = ["git", "checkout"]
    if create:
        args.append("-b")
    args.append(branch)
    run_command(args, cwd=repo_path, dry_run=dry_run)


def git_add(repo_path: Path, file_path: Path, *, dry_run: bool = False) -> None:
    """Stage *file_path* in *repo_path*.

    *file_path* may be an absolute path or relative to *repo_path*.
    In dry-run mode prints the command without executing it.
    """
    run_command(["git", "add", str(file_path)], cwd=repo_path, dry_run=dry_run)


def git_commit(repo_path: Path, message: str, *, dry_run: bool = False, no_verify: bool = False) -> None:
    """Stage all tracked changes (``git add -u``) and commit in *repo_path*.

    Pass ``no_verify=True`` to skip pre-commit hooks (``--no-verify``).
    """
    run_command(["git", "add", "-u"], cwd=repo_path, dry_run=dry_run)
    commit_args = ["git", "commit", "-m", message]
    if no_verify:
        commit_args.append("--no-verify")
    run_command(commit_args, cwd=repo_path, dry_run=dry_run)


def git_commit_if_changed(
    repo_path: Path,
    message: str,
    *,
    dry_run: bool = False,
    no_verify: bool = False,
) -> CommitResult:
    """Commit staged changes in *repo_path*, returning a sentinel when there is nothing to commit.

    Unlike :func:`git_commit`, this distinguishes the "nothing to commit" no-op
    (git exits 1 with that message on stdout) from genuine git failures, which
    are re-raised unchanged. Callers should pre-stage their changes (or use the
    ``git add -u`` performed by :func:`git_commit`).
    """
    if dry_run:
        commit_args = ["git", "commit", "-m", message, *(["--no-verify"] if no_verify else [])]
        run_command(commit_args, cwd=repo_path, dry_run=True)
        return CommitResult.COMMITTED

    commit_args = ["git", "commit", "-m", message]
    if no_verify:
        commit_args.append("--no-verify")
    try:
        subprocess.run(commit_args, cwd=repo_path, check=True, capture_output=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or b"") + (exc.stderr or b"")
        # Git phrases the no-op differently when untracked (but unstaged) files are present.
        if any(
            pattern in output
            for pattern in (b"nothing to commit", b"nothing added to commit", b"no changes added to commit")
        ):
            return CommitResult.NOTHING_TO_COMMIT
        raise
    return CommitResult.COMMITTED


def git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
    """Move *source* to *target* and stage the change in *repo_path*.

    *source* and *target* may be absolute paths; git is invoked with ``cwd=repo_path``.
    """
    run_command(["git", "mv", str(source), str(target)], cwd=repo_path, dry_run=dry_run)


def git_diff_branch(repo_path: Path, *extra_args: str) -> str:
    """Return ``git diff origin/main...HEAD`` output as a decoded string.

    Optionally passes *extra_args* to git (e.g. ``"--name-only"``).
    Raises :class:`subprocess.CalledProcessError` on non-zero exit.
    """
    result = run_command(
        ["git", "diff", "origin/main...HEAD", *extra_args],
        cwd=repo_path,
        capture_output=True,
    )
    assert result is not None
    return result.stdout.decode()


def transfer_branch_to_main(
    source_repo: Path,
    canon_repo: Path,
    *,
    branch: str | None = None,
    dry_run: bool = False,
) -> None:
    """Push *source_repo*'s branch to bare, pull to canon, merge, and push back.

    Uses *branch* as the merge ref when given (so callers with a fixed feature-branch
    convention, e.g. ``accept``, don't depend on what *source_repo* happens to be
    checked out to); otherwise reads *source_repo*'s current branch.

    Every step is individually idempotent (a merge of an already-merged ref is a
    harmless no-op, checkout/pull no-op when already on ``main``), so re-running the
    whole helper on a partially-completed prior transfer is safe -- including when
    *source_repo* is already on ``main`` (a mid-transfer resume), which reduces to a
    harmless main-into-main merge. Callers that want to skip the transfer altogether
    on resume should not call this at all (see each command's classify step).

    Raises :class:`~workflow.exceptions.WorkflowError` if *canon_repo* is not on
    ``'main'`` (it always should be at rest; this guards against clobbering a feature
    branch checked out there by mistake).

    Steps:
    1. ``git push origin`` in *source_repo*
    2. ``git pull`` in *canon_repo*
    3. ``git merge <branch>`` in *canon_repo*
    4. ``git push origin`` in *canon_repo*
    5. ``git checkout main`` in *source_repo* (skipped if already on ``main``)
    6. ``git pull`` in *source_repo*
    """
    require(
        git_current_branch(canon_repo) == "main",
        "transfer_branch_to_main: canon_repo is not on 'main'; won't clobber a feature branch",
    )
    merge_ref = f"origin/{branch or git_current_branch(source_repo)}"

    git_push(source_repo, dry_run=dry_run)
    git_pull(canon_repo, dry_run=dry_run)
    # Merge via the remote-tracking ref so the branch need not exist locally.
    git_merge(canon_repo, merge_ref, dry_run=dry_run)
    git_push(canon_repo, dry_run=dry_run)
    if git_current_branch(source_repo) != "main":
        git_checkout(source_repo, "main", create=False, dry_run=dry_run)
    git_pull(source_repo, dry_run=dry_run)
