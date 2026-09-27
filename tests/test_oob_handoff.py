"""Integration test for the oob multi-repo `check` handoff (023.5).

Builds a real oob project via `init --oob` (docker/tmux mocked or gracefully degraded,
as in test_init_oob.py), then drives `check` directly against real git repos to verify
the ahead-of-bare push set, per-repo hooks, the reviewer fetch-not-checkout rule, and the
dirty-tree preconditions.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner, Result

import workflow.commands.workflow as wc
from tests.test_init_oob import _git, _make_oob_project, _run_init
from workflow.cli import main
from workflow.names import DOCS_REPO
from workflow.validate import CheckResult

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


def _write_plan(tmp_path: Path) -> None:
    plan_dir = tmp_path / "builder" / "docs" / "plans" / "implementing"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "005_foo.md").write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")


def _commit_docs_and_dep1(tmp_path: Path) -> None:
    """Builder commits a plan (on feat/005_foo) in docs, and an unrelated change in dep1."""
    docs_repo = tmp_path / "builder" / DOCS_REPO
    _git(["checkout", "-b", "feat/005_foo"], cwd=docs_repo)
    _write_plan(tmp_path)
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)

    dep1_repo = tmp_path / "builder" / "dep1"
    _git(["checkout", "-b", "add-feature"], cwd=dep1_repo)
    (dep1_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(["add", "."], cwd=dep1_repo)
    _git(["commit", "-m", "dep1: add feature"], cwd=dep1_repo)


def _run_check(
    tmp_path: Path,
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Result:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        wc,
        "run_validate",
        MagicMock(return_value=[CheckResult(check_name="x", status="pass", message="ok")]),
    )
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    return CliRunner().invoke(main, ["--config", str(config_path), "check", "005"])


def test_check_pushes_exactly_the_committed_repos_and_fetches_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    _commit_docs_and_dep1(tmp_path)
    dep1_sha = _git(["rev-parse", "HEAD"], cwd=tmp_path / "builder" / "dep1").stdout.decode().strip()
    mypackage_sha_before = _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare" / "mypackage").stdout.decode()

    result = _run_check(tmp_path, config_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert _contains(result.output, "dep1")
    assert not _contains(result.output, "push mypackage")

    # docs: transitioned (an extra commit lands on the branch) + pushed via the existing
    # dedicated steps -- compare against builder's post-check feat/005_foo tip (builder itself
    # is parked back on 'main' by check, so HEAD no longer names it), not the pre-check commit.
    docs_sha_after = (
        _git(["rev-parse", "refs/heads/feat/005_foo"], cwd=tmp_path / "builder" / DOCS_REPO).stdout.decode().strip()
    )
    assert _git(
        ["rev-parse", "refs/heads/feat/005_foo"],
        cwd=tmp_path / "bare" / DOCS_REPO,
    ).stdout.decode().strip() == (docs_sha_after)
    # dep1: pushed via the new generic push-set step, under its own (agent-chosen) branch name.
    assert _git(["rev-parse", "refs/heads/add-feature"], cwd=tmp_path / "bare" / "dep1").stdout.decode().strip() == (
        dep1_sha
    )
    # mypackage: untouched -- no new branch, bare/main unchanged.
    assert (
        _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare" / "mypackage").stdout.decode()
        == mypackage_sha_before
    )

    # Reviewer: docs is checked out to the plan's branch; dep1 is fetched but left alone.
    assert _git(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tmp_path / "reviewer" / DOCS_REPO,
    ).stdout.decode().strip() == ("feat/005_foo")
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "reviewer" / "dep1").stdout.decode().strip() == (
        "main"
    )
    assert (
        _git(["rev-parse", "refs/remotes/bare/add-feature"], cwd=tmp_path / "reviewer" / "dep1").stdout.decode().strip()
        == dep1_sha
    )

    # builder itself is parked back on 'main' (unrelated to the plan) once the handoff lands;
    # reviewer is the worktree left checked out to the branch, so the moved file shows up there.
    plan_path = tmp_path / "reviewer" / "docs" / "plans" / "checking" / "005_foo.md"
    assert plan_path.exists()


def test_check_fails_on_tracked_modification_in_a_code_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    _commit_docs_and_dep1(tmp_path)
    (tmp_path / "builder" / "dep1" / "feature.txt").write_text("changed after commit\n", encoding="utf-8")

    result = _run_check(tmp_path, config_path, monkeypatch)

    assert result.exit_code != 0
    assert _contains(result.output, "uncommitted tracked changes")
    assert _contains(result.output, "dep1")
    # Refused before anything was pushed.
    assert not (tmp_path / "bare" / "dep1" / "refs" / "heads" / "add-feature").exists()


def test_check_warns_but_succeeds_on_untracked_file_in_a_code_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    _commit_docs_and_dep1(tmp_path)
    (tmp_path / "builder" / "mypackage" / "build_output.tmp").write_text("junk\n", encoding="utf-8")

    result = _run_check(tmp_path, config_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert _contains(result.output, "untracked files")
