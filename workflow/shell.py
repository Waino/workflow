"""Shell command execution helpers for the workflow CLI."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from rich.console import Console

console = Console()
"""Shared Rich console used for all CLI stdout output."""
error_console = Console(stderr=True)
"""Shared Rich console used for all CLI stderr output."""


def print_workflow_error(exc: BaseException) -> None:
    """Print a workflow exception to error_console.

    Exceptions that implement Rich's ``__rich__`` protocol render themselves
    (e.g. :class:`~workflow.plan_resolver.SourcesDisagreeError` produces a
    colorized, aligned table). All other exceptions are wrapped in a ``[red]``
    markup tag for visibility.
    """
    if hasattr(exc, "__rich__"):
        error_console.print(exc)
    else:
        error_console.print(f"[red]{exc}[/red]")


def run_command(
    args: list[str],
    *,
    dry_run: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess[bytes] | None:
    """Run a shell command, or print it if *dry_run* is ``True``.

    Passes ``**kwargs`` through to :func:`subprocess.run`
    (e.g. ``cwd``, ``env``, ``capture_output``).

    Returns the :class:`subprocess.CompletedProcess` result, or ``None`` in
    dry-run mode.

    Raises :class:`subprocess.CalledProcessError` on a non-zero exit code
    (``check=True`` is always set).
    """
    if dry_run:
        console.print(f"[dim]would run:[/dim] {shlex.join(args)}")
        return None
    return subprocess.run(args, check=True, **kwargs)  # noqa: S603


def run_condition_command(
    args: list[str],
    **kwargs: Any,
) -> int:
    """Run a shell command, for use as a condition.

    Passes ``**kwargs`` through to :func:`subprocess.run`
    (e.g. ``cwd``, ``env``).

    Returns the return value of the command.

    NOTE: the command MUST be safe to run in dry-run mode.
    """
    result = subprocess.run(args, check=False, **kwargs)  # noqa: S603
    assert result is not None
    return result.returncode
