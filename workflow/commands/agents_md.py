"""CLI command for regenerating the AGENTS.md workflow section.

This module registers the ``agents_md`` script command and exposes the
``generate_and_write_agents_md`` orchestrator for use by ``workflow init``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click

from workflow.agents_md import (
    append_managed_section,
    compute_hash_file_contents,
    find_managed_section,
    normalise,
    render_workflow_section,
    replace_managed_section,
)
from workflow.config import WorkflowConfig
from workflow.exceptions import AgentsMdGenerationError, WorkflowError
from workflow.registry import register_script


@dataclass
class _AgentsMdUpdate:
    """Result of computing a AGENTS.md update without writing it to disk."""

    resolved_path: Path
    hash_file_path: Path
    new_text: str  # normalised, ready to write
    new_hash: str
    agents_md_unchanged: bool  # True when on-disk content already matches new_text
    hash_unchanged: bool  # True when on-disk hash already matches new_hash


def _prepare_agents_md_update(
    config: WorkflowConfig,
    agents_md_path: Path | None = None,
) -> _AgentsMdUpdate:
    """Compute the new AGENTS.md content and hash without writing anything.

    Raises :class:`~workflow.exceptions.AgentsMdGenerationError` when
    ``config.project_dir`` is unset, markers are malformed, or the generated
    output is internally inconsistent.
    """
    if config.project_dir is None:
        msg = "config.project_dir must be set to generate AGENTS.md."
        raise AgentsMdGenerationError(msg)
    project_dir = config.project_dir
    resolved_path = agents_md_path or (project_dir / "canon" / "AGENTS.md")
    config_yaml_path = project_dir / "config.yaml"
    hash_file_path = project_dir / ".agents_md_hash"

    # Read existing content (or start fresh).
    existing_text = resolved_path.read_text(encoding="utf-8") if resolved_path.exists() else ""

    # Validate existing markers before generating.
    span = find_managed_section(existing_text)  # raises AgentsMdGenerationError on malformed

    # Generate the managed section content and splice into the file.
    managed = render_workflow_section(config)
    new_text = (
        replace_managed_section(existing_text, span, managed)
        if span is not None
        else append_managed_section(existing_text, managed)
    )
    new_text = normalise(new_text)

    # Sanity-check: the generated file must have exactly one marker pair.
    if find_managed_section(new_text) is None:
        msg = "BUG: generated AGENTS.md has no markers after generation."
        raise AgentsMdGenerationError(msg)

    config_yaml_bytes = config_yaml_path.read_bytes() if config_yaml_path.exists() else b""
    new_hash = compute_hash_file_contents(new_text, config_yaml_bytes)

    existing_hash = hash_file_path.read_text(encoding="utf-8") if hash_file_path.exists() else ""
    agents_md_unchanged = resolved_path.exists() and resolved_path.read_text(encoding="utf-8") == new_text
    hash_unchanged = existing_hash == new_hash

    return _AgentsMdUpdate(
        resolved_path=resolved_path,
        hash_file_path=hash_file_path,
        new_text=new_text,
        new_hash=new_hash,
        agents_md_unchanged=agents_md_unchanged,
        hash_unchanged=hash_unchanged,
    )


def generate_and_write_agents_md(
    config: WorkflowConfig,
    *,
    agents_md_path: Path | None = None,
) -> Path:
    """Regenerate AGENTS.md and refresh the trusted hash file.

    Returns the path that was written.
    Raises :class:`~workflow.exceptions.AgentsMdGenerationError` when markers
    are malformed or generation cannot proceed safely.

    Performs no git operations. The caller is responsible for any staging,
    committing, or pushing.
    """
    update = _prepare_agents_md_update(config, agents_md_path)
    # Write atomically. AGENTS.md is committed to git, so use 0o644;
    # the hash file is a local trust anchor that does not need to be world-readable.
    _atomic_write(update.resolved_path, update.new_text.encode("utf-8"), mode=0o644)
    _atomic_write(update.hash_file_path, update.new_hash.encode("utf-8"))
    return update.resolved_path


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


@register_script("agents_md")
@click.option("--check", is_flag=True, help="Exit 1 if AGENTS.md or hash would change; never writes.")
@click.pass_context
def agents_md(ctx: click.Context, check: bool) -> None:  # noqa: FBT001
    """Regenerate the auto-generated workflow section of AGENTS.md."""
    config: WorkflowConfig = ctx.obj["config"]
    if config.project_dir is None:
        click.echo("Error: not inside a workflow project directory.", err=True)
        ctx.exit(1)
        return

    try:
        update = _prepare_agents_md_update(config)

        if update.agents_md_unchanged and update.hash_unchanged:
            click.echo("AGENTS.md is up to date.")
            ctx.exit(0)
            return

        if check:
            parts = []
            if not update.agents_md_unchanged:
                parts.append("AGENTS.md content")
            if not update.hash_unchanged:
                parts.append("hash file")
            click.echo(f"AGENTS.md is out of date: {' and '.join(parts)} would change.", err=True)
            ctx.exit(1)
            return

        _atomic_write(update.resolved_path, update.new_text.encode("utf-8"), mode=0o644)
        _atomic_write(update.hash_file_path, update.new_hash.encode("utf-8"))
        click.echo("AGENTS.md written. Review with 'git diff' in canon, then commit and push.")

    except (AgentsMdGenerationError, WorkflowError) as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)
        return

    ctx.exit(0)
