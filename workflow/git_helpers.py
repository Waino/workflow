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
