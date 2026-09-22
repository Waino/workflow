"""Tests for workflow.layout.Layout, parametrised over both project modes."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from workflow.exceptions import WorkflowError
from workflow.layout import Layout
from workflow.metadata import CodeRepoMeta, ProjectMetadata, ProjectMode
from workflow.names import DOCS_REPO, WorktreeName


def _in_band(project_dir: Path) -> Layout:
    return Layout(project_dir=project_dir, metadata=ProjectMetadata(mode=ProjectMode.in_band))


def _oob(project_dir: Path) -> Layout:
    metadata = ProjectMetadata(
        mode=ProjectMode.oob,
        code_repos=[CodeRepoMeta(name="mypackage"), CodeRepoMeta(name="dep1")],
    )
    return Layout(project_dir=project_dir, metadata=metadata)


class TestWorktreeRoot:
    def test_in_band(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).worktree_root(WorktreeName.builder) == tmp_path / "builder"

    def test_oob(self, tmp_path: Path) -> None:
        assert _oob(tmp_path).worktree_root(WorktreeName.builder) == tmp_path / "builder"


class TestGitRepoRoot:
    def test_in_band_defaults_to_worktree_root(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).git_repo_root(WorktreeName.canon) == tmp_path / "canon"

    def test_in_band_rejects_non_docs_repo(self, tmp_path: Path) -> None:
        with pytest.raises(WorkflowError, match="mypackage"):
            _in_band(tmp_path).git_repo_root(WorktreeName.canon, "mypackage")

    def test_oob_docs_repo(self, tmp_path: Path) -> None:
        assert _oob(tmp_path).git_repo_root(WorktreeName.canon, DOCS_REPO) == tmp_path / "canon" / "docs"

    def test_oob_code_repo(self, tmp_path: Path) -> None:
        assert _oob(tmp_path).git_repo_root(WorktreeName.canon, "mypackage") == tmp_path / "canon" / "mypackage"


class TestPlansRoot:
    def test_mode_independent(self, tmp_path: Path) -> None:
        expected = tmp_path / "builder" / "docs" / "plans"
        assert _in_band(tmp_path).plans_root(WorktreeName.builder) == expected
        assert _oob(tmp_path).plans_root(WorktreeName.builder) == expected


class TestVenvRoot:
    def test_mode_independent(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).venv_root(WorktreeName.canon) == tmp_path / "canon"
        assert _oob(tmp_path).venv_root(WorktreeName.canon) == tmp_path / "canon"


class TestHookRoot:
    def test_matches_git_repo_root(self, tmp_path: Path) -> None:
        layout = _oob(tmp_path)
        assert layout.hook_root(WorktreeName.canon, "mypackage") == layout.git_repo_root(
            WorktreeName.canon, "mypackage",
        )


class TestBareRepo:
    def test_in_band_defaults(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).bare_repo() == tmp_path / "bare"

    def test_in_band_rejects_non_docs_repo(self, tmp_path: Path) -> None:
        with pytest.raises(WorkflowError, match="mypackage"):
            _in_band(tmp_path).bare_repo("mypackage")

    def test_oob_per_repo(self, tmp_path: Path) -> None:
        layout = _oob(tmp_path)
        assert layout.bare_repo(DOCS_REPO) == tmp_path / "bare" / "docs"
        assert layout.bare_repo("mypackage") == tmp_path / "bare" / "mypackage"


class TestRepoInventory:
    def test_in_band_repos(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).repos() == [DOCS_REPO]

    def test_in_band_code_repos_empty(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).code_repos() == []

    def test_oob_repos(self, tmp_path: Path) -> None:
        assert _oob(tmp_path).repos() == [DOCS_REPO, "mypackage", "dep1"]

    def test_oob_code_repos_excludes_docs(self, tmp_path: Path) -> None:
        assert _oob(tmp_path).code_repos() == ["mypackage", "dep1"]


class TestPlansPrefix:
    def test_in_band(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).plans_prefix == PurePosixPath("docs", "plans")

    def test_oob(self, tmp_path: Path) -> None:
        assert _oob(tmp_path).plans_prefix == PurePosixPath("plans")

    def test_not_mode_independent(self, tmp_path: Path) -> None:
        assert _in_band(tmp_path).plans_prefix != _oob(tmp_path).plans_prefix
