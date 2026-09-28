"""Oob-mode tests for workflow.commands.doctor (023.6).

Builds a real oob project via `init --oob` (docker/tmux mocked or gracefully degraded,
as in test_init_oob.py) and drives `doctor` against real git repos.
"""

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


def _contains(text: str, phrase: str) -> bool:
    """Whitespace-agnostic substring check (rich wraps long lines at the console width)."""

    def strip(s: str) -> str:
        return "".join(s.split())

    return strip(phrase) in strip(text)


def _setup_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a real oob project (docs, mypackage, dep1) and return its config path."""
    _make_oob_project(tmp_path, ["mypackage", "dep1"])
    result = _run_init(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    return tmp_path / "config.yaml"


def test_doctor_exits_zero_on_a_clean_oob_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 0, result.output


def test_doctor_flags_an_extra_remote_in_an_agent_code_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "250")  # rich truncates instead of wrapping a narrow anomalies cell
    config_path = _setup_project(tmp_path, monkeypatch)
    builder_mypackage = tmp_path / "builder" / "mypackage"
    _git(["remote", "add", "origin", str(tmp_path / "canon" / "mypackage")], cwd=builder_mypackage)

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 1
    assert _contains(result.output, "expected exactly one remote")


def test_doctor_tolerates_canons_extra_origin_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Canon legitimately keeps origin + bare for every code repo (for `workflow sync`)."""
    config_path = _setup_project(tmp_path, monkeypatch)

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 0, result.output
    assert not _contains(result.output, "expected exactly one remote")


def test_doctor_flags_a_missing_code_repo_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    shutil.rmtree(tmp_path / "reviewer" / "dep1")

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 1
    assert _contains(result.output, "missing")


def test_doctor_ignores_branch_rules_in_code_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Code-repo branches aren't managed by the tool: feature branches and non-main canon are normal."""
    monkeypatch.setenv("COLUMNS", "250")
    config_path = _setup_project(tmp_path, monkeypatch)
    _git(["checkout", "-b", "feature/widget"], cwd=tmp_path / "builder" / "mypackage")
    _git(["checkout", "-b", "dev"], cwd=tmp_path / "canon" / "dep1")

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 0, result.output


def test_doctor_ignores_stale_local_main_in_code_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare's main advancing (e.g. after `workflow sync`) leaves agents' local code-repo main behind."""
    monkeypatch.setenv("COLUMNS", "250")
    config_path = _setup_project(tmp_path, monkeypatch)
    canon_mypackage = tmp_path / "canon" / "mypackage"
    (canon_mypackage / "upstream.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "."], cwd=canon_mypackage)
    _git(["commit", "-m", "upstream change"], cwd=canon_mypackage)
    _git(["push", "bare", "main"], cwd=canon_mypackage)

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 0, result.output


def test_doctor_still_flags_a_stray_docs_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "250")
    config_path = _setup_project(tmp_path, monkeypatch)
    _git(["checkout", "-b", "feature/widget"], cwd=tmp_path / "builder" / "docs")

    result = CliRunner().invoke(main, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 1
    assert _contains(result.output, "stray branch 'feature/widget'")
