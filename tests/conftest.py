"""Shared test fixtures/helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workflow.metadata import ProjectMetadata, write_metadata

if TYPE_CHECKING:
    from pathlib import Path


def create_project_marker(project_dir: Path) -> None:
    """Write a default .workflow.json, marking *project_dir* as a project root."""
    write_metadata(project_dir, ProjectMetadata())
