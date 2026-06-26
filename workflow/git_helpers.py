"""Git helper functions for the workflow CLI.

Provides thin wrappers around git commands that respect dry-run mode.
All helpers that run git operations use cwd=repo_path and the remote named 'origin'.
"""

from __future__ import annotations

import enum
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from workflow.exceptions import WorkflowError
from workflow.shell import error_console, run_command, run_condition_command

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


def git_status_porcelain(repo_path: Path, *, dry_run: bool = False) -> str:
    """Return ``git status --porcelain`` output for *repo_path* (empty == clean tree).

    In dry-run mode prints the command and returns an empty string (treated as clean).
    """
    if dry_run:
        run_command(["git", "status", "--porcelain"], cwd=repo_path, dry_run=True)
        return ""
    result = run_command(["git", "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True)
    assert result is not None
    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    return stdout.strip()


def git_branch_exists(repo_path: Path, branch: str, *, dry_run: bool = False) -> bool:
    """Return whether *branch* exists locally in *repo_path*.

    In dry-run mode prints the command and returns ``False`` (treated as absent).
    """
    if dry_run:
        run_command(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo_path, dry_run=True)
        return False
    result = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


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
        if b"nothing to commit" in output:
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
    master_repo: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Push *source_repo*'s current branch to bare, pull to master, merge, and push back.

    Reads the current branch from *source_repo*.
    Raises :class:`~workflow.exceptions.WorkflowError` if that branch is ``'main'``.

    Steps:
    1. ``git push origin`` in *source_repo*
    2. ``git pull`` in *master_repo*
    3. ``git merge <branch>`` in *master_repo*
    4. ``git push origin`` in *master_repo*
    5. ``git checkout main`` in *source_repo*
    2. ``git pull`` in *source_repo*
    """
    branch = git_current_branch(source_repo)
    master_branch = git_current_branch(master_repo)
    if branch == "main":
        msg = "transfer_branch_to_main: source_repo is on 'main'; expected a feature branch"
        raise WorkflowError(msg)
    if master_branch != "main":
        msg = "transfer_branch_to_main: master_repo is not on 'main'; won't clobber a feature branch"
        raise WorkflowError(msg)

    git_push(source_repo, dry_run=dry_run)
    git_pull(master_repo, dry_run=dry_run)
    # Merge via the remote-tracking ref so the branch need not exist locally.
    git_merge(master_repo, f"origin/{branch}", dry_run=dry_run)
    git_push(master_repo, dry_run=dry_run)
    git_checkout(source_repo, "main", create=False, dry_run=dry_run)
    git_pull(source_repo, dry_run=dry_run)
