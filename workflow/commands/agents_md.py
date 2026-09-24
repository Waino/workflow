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
from workflow.layout import Layout
from workflow.metadata import ProjectMetadata, ProjectMode
from workflow.names import DOCS_REPO, WorktreeName
from workflow.registry import register_script

BODY_OF_AGENTS_FILENAME = "body_of_agents.md"
"""Hand-written AGENTS.md body, version controlled in the docs repo (oob mode only)."""


@dataclass
class _AgentsMdUpdate:
    """Result of computing a AGENTS.md update without writing it to disk."""

    write_targets: list[Path]  # one per worktree root that gets an AGENTS.md copy
    hash_file_path: Path
    new_text: str  # normalised, ready to write
    new_hash: str
    agents_md_unchanged: bool  # True when every target already matches new_text
    hash_unchanged: bool  # True when on-disk hash already matches new_hash


def _read_body(layout: Layout) -> tuple[str, Path]:
    """Return (existing body text, the single in-band target) for in-band mode.

    In-band, the body and the single write target are the same file: the
    hand-written prose and the managed section already live together in
    ``canon/AGENTS.md``.
    """
    target = layout.worktree_root(WorktreeName.canon) / "AGENTS.md"
    return (target.read_text(encoding="utf-8") if target.exists() else ""), target


def prepare_agents_md_update(config: WorkflowConfig) -> _AgentsMdUpdate:
    """Compute the new AGENTS.md content and hash without writing anything.

    In-band: the body is the hand-written prose already in ``canon/AGENTS.md``;
    the managed section is spliced in place; there is a single write target.

    Oob: the body is version controlled as ``docs/body_of_agents.md`` and must not
    itself contain markers; the managed section is appended to render each of the
    four worktree-root ``AGENTS.md`` copies (unversioned build artifacts).

    Raises :class:`~workflow.exceptions.AgentsMdGenerationError` when
    ``config.project_dir`` is unset, markers are malformed, or the generated
    output is internally inconsistent.
    """
    if config.project_dir is None:
        msg = "config.project_dir must be set to generate AGENTS.md."
        raise AgentsMdGenerationError(msg)
    project_dir = config.project_dir
    # A fresh default metadata is used as a fallback since generate_and_write_agents_md is also
    # called from `init`, before .workflow.json exists; init threads the intended metadata through
    # config.metadata explicitly (including for --oob) so this fallback is in-band-only in practice.
    layout = Layout(project_dir=project_dir, metadata=config.metadata or ProjectMetadata())
    config_yaml_path = project_dir / "config.yaml"
    hash_file_path = project_dir / ".agents_md_hash"

    managed = render_workflow_section(config)

    if layout.metadata.mode == ProjectMode.oob:
        body_path = layout.git_repo_root(WorktreeName.canon, DOCS_REPO) / BODY_OF_AGENTS_FILENAME
        body_text = body_path.read_text(encoding="utf-8") if body_path.exists() else ""
        if find_managed_section(body_text) is not None:
            msg = f"{body_path} must not contain workflow markers; they belong in the generated AGENTS.md only."
            raise AgentsMdGenerationError(msg)
        new_text = normalise(append_managed_section(body_text, managed))
        write_targets = [layout.worktree_root(worktree) / "AGENTS.md" for worktree in WorktreeName]
    else:
        existing_text, target = _read_body(layout)
        span = find_managed_section(existing_text)  # raises AgentsMdGenerationError on malformed
        new_text = (
            replace_managed_section(existing_text, span, managed)
            if span is not None
            else append_managed_section(existing_text, managed)
        )
        new_text = normalise(new_text)
        write_targets = [target]

    # Sanity-check: the generated file must have exactly one marker pair.
    if find_managed_section(new_text) is None:
        msg = "BUG: generated AGENTS.md has no markers after generation."
        raise AgentsMdGenerationError(msg)

    config_yaml_bytes = config_yaml_path.read_bytes() if config_yaml_path.exists() else b""
    new_hash = compute_hash_file_contents(new_text, config_yaml_bytes)

    existing_hash = hash_file_path.read_text(encoding="utf-8") if hash_file_path.exists() else ""
    agents_md_unchanged = all(
        target.exists() and target.read_text(encoding="utf-8") == new_text for target in write_targets
    )
    hash_unchanged = existing_hash == new_hash

    return _AgentsMdUpdate(
        write_targets=write_targets,
        hash_file_path=hash_file_path,
        new_text=new_text,
        new_hash=new_hash,
        agents_md_unchanged=agents_md_unchanged,
        hash_unchanged=hash_unchanged,
    )


def generate_and_write_agents_md(
    config: WorkflowConfig,
) -> list[Path]:
    """Regenerate AGENTS.md (every worktree copy) and refresh the trusted hash file.

    Returns the paths that were written.
    Raises :class:`~workflow.exceptions.AgentsMdGenerationError` when markers
    are malformed or generation cannot proceed safely.

    Performs no git operations. The caller is responsible for any staging,
    committing, or pushing.
    """
    update = prepare_agents_md_update(config)
    # Write atomically. AGENTS.md is committed to git in-band, so use 0o644;
    # the hash file is a local trust anchor that does not need to be world-readable.
    for target in update.write_targets:
        _atomic_write(target, update.new_text.encode("utf-8"), mode=0o644)
    _atomic_write(update.hash_file_path, update.new_hash.encode("utf-8"))
    return update.write_targets


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
        update = prepare_agents_md_update(config)

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

        for target in update.write_targets:
            _atomic_write(target, update.new_text.encode("utf-8"), mode=0o644)
        _atomic_write(update.hash_file_path, update.new_hash.encode("utf-8"))
        click.echo("AGENTS.md written. Review with 'git diff' in canon, then commit and push.")

    except (AgentsMdGenerationError, WorkflowError) as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)
        return

    ctx.exit(0)
