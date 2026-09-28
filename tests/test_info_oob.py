"""Oob-mode tests for the `status` git-status matrix (023.6)."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from tests.test_init_oob import _git, _make_oob_project, _run_init
from workflow.cli import main

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def test_status_prints_a_repo_by_worktree_matrix_in_oob_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "250")
    _make_oob_project(tmp_path, ["mypackage", "dep1"])
    init_result = _run_init(tmp_path, monkeypatch)
    assert init_result.exit_code == 0, init_result.output

    result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "status"])

    assert result.exit_code == 0, result.output
    assert "Git Status" in result.output
    for worktree in ("canon", "planner", "builder", "reviewer"):
        assert worktree in result.output
    for repo in ("docs", "mypackage", "dep1"):
        assert repo in result.output
    assert "Git status for worktree" not in result.output


def test_status_matrix_cells_show_counts_dirty_marker_and_missing_clones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "250")
    _make_oob_project(tmp_path, ["mypackage", "dep1"])
    assert _run_init(tmp_path, monkeypatch).exit_code == 0
    builder_mypackage = tmp_path / "builder" / "mypackage"
    _git(["checkout", "-b", "feat/w"], cwd=builder_mypackage)
    (builder_mypackage / "w.txt").write_text("w\n", encoding="utf-8")
    _git(["add", "."], cwd=builder_mypackage)
    _git(["commit", "-m", "w"], cwd=builder_mypackage)
    (builder_mypackage / "README.md").write_text("dirty\n", encoding="utf-8")
    shutil.rmtree(tmp_path / "reviewer" / "dep1")

    result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "status"])

    assert result.exit_code == 0, result.output
    assert "feat/w +1/-- *" in result.output  # no bare/feat/w yet: behind is unknown
    assert "main +0/-0" in result.output
    assert "missing" in result.output
