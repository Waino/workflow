"""Tests for workflow.shell."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from workflow.shell import run_command

if TYPE_CHECKING:
    from pathlib import Path


def test_dry_run_without_cwd_prints_plain_would_run(capsys: pytest.CaptureFixture[str]) -> None:
    run_command(["git", "status"], dry_run=True)

    assert capsys.readouterr().out.strip() == "would run: git status"


@pytest.mark.parametrize(("sub", "shown"), [("planner", "planner"), ("", ".")])
def test_dry_run_shows_cwd_relative_to_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sub: str,
    shown: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    run_command(["git", "status"], dry_run=True, cwd=tmp_path / sub)

    assert capsys.readouterr().out.strip() == f"would run in {shown}: git status"


def test_dry_run_shows_absolute_cwd_outside_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    here = tmp_path / "here"
    there = tmp_path / "there"
    here.mkdir()
    monkeypatch.chdir(here)

    run_command(["git", "status"], dry_run=True, cwd=there)

    assert "".join(capsys.readouterr().out.split()) == f"wouldrunin{there}:gitstatus"
