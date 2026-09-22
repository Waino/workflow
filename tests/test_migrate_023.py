"""Tests for scripts/migrate_023.py.

Invoked via subprocess (the script lives outside the workflow package, is not
importable as a module, and is meant to be run as a standalone CLI tool).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from workflow.metadata import LEGACY_ROOT_MARKER, METADATA_FILENAME, read_metadata

_SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_023.py"


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
