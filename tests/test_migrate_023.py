"""Tests for scripts/migrate_023.py.

Invoked via subprocess (the script lives outside the workflow package, is not
importable as a module, and is meant to be run as a standalone CLI tool).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from workflow.metadata import LEGACY_ROOT_MARKER, METADATA_FILENAME, read_metadata

_SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_023.py"
_GIT = shutil.which("git") or "git"


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([_GIT, *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "initial commit"], cwd=path)
    return path


def _make_project_with_legacy_remotes(project_dir: Path) -> None:
    """Build an in-band project whose worktrees have the pre-023.3 'origin' remote shape."""
    shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", project_dir / "config.yaml")
    canon = _init_repo(project_dir / "canon")
    bare = project_dir / "bare"
    _git(["clone", "--bare", str(canon), str(bare)], cwd=project_dir)
    _git(["remote", "add", "origin", str(bare)], cwd=canon)
    _git(["push", "-u", "origin", "main"], cwd=canon)
    for name in ("planner", "builder", "reviewer"):
        _git(["clone", str(bare), str(project_dir / name)], cwd=project_dir)


def _run(project_dir: Path, *, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(_SCRIPT), "--project-dir", str(project_dir)]
    if dry_run:
        args.append("--dry-run")
    return subprocess.run(args, capture_output=True, text=True, check=True)  # noqa: S603


def test_migrates_legacy_marker_to_metadata_file(tmp_path: Path) -> None:
    (tmp_path / LEGACY_ROOT_MARKER).touch()

    _run(tmp_path)

    assert (tmp_path / METADATA_FILENAME).exists()
    assert not (tmp_path / LEGACY_ROOT_MARKER).exists()
    assert read_metadata(tmp_path) == read_metadata(tmp_path)  # parses cleanly


def test_writes_default_metadata_when_no_legacy_marker(tmp_path: Path) -> None:
    _run(tmp_path)

    assert (tmp_path / METADATA_FILENAME).exists()


def test_rerun_is_a_noop(tmp_path: Path) -> None:
    (tmp_path / LEGACY_ROOT_MARKER).touch()
    _run(tmp_path)
    metadata_before = (tmp_path / METADATA_FILENAME).read_text()

    _run(tmp_path)

    assert (tmp_path / METADATA_FILENAME).read_text() == metadata_before
    assert not (tmp_path / LEGACY_ROOT_MARKER).exists()


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    (tmp_path / LEGACY_ROOT_MARKER).touch()

    result = _run(tmp_path, dry_run=True)

    assert not (tmp_path / METADATA_FILENAME).exists()
    assert (tmp_path / LEGACY_ROOT_MARKER).exists()
    assert "writing default" in result.stdout
    assert "removing legacy" in result.stdout


def test_migrates_repo_override_key_in_config_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "states:\n  todo:\n    commands:\n      - repo_override: canon\n      - repo_override: builder\n",
        encoding="utf-8",
    )

    result = _run(tmp_path)

    new_text = config_path.read_text(encoding="utf-8")
    assert "repo_override" not in new_text
    assert new_text.count("worktree_override: canon") == 1
    assert new_text.count("worktree_override: builder") == 1
    assert "rewriting 2 occurrence(s)" in result.stdout


def test_config_yaml_without_repo_override_is_untouched(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    original = "states:\n  todo:\n    commands: []\n"
    config_path.write_text(original, encoding="utf-8")

    _run(tmp_path)

    assert config_path.read_text(encoding="utf-8") == original


def test_missing_config_yaml_is_skipped(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert not (tmp_path / "config.yaml").exists()
    assert "not present; skipping" in result.stdout


def test_config_yaml_migration_dry_run_does_not_mutate(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    original = "states:\n  todo:\n    commands:\n      - repo_override: canon\n"
    config_path.write_text(original, encoding="utf-8")

    _run(tmp_path, dry_run=True)

    assert config_path.read_text(encoding="utf-8") == original


_needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


@_needs_git
def test_bare_remote_rename_renames_origin_to_bare_everywhere(tmp_path: Path) -> None:
    _make_project_with_legacy_remotes(tmp_path)

    result = _run(tmp_path)

    for name in ("canon", "planner", "builder", "reviewer"):
        repo = tmp_path / name
        assert _git(["remote", "get-url", "bare"], cwd=repo).stdout.decode().strip() == str(tmp_path / "bare")
        get_url = subprocess.run(  # noqa: S603
            [_GIT, "remote", "get-url", "origin"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        assert get_url.returncode != 0
    assert "renaming 'origin' to 'bare'" in result.stdout


@_needs_git
def test_bare_remote_rename_repoints_a_feature_branch_upstream(tmp_path: Path) -> None:
    _make_project_with_legacy_remotes(tmp_path)
    builder = tmp_path / "builder"
    _git(["checkout", "-b", "feat/005_foo"], cwd=builder)
    (builder / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(["add", "."], cwd=builder)
    _git(["commit", "-m", "feature work"], cwd=builder)
    _git(["push", "-u", "origin", "feat/005_foo"], cwd=builder)

    _run(tmp_path)

    upstream = (
        _git(
            ["rev-parse", "--abbrev-ref", "feat/005_foo@{u}"],
            cwd=builder,
        )
        .stdout.decode()
        .strip()
    )
    assert upstream == "bare/feat/005_foo"


@_needs_git
def test_bare_remote_rename_rerun_is_a_noop(tmp_path: Path) -> None:
    _make_project_with_legacy_remotes(tmp_path)
    _run(tmp_path)

    result = _run(tmp_path)

    assert "already has a 'bare' remote; skipping" in result.stdout


@_needs_git
def test_bare_remote_rename_preserves_an_unrelated_origin(tmp_path: Path) -> None:
    _make_project_with_legacy_remotes(tmp_path)
    canon = tmp_path / "canon"
    upstream = tmp_path / "upstream.git"
    _git(["clone", "--bare", str(canon), str(upstream)], cwd=tmp_path)
    _git(["remote", "remove", "origin"], cwd=canon)
    _git(["remote", "add", "origin", str(upstream)], cwd=canon)

    result = _run(tmp_path)

    assert _git(["remote", "get-url", "origin"], cwd=canon).stdout.decode().strip() == str(upstream)
    assert "not the project's bare path; leaving as-is" in result.stdout


@_needs_git
def test_bare_remote_rename_dry_run_does_not_mutate(tmp_path: Path) -> None:
    _make_project_with_legacy_remotes(tmp_path)
    canon = tmp_path / "canon"

    result = _run(tmp_path, dry_run=True)

    assert _git(["remote", "get-url", "origin"], cwd=canon).stdout.decode().strip() == str(tmp_path / "bare")
    get_url = subprocess.run(  # noqa: S603
        [_GIT, "remote", "get-url", "bare"],
        cwd=canon,
        capture_output=True,
        check=False,
    )
    assert get_url.returncode != 0
    assert "would run: git" in result.stdout
