"""Shared test fixtures/helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from workflow.layout import Layout
from workflow.metadata import CodeRepoMeta, ProjectMetadata, ProjectMode, write_metadata

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def create_project_marker(project_dir: Path) -> None:
    """Write a default .workflow.json, marking *project_dir* as a project root."""
    write_metadata(project_dir, ProjectMetadata())


def make_layout(project_dir: Path) -> Layout:
    """Build a default in-band Layout rooted at *project_dir*, for tests that need path math."""
    return Layout(project_dir=project_dir, metadata=ProjectMetadata())


def make_project(
    project_dir: Path,
    *,
    mode: ProjectMode = ProjectMode.in_band,
    code_repos: Sequence[str] = (),
) -> Layout:
    """Write ``.workflow.json`` for *mode*/*code_repos* and return the resulting Layout.

    Filesystem-only (no real git repos): for tests whose value is path math or plan
    resolution, not git behaviour (e.g. ``test_plan_resolver.py``, ``test_state.py``).
    Tests that need real git repos keep using the heavier ``_git``/``_init_repo``-style
    helpers in ``tests/test_init_oob.py``.
    """
    metadata = ProjectMetadata(mode=mode, code_repos=[CodeRepoMeta(name=name) for name in code_repos])
    write_metadata(project_dir, metadata)
    return Layout(project_dir=project_dir, metadata=metadata)


@pytest.fixture(autouse=True)
def _no_live_tmux_in_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the blind-send check independent of the tmux server of the machine running the tests."""
    monkeypatch.setattr("workflow.tmux_dispatch.active_tmux_repo", lambda _session: None)
