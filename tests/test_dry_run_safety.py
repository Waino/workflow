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

CHECKING, IMPLEMENTING, PLANNING, INTENT = "004_checking", "005_implementing", "006_planning", "007_intent"

# Accepted coverage gap: these refuse in this in-band sandbox; their own dry-run tests live in
# test_init_oob.py (add-repo) and test_sync.py, so an oob sandbox here isn't worth its setup cost.
OOB_ONLY_SCRIPTS = {"add-repo", "sync"}

# One argv per registered script; a new script without an entry fails test_every_script_has_an_argv.
SCRIPT_ARGV: dict[str, list[str]] = {
    "accept": ["accept", CHECKING],
    "agents_md": ["agents_md"],
    "canon_pre_commit": ["canon_pre_commit"],
    "cheat": ["cheat"],
    "check": ["check", IMPLEMENTING],
    "doctor": ["doctor"],
    "go": ["go", "planner"],
    "implement": ["implement", PLANNING, "--onto", IMPLEMENTING],
    "init": ["init", "--continue"],
    "log": ["log"],
    "new": ["new", "fresh_idea"],
    "push": ["push", INTENT],
    "reject": ["reject", CHECKING],
    "start": ["start"],
    "status": ["status"],
    "todo": ["todo", PLANNING],
    "validate": ["validate", CHECKING],
}
PROMPT_ARGV = {"ask": ["ask", PLANNING]}
# Exit 1 here is a finding, not a refusal: doctor reports canon's unpushed intent commit (push's work).
REPORTS_FINDINGS = {"doctor"}


def _file_hashes(project_dir: Path) -> dict[str, str]:
    """Hash every file except `.git` directories: read-only `git status` may refresh the index stat cache."""
    return {
        str(path.relative_to(project_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(project_dir).parts
    }


def _git_states(project_dir: Path) -> dict[str, str]:
    """All non-remote refs and HEAD of every repo: worktrees (a `.git` entry) and bare repos (`HEAD` + `refs`).

    Remote-tracking refs are left out: dry-run previews deliberately fetch from bare (e.g. `accept`),
    which only refreshes that cache.
    """
    repos = {path.parent for path in project_dir.rglob(".git")}
    repos |= {path.parent for path in project_dir.rglob("HEAD") if (path.parent / "refs").is_dir()}
    states: dict[str, str] = {}
    for repo in sorted(repos):
        if ".git" in repo.relative_to(project_dir).parts:
            continue
        refs = [
            line
            for line in run_git(["for-each-ref"], cwd=repo).stdout.decode().splitlines()
            if "\trefs/remotes/" not in line
        ]
        head = run_git(["symbolic-ref", "-q", "HEAD"], cwd=repo, check=False).stdout.decode()
        commit = run_git(["rev-parse", "HEAD"], cwd=repo, check=False).stdout.decode()
        states[str(repo.relative_to(project_dir))] = "\n".join([*refs, head, commit])
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
        make_plan_file(project / "canon", "intent", f"{INTENT}.md")
        run_git(["add", "."], cwd=project / "canon")
        run_git(["commit", "-m", "add intent plan"], cwd=project / "canon")
        advance_clock()

        (project / ".agents_md_hash").unlink()  # stale, so agents_md has something it would write

        monkeypatch.setenv("EDITOR", "true")
        for module in (tmux_dispatch, plan_resolver):
            monkeypatch.setattr(module, "active_tmux_repo", lambda _session: None)
        monkeypatch.setattr(wc, "workflow_session_exists", lambda _config: False)
        monkeypatch.setattr(wc, "is_inside_workflow_session", lambda _config: False)
        yield project, config


def test_every_script_has_an_argv() -> None:
    assert set(SCRIPT_ARGV) | OOB_ONLY_SCRIPTS == set(_SCRIPT_REGISTRY), "add an argv for every new script command"


@pytest.mark.parametrize("argv", [*SCRIPT_ARGV.values(), *PROMPT_ARGV.values()], ids=lambda argv: argv[0])
def test_dry_run_changes_nothing(sandbox: tuple[Path, Path], argv: list[str]) -> None:
    project, config = sandbox
    before = _snapshot(project)

    result = CliRunner().invoke(main, ["--config", str(config), "--dry-run", *argv])

    # A refusal would be safe but vacuous: it never reaches the code that could mutate.
    assert result.exit_code == (1 if argv[0] in REPORTS_FINDINGS else 0), result.output
    assert _snapshot(project) == before
