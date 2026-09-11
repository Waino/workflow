#!/usr/bin/env python
"""Single-use migration script for the 023 (out-of-band) plan series.

Idempotent: safe to re-run. Extended by later plans in the 023 series.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from workflow.metadata import LEGACY_ROOT_MARKER, METADATA_FILENAME, ProjectMetadata, write_metadata


def migrate_metadata_file(project_dir: Path, *, dry_run: bool) -> None:
    """Step 1: write a default .workflow.json if absent, then delete the legacy marker."""
    metadata_path = project_dir / METADATA_FILENAME
    if not metadata_path.exists():
        print(f"writing default {metadata_path}")
        if not dry_run:
            write_metadata(project_dir, ProjectMetadata())
    else:
        print(f"{metadata_path} already exists; skipping")

    legacy_marker = project_dir / LEGACY_ROOT_MARKER
    if legacy_marker.exists():
        print(f"removing legacy {legacy_marker}")
        if not dry_run:
            legacy_marker.unlink()
    else:
        print(f"{legacy_marker} not present; skipping")


def main() -> None:
    """Parse args and run the migration steps."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True, help="Project root to migrate.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without performing them.")
    args = parser.parse_args()

    migrate_metadata_file(args.project_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
