#!/usr/bin/env python
"""Single-use migration script for the 023 (out-of-band) plan series.

Idempotent: safe to re-run. Extended by later plans in the 023 series.
"""

from __future__ import annotations

from pathlib import Path

import click

from workflow.metadata import LEGACY_ROOT_MARKER, METADATA_FILENAME, ProjectMetadata, write_metadata


def migrate_metadata_file(project_dir: Path, *, dry_run: bool) -> None:
    """Step 1: write a default .workflow.json if absent, then delete the legacy marker."""
    metadata_path = project_dir / METADATA_FILENAME
    if not metadata_path.exists():
        click.echo(f"writing default {metadata_path}")
        if not dry_run:
            write_metadata(project_dir, ProjectMetadata())
    else:
        click.echo(f"{metadata_path} already exists; skipping")

    legacy_marker = project_dir / LEGACY_ROOT_MARKER
    if legacy_marker.exists():
        click.echo(f"removing legacy {legacy_marker}")
        if not dry_run:
            legacy_marker.unlink()
    else:
        click.echo(f"{legacy_marker} not present; skipping")


@click.command()
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Project root to migrate.",
)
@click.option("--dry-run", is_flag=True, help="Print actions without performing them.")
def main(project_dir: Path, *, dry_run: bool) -> None:
    """Migrate a pre-023 project directory to the current on-disk layout."""
    migrate_metadata_file(project_dir, dry_run=dry_run)


if __name__ == "__main__":
    main()
