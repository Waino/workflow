"""Shared test fixtures/helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workflow.layout import Layout
from workflow.metadata import ProjectMetadata, write_metadata

if TYPE_CHECKING:
    from pathlib import Path


def create_project_marker(project_dir: Path) -> None:
    """Write a default .workflow.json, marking *project_dir* as a project root."""
    write_metadata(project_dir, ProjectMetadata())


def make_layout(project_dir: Path) -> Layout:
    """Build a default in-band Layout rooted at *project_dir*, for tests that need path math."""
    return Layout(project_dir=project_dir, metadata=ProjectMetadata())
