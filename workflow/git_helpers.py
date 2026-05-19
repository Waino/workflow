"""Git helper functions for the workflow CLI.

Provides thin wrappers around git commands that respect dry-run mode.
All helpers that run git operations use cwd=repo_path and the remote named 'origin'.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from workflow.exceptions import WorkflowError
from workflow.shell import run_command


def git_pull(repo_path: Path, *, dry_run: bool = False) -> None:
    """Run ``git pull`` in *repo_path*.

    In dry-run mode prints the command without executing it.
    Raises :class:`subprocess.CalledProcessError` on non-zero exit.
    """
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


def git_merge(repo_path: Path, branch: str, *, dry_run: bool = False) -> None:
    """Merge *branch* into the current branch in *repo_path*."""
    run_command(["git", "merge", "--", branch], cwd=repo_path, dry_run=dry_run)


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
    """
    branch = git_current_branch(source_repo)
    if branch == "main":
        msg = "transfer_branch_to_main: source_repo is on 'main'; expected a feature branch"
        raise WorkflowError(msg)
    git_push(source_repo, dry_run=dry_run)
    git_pull(master_repo, dry_run=dry_run)
    git_merge(master_repo, branch, dry_run=dry_run)
    git_push(master_repo, dry_run=dry_run)
