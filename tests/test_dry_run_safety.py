"""`--dry-run` must never change anything: files, refs, or HEAD of any repo in the project."""

from __future__ import annotations

import hashlib
import shutil
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

import workflow.cli
import workflow.commands.workflow as wc
from tests.conftest import (
    advance_clock,
    builder_commit,
    create_and_todo_plan,
    make_plan_file,
    run_git,
    run_workflow,
    setup_stacked_project,
)
from workflow import plan_resolver, tmux_dispatch
from workflow.cli import main
from workflow.registry import _SCRIPT_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

CHECKING, IMPLEMENTING, PLANNING = "004_checking", "005_implementing", "006_planning"

# One argv per registered script; a new script without an entry fails test_every_script_has_an_argv.
SCRIPT_ARGV: dict[str, list[str]] = {
    "accept": ["accept", CHECKING],
    "add-repo": ["add-repo", "extra"],
    "agents_md": ["agents_md"],
    "canon_pre_commit": ["canon_pre_commit"],
    "cheat": ["cheat"],
    "check": ["check", IMPLEMENTING],
    "doctor": ["doctor"],
    "go": ["go", "planner"],
    "implement": ["implement", PLANNING],
    "init": ["init"],
    "log": ["log"],
    "new": ["new", "fresh_idea"],
    "push": ["push", PLANNING],
    "reject": ["reject", CHECKING],
    "start": ["start"],
    "status": ["status"],
    "sync": ["sync"],
    "todo": ["todo", PLANNING],
    "validate": ["validate", CHECKING],
}
PROMPT_ARGV = {"ask": ["ask", PLANNING]}


def _file_hashes(project_dir: Path) -> dict[str, str]:
    """Hash every file except `.git` directories: read-only `git status` may refresh the index stat cache."""
    return {
        str(path.relative_to(project_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(project_dir).parts
    }


def _git_states(project_dir: Path) -> dict[str, str]:
    """Local branches, tags and HEAD of every repo: worktrees (a `.git` entry) and bare repos (`HEAD` + `refs`).

    Remote-tracking refs are left out: dry-run previews deliberately fetch from bare (e.g. `accept`),
    which only refreshes that cache.
    """
    repos = {path.parent for path in project_dir.rglob(".git")}
    repos |= {path.parent for path in project_dir.rglob("HEAD") if (path.parent / "refs").is_dir()}
    states: dict[str, str] = {}
    for repo in sorted(repos):
        if ".git" in repo.relative_to(project_dir).parts:
            continue
        refs = run_git(["for-each-ref", "refs/heads", "refs/tags"], cwd=repo).stdout
        head = run_git(["symbolic-ref", "-q", "HEAD"], cwd=repo, check=False).stdout
        states[str(repo.relative_to(project_dir))] = (refs + head).decode()
    return states


def _snapshot(project_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    return _file_hashes(project_dir), _git_states(project_dir)


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    """A project with one plan each in checking, implementing and planning; yields (project_dir, config)."""
    project = tmp_path_factory.mktemp("dry_run_safety")
    with pytest.MonkeyPatch.context() as monkeypatch:
        config = setup_stacked_project(project, monkeypatch)
        create_and_todo_plan(config, project, CHECKING)
        assert run_workflow(config, "implement", CHECKING).exit_code == 0
        builder_commit(project, "checking.txt", "work on the checking plan")
        assert run_workflow(config, "check", CHECKING).exit_code == 0
        create_and_todo_plan(config, project, IMPLEMENTING)
        assert run_workflow(config, "implement", IMPLEMENTING).exit_code == 0
        planner = project / "planner"
        run_git(["checkout", "main"], cwd=planner)
        run_git(["checkout", "-b", f"plan/{PLANNING}"], cwd=planner)
        make_plan_file(planner, "planning", f"{PLANNING}.md")
        run_git(["add", "."], cwd=planner)
        run_git(["commit", "-m", "add planning plan"], cwd=planner)
        advance_clock()

        (project / ".agents_md_hash").unlink()  # stale, so agents_md has something it would write

        monkeypatch.setenv("EDITOR", "true")
        for module in (tmux_dispatch, plan_resolver):
            monkeypatch.setattr(module, "active_tmux_repo", lambda _session: None)
        monkeypatch.setattr(wc, "workflow_session_exists", lambda _config: False)
        monkeypatch.setattr(wc, "is_inside_workflow_session", lambda _config: False)
        yield project, config


def test_every_script_has_an_argv() -> None:
    assert set(SCRIPT_ARGV) == set(_SCRIPT_REGISTRY), "add an argv for every new script command"


@pytest.mark.parametrize("argv", [*SCRIPT_ARGV.values(), *PROMPT_ARGV.values()], ids=lambda argv: argv[0])
def test_dry_run_changes_nothing(sandbox: tuple[Path, Path], argv: list[str]) -> None:
    project, config = sandbox
    before = _snapshot(project)

    # The exit code is not asserted: refusing in this state is safe, mutating is not.
    CliRunner().invoke(main, ["--config", str(config), "--dry-run", *argv])

    assert _snapshot(project) == before
