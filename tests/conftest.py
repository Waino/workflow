"""Shared test fixtures/helpers."""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

import workflow.commands.workflow as wc
from workflow.cli import main
from workflow.git_helpers import CommitResult
from workflow.layout import Layout
from workflow.metadata import CodeRepoMeta, ProjectMetadata, ProjectMode, write_metadata
from workflow.validate import CheckResult

if TYPE_CHECKING:
    from collections.abc import Sequence


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


# ---------------------------------------------------------------------------
# Real-git sandbox helpers (shared by the integration and dry-run safety tests)
# ---------------------------------------------------------------------------

GIT = shutil.which("git") or "git"


GIT_CLOCK = itertools.count(int(time.time()), 60)
"""Commit timestamps handed to git, one minute apart, starting around now."""


def advance_clock() -> None:
    """Move the commit clock forward for every git process, ours and the workflow's.

    The multi-source plan resolver breaks same-second ties within a single repo's own
    history by git log traversal order, not recency; stacked-plan tests commit several
    times per second. Stamping the date via the environment keeps resolution
    deterministic without sleeping.
    """
    stamp = f"{next(GIT_CLOCK)} +0000"
    os.environ["GIT_AUTHOR_DATE"] = stamp
    os.environ["GIT_COMMITTER_DATE"] = stamp


def run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([GIT, *args], cwd=cwd, check=check, capture_output=True, **kwargs)  # noqa: S603


def git_commit_without_noverify(
    repo_path: Path,
    msg: str,
    *,
    dry_run: bool = False,
    no_verify: bool = False,  # noqa: ARG001 — preserves git_commit signature
) -> None:
    """Real-git stand-in for git_commit: the sandbox git wrapper rejects --no-verify."""
    if not dry_run:
        run_git(["commit", "-m", msg], cwd=repo_path)


def git_commit_if_changed_without_noverify(
    repo_path: Path,
    msg: str,
    *,
    dry_run: bool = False,
    no_verify: bool = False,  # noqa: ARG001 — preserves signature
) -> CommitResult:
    """Real-git stand-in for git_commit_if_changed: the sandbox git wrapper rejects --no-verify."""
    if dry_run:
        return CommitResult.COMMITTED
    proc = subprocess.run(  # noqa: S603
        [GIT, "commit", "-m", msg],
        cwd=repo_path,
        check=False,
        capture_output=True,
    )
    output = proc.stdout + proc.stderr
    if proc.returncode != 0 and (b"nothing to commit" in output or b"nothing added to commit" in output):
        return CommitResult.NOTHING_TO_COMMIT
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
    return CommitResult.COMMITTED


def init_canon_repo(tmp_path: Path) -> Path:
    """Create canon/ as a real git repo with one initial commit on 'main', return its path."""
    canon = tmp_path / "canon"
    canon.mkdir()
    run_git(["init", str(canon)])
    run_git(["config", "user.email", "test@example.com"], cwd=canon)
    run_git(["config", "user.name", "Test"], cwd=canon)
    run_git(["checkout", "-b", "main"], cwd=canon, check=False)
    (canon / "README.md").write_text("initial\n", encoding="utf-8")
    run_git(["add", "."], cwd=canon)
    run_git(["commit", "-m", "initial commit"], cwd=canon)
    return canon


def make_integration_config(tmp_path: Path) -> Path:
    """Write a minimal workflow config derived from the default, return its path."""
    with (tmp_path / "run_agent_in_docker.sh").open("w") as dummy_script:
        dummy_script.write("#!/bin/bash")
    (tmp_path / "run_agent_in_docker.sh").chmod(0o755)
    src = Path(__file__).parent.parent / "docs" / "default_config.yaml"
    dest = tmp_path / "config.yaml"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    create_project_marker(tmp_path)
    (tmp_path / "canon" / ".git-pre-commit-hook").touch()
    (tmp_path / "env.list").touch()
    return dest


def make_plan_file(repo_path: Path, state: str, filename: str) -> None:
    plan_dir = repo_path / "docs" / "plans" / state
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / filename).write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")


def setup_stacked_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Init a project and set git identity in every agent clone; return the config path."""
    init_canon_repo(tmp_path)
    config_path = make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "_run_pre_commit_hook", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    monkeypatch.setattr(wc, "git_commit_if_changed", git_commit_if_changed_without_noverify)
    for var in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(var, "")  # registers the var for teardown; advance_clock owns the value
    advance_clock()

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output
    for repo_name in ("planner", "builder", "reviewer"):
        repo = tmp_path / repo_name
        run_git(["config", "user.email", "test@example.com"], cwd=repo)
        run_git(["config", "user.name", "Test"], cwd=repo)
    return config_path


def create_and_todo_plan(config_path: Path, tmp_path: Path, plan_name: str) -> None:
    """Planner-create a plan on its own `plan/*` branch, cut from current main, and `todo` it."""
    planner = tmp_path / "planner"
    run_git(["checkout", "main"], cwd=planner)
    run_git(["pull"], cwd=planner, check=False)
    run_git(["checkout", "-b", f"plan/{plan_name}"], cwd=planner)
    make_plan_file(planner, "planning", f"{plan_name}.md")
    run_git(["add", "."], cwd=planner)
    run_git(["commit", "-m", f"add planning plan {plan_name}"], cwd=planner)
    # Same-second tie between this commit and `todo`'s own transition commit would otherwise
    # be broken by git log traversal order, not recency, once a third branch is in play (see
    # advance_clock's docstring).
    advance_clock()

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", plan_name])
    assert result.exit_code == 0, result.output
    advance_clock()


def builder_commit(tmp_path: Path, filename: str, message: str) -> None:
    builder = tmp_path / "builder"
    (builder / filename).write_text(f"{message}\n", encoding="utf-8")
    run_git(["add", "."], cwd=builder)
    run_git(["commit", "-m", message], cwd=builder)
    advance_clock()


def run_workflow(config_path: Path, *args: str) -> Any:
    """Invoke a workflow subcommand, then move the commit clock on for the next one."""
    result = CliRunner().invoke(main, ["--config", str(config_path), *args])
    advance_clock()
    return result
