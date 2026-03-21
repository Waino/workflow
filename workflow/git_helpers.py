"""Git helper functions for the workflow CLI.

Provides thin wrappers around git commands that respect dry-run mode.
Plan 005 will expand this module with additional helpers (push, fetch, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workflow.shell import run_command

if TYPE_CHECKING:
    from pathlib import Path


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
