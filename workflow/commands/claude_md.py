"""CLI command for regenerating the CLAUDE.md workflow section.

This module registers the ``claude_md`` script command and exposes the
``generate_and_write_claude_md`` orchestrator for use by ``workflow init``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import click

from workflow.claude_md import (
    append_managed_section,
    compute_hash_file_contents,
    find_managed_section,
    normalise,
    render_workflow_section,
    replace_managed_section,
)
from workflow.config import WorkflowConfig
from workflow.exceptions import ClaudeMdGenerationError, WorkflowError
from workflow.registry import register_script


def generate_and_write_claude_md(
    config: WorkflowConfig,
    *,
    claude_md_path: Path | None = None,
) -> Path:
    """Regenerate CLAUDE.md and refresh the trusted hash file.

    Returns the path that was written.
    Raises :class:`~workflow.exceptions.ClaudeMdGenerationError` when markers
    are malformed or generation cannot proceed safely.

    Performs no git operations. The caller is responsible for any staging,
    committing, or pushing.
    """
    if config.project_dir is None:
        msg = "generate_and_write_claude_md: config.project_dir must be set."
        raise ClaudeMdGenerationError(msg)
    project_dir = config.project_dir
    resolved_path = claude_md_path or (project_dir / "master" / "CLAUDE.md")
    config_yaml_path = project_dir / "config.yaml"
    hash_file_path = project_dir / ".claude_md_hash"

    # Read existing content (or start fresh).
    existing_text = resolved_path.read_text(encoding="utf-8") if resolved_path.exists() else ""

    # Validate existing markers before generating.
    span = find_managed_section(existing_text)  # raises ClaudeMdGenerationError on malformed

    # Generate the managed section content.
    managed = render_workflow_section(config)

    # Splice into the file.
    if span is not None:
        new_text = replace_managed_section(existing_text, span, managed)
    else:
        new_text = append_managed_section(existing_text, managed)

    new_text = normalise(new_text)

    # Validate the result has exactly one well-formed marker pair.
    result_span = find_managed_section(new_text)
    if result_span is None:
        msg = "BUG: generated CLAUDE.md has no markers after generation."
        raise ClaudeMdGenerationError(msg)

    # Compute hash file contents.
    config_yaml_bytes = config_yaml_path.read_bytes() if config_yaml_path.exists() else b""
    hash_contents = compute_hash_file_contents(new_text, config_yaml_bytes)

    # Write atomically. CLAUDE.md is committed to git, so use 0o644;
    # the hash file is a local trust anchor that does not need to be world-readable.
    _atomic_write(resolved_path, new_text.encode("utf-8"), mode=0o644)
    _atomic_write(hash_file_path, hash_contents.encode("utf-8"))

    return resolved_path


def _atomic_write(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Write *data* to *path* atomically via a temporary file in the same directory.

    When *mode* is provided, set permissions on the temp file before the rename
    (``tempfile.mkstemp`` defaults to 0o600).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if mode is not None:
            Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise


@register_script("claude_md")
@click.option("--check", is_flag=True, help="Exit 1 if CLAUDE.md or hash would change; never writes.")
@click.pass_context
def claude_md(ctx: click.Context, check: bool) -> None:  # noqa: FBT001
    """Regenerate the auto-generated workflow section of CLAUDE.md."""
    config: WorkflowConfig = ctx.obj["config"]
    if config.project_dir is None:
        click.echo("Error: not inside a workflow project directory.", err=True)
        ctx.exit(1)
        return

    project_dir = config.project_dir
    claude_md_path = project_dir / "master" / "CLAUDE.md"
    hash_file_path = project_dir / ".claude_md_hash"
    config_yaml_path = project_dir / "config.yaml"

    try:
        existing_text = claude_md_path.read_text(encoding="utf-8") if claude_md_path.exists() else ""
        span = find_managed_section(existing_text)
        managed = render_workflow_section(config)
        new_text = (
            replace_managed_section(existing_text, span, managed)
            if span is not None
            else append_managed_section(existing_text, managed)
        )
        new_text = normalise(new_text)

        config_yaml_bytes = config_yaml_path.read_bytes() if config_yaml_path.exists() else b""
        new_hash = compute_hash_file_contents(new_text, config_yaml_bytes)

        existing_hash = hash_file_path.read_text(encoding="utf-8") if hash_file_path.exists() else ""

        claude_md_unchanged = claude_md_path.exists() and claude_md_path.read_text(encoding="utf-8") == new_text
        hash_unchanged = existing_hash == new_hash

        if claude_md_unchanged and hash_unchanged:
            click.echo("CLAUDE.md is up to date.")
            ctx.exit(0)
            return

        if check:
            parts = []
            if not claude_md_unchanged:
                parts.append("CLAUDE.md content")
            if not hash_unchanged:
                parts.append("hash file")
            click.echo(f"CLAUDE.md is out of date: {' and '.join(parts)} would change.", err=True)
            ctx.exit(1)
            return

        # Validate result markers before writing.
        result_span = find_managed_section(new_text)
        if result_span is None:
            click.echo("Error: BUG: generated CLAUDE.md has no markers.", err=True)
            ctx.exit(1)
            return

        _atomic_write(claude_md_path, new_text.encode("utf-8"), mode=0o644)
        _atomic_write(hash_file_path, new_hash.encode("utf-8"))
        click.echo("CLAUDE.md written. Review with 'git diff' in master, then commit and push.")

    except (ClaudeMdGenerationError, WorkflowError) as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)
        return

    ctx.exit(0)
