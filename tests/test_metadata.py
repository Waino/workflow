"""Tests for workflow.metadata."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from workflow.exceptions import WorkflowError
from workflow.metadata import (
    METADATA_FILENAME,
    CodeRepoMeta,
    ProjectMetadata,
    ProjectMode,
    read_metadata,
    write_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestReadWriteRoundTrip:
    def test_round_trip_defaults(self, tmp_path: Path) -> None:
        write_metadata(tmp_path, ProjectMetadata())
        loaded = read_metadata(tmp_path)
        assert loaded == ProjectMetadata()

    def test_round_trip_with_code_repos(self, tmp_path: Path) -> None:
        metadata = ProjectMetadata(
            mode=ProjectMode.oob,
            code_repos=[CodeRepoMeta(name="myrepo", run_hook=False)],
        )
        write_metadata(tmp_path, metadata)
        loaded = read_metadata(tmp_path)
        assert loaded == metadata

    def test_written_file_is_pretty_printed_json(self, tmp_path: Path) -> None:
        write_metadata(tmp_path, ProjectMetadata())
        content = (tmp_path / METADATA_FILENAME).read_text()
        assert json.loads(content) == {"version": 1, "mode": "in_band", "code_repos": []}
        assert "\n" in content  # indented, not a single line


class TestReadMetadataErrors:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(WorkflowError, match=str(tmp_path / METADATA_FILENAME)):
            read_metadata(tmp_path)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / METADATA_FILENAME).write_text("{not json")
        with pytest.raises(WorkflowError, match=str(tmp_path / METADATA_FILENAME)):
            read_metadata(tmp_path)

    def test_unknown_version_raises(self, tmp_path: Path) -> None:
        (tmp_path / METADATA_FILENAME).write_text(json.dumps({"version": 2}))
        with pytest.raises(WorkflowError, match="unknown or missing version"):
            read_metadata(tmp_path)

    def test_missing_version_raises(self, tmp_path: Path) -> None:
        (tmp_path / METADATA_FILENAME).write_text(json.dumps({"mode": "in_band"}))
        with pytest.raises(WorkflowError, match="unknown or missing version"):
            read_metadata(tmp_path)

    def test_invalid_shape_raises(self, tmp_path: Path) -> None:
        (tmp_path / METADATA_FILENAME).write_text(json.dumps({"version": 1, "mode": "not_a_real_mode"}))
        with pytest.raises(WorkflowError, match="malformed"):
            read_metadata(tmp_path)


class TestCodeReposMatchesMode:
    def test_oob_without_code_repos_raises(self) -> None:
        with pytest.raises(ValueError, match="oob projects must have at least one code repo"):
            ProjectMetadata(mode=ProjectMode.oob)

    def test_in_band_with_code_repos_raises(self) -> None:
        with pytest.raises(ValueError, match="in-band projects must not have code_repos"):
            ProjectMetadata(mode=ProjectMode.in_band, code_repos=[CodeRepoMeta(name="myrepo")])

    def test_oob_with_code_repos_is_valid(self) -> None:
        ProjectMetadata(mode=ProjectMode.oob, code_repos=[CodeRepoMeta(name="myrepo")])

    def test_in_band_without_code_repos_is_valid(self) -> None:
        ProjectMetadata(mode=ProjectMode.in_band)


class TestWriteMetadataAtomicity:
    def test_no_tmp_residue_on_success(self, tmp_path: Path) -> None:
        write_metadata(tmp_path, ProjectMetadata())
        assert list(tmp_path.iterdir()) == [tmp_path / METADATA_FILENAME]

    def test_preserves_old_content_and_no_residue_when_serialization_raises(self, tmp_path: Path) -> None:
        write_metadata(tmp_path, ProjectMetadata())
        original_content = (tmp_path / METADATA_FILENAME).read_text()

        with (
            patch("workflow.metadata.json.dump", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            write_metadata(
                tmp_path,
                ProjectMetadata(mode=ProjectMode.oob, code_repos=[CodeRepoMeta(name="myrepo")]),
            )

        assert (tmp_path / METADATA_FILENAME).read_text() == original_content
        assert list(tmp_path.iterdir()) == [tmp_path / METADATA_FILENAME]
