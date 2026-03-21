"""Shell command execution helpers for the workflow CLI."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from rich.console import Console

console = Console()
"""Shared Rich console used for all CLI output."""


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
