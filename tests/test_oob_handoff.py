"""Integration tests for the oob multi-repo `check`/`accept` handoffs and prompt dispatch (023.5).

Builds a real oob project via `init --oob` (docker/tmux mocked or gracefully degraded,
as in test_init_oob.py), then drives the commands against real git repos to verify the
ahead-of-bare push set, per-repo hooks, the reviewer fetch-not-checkout rule, the push-set
preconditions, and code-repo fetches on dispatch. The docker script is a stub that logs its
arguments, so hook invocations can be asserted.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner, Result

import workflow.commands.workflow as wc
from tests.test_init_oob import _git, _make_oob_project, _run_init
from workflow import tmux_dispatch
from workflow.cli import main
from workflow.frontmatter import PlanFrontmatter, RepoBranch, strip_frontmatter, write_frontmatter
from workflow.metadata import read_metadata
from workflow.names import DOCS_REPO

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _contains(text: str, phrase: str) -> bool:
    """Whitespace-agnostic substring check (rich wraps long lines at the console width)."""

    def strip(s: str) -> str:
        return "".join(s.split())

    return strip(phrase) in strip(text)


def _setup_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, skip_dep1_hook: bool = False) -> Path:
    """Build a real oob project (docs, mypackage, dep1) and return its config path."""
    # --skip-hook only accepts a repo that ships a hook, so dep1 gets one in that case.
    _make_oob_project(tmp_path, ["mypackage", "dep1"], hook_repos=("dep1",) if skip_dep1_hook else ())
    result = _run_init(tmp_path, monkeypatch, *(["--skip-hook", "dep1"] if skip_dep1_hook else []))
    assert result.exit_code == 0, result.output
    script = tmp_path / "run_agent_in_docker.sh"
    script.write_text(f'#!/bin/bash\necho "$@" >> {tmp_path / "docker_calls.log"}\n', encoding="utf-8")
    return tmp_path / "config.yaml"


def _hook_calls(tmp_path: Path) -> list[str]:
    """Return the logged ``hook`` invocations of the docker script stub, as ``worktree [--repo R]``."""
    log = tmp_path / "docker_calls.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    calls = []
    for line in lines:
        args = line.split()
        if args[0] == "hook":
            calls.append(" ".join([args[2], *args[5:]]))
    return calls


def _write_plan(tmp_path: Path) -> None:
    plan_dir = tmp_path / "builder" / "docs" / "plans" / "implementing"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "005_foo.md"
    plan_path.write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")
    branches = {"mypackage": None, "dep1": RepoBranch(branch="add-feature", base="main")}
    write_frontmatter(plan_path, PlanFrontmatter(branches=branches))


def _commit(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", message], cwd=repo)
    return _git(["rev-parse", "HEAD"], cwd=repo).stdout.decode().strip()


def _bare_sha(tmp_path: Path, repo: str, branch: str) -> str | None:
    result = _git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=tmp_path / "bare" / repo,
        check=False,
    )
    return result.stdout.decode().strip() or None


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
    return _run(tmp_path, config_path, monkeypatch, "check", "005")


def _run(tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> Result:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    return CliRunner().invoke(main, ["--config", str(config_path), *args])


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
    assert _contains(result.output, "push dep1")
    assert not _contains(result.output, "push mypackage")
    # Real validate ran (not mocked) and passed on a default project.
    assert _contains(result.output, "branches-frontmatter")
    # One hook run per repo in the push set, each told which repo to run in.
    assert _hook_calls(tmp_path) == ["builder --repo docs", "builder --repo dep1"]

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


def test_check_skips_the_hook_for_a_repo_with_run_hook_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch, skip_dep1_hook=True)
    _commit_docs_and_dep1(tmp_path)

    result = _run_check(tmp_path, config_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert _hook_calls(tmp_path) == ["builder --repo docs"]
    assert _contains(result.output, "skipping pre-commit hook for dep1")


def test_check_refuses_a_detached_head_in_a_code_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    _commit_docs_and_dep1(tmp_path)
    _git(["checkout", "--detach"], cwd=tmp_path / "builder" / "dep1")

    result = _run_check(tmp_path, config_path, monkeypatch)

    assert result.exit_code != 0
    assert _contains(result.output, "detached HEAD")
    # Refused before the docs handoff landed.
    assert _bare_sha(tmp_path, DOCS_REPO, "feat/005_foo") is None
    assert (tmp_path / "builder" / "docs" / "plans" / "implementing" / "005_foo.md").exists()


def test_check_refuses_commits_on_a_code_repos_default_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    _commit_docs_and_dep1(tmp_path)
    _commit(tmp_path / "builder" / "mypackage", "direct.txt", "x\n", "commit on main")

    result = _run_check(tmp_path, config_path, monkeypatch)

    assert result.exit_code != 0
    assert _contains(result.output, "has commits on 'main'")
    assert _bare_sha(tmp_path, DOCS_REPO, "feat/005_foo") is None


def _check_then_reviewer_commits_to_dep1(tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run `check`, then have the reviewer add a commit on dep1's add-feature branch; return reviewer/dep1."""
    _commit_docs_and_dep1(tmp_path)
    assert _run_check(tmp_path, config_path, monkeypatch).exit_code == 0
    reviewer_dep1 = tmp_path / "reviewer" / "dep1"
    _git(["checkout", "-b", "add-feature", "bare/add-feature"], cwd=reviewer_dep1)
    _commit(reviewer_dep1, "review_fix.txt", "fix\n", "dep1: review fix")
    (tmp_path / "docker_calls.log").unlink()
    return reviewer_dep1


def test_accept_pushes_reviewer_code_commits_and_runs_their_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    reviewer_dep1 = _check_then_reviewer_commits_to_dep1(tmp_path, config_path, monkeypatch)
    fix_sha = _git(["rev-parse", "HEAD"], cwd=reviewer_dep1).stdout.decode().strip()

    result = _run(tmp_path, config_path, monkeypatch, "accept", "005")

    assert result.exit_code == 0, result.output
    assert _contains(result.output, "push dep1")
    assert _bare_sha(tmp_path, "dep1", "add-feature") == fix_sha
    assert _hook_calls(tmp_path) == ["reviewer --repo dep1"]
    assert (tmp_path / "canon" / "docs" / "plans" / "done" / "005_foo.md").exists()
    # The post-accept sync fetches every worktree's code repos.
    builder_dep1 = tmp_path / "builder" / "dep1"
    assert _git(["rev-parse", "refs/remotes/bare/add-feature"], cwd=builder_dep1).stdout.decode().strip() == fix_sha


def test_accept_needs_force_push_for_a_rewritten_code_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    reviewer_dep1 = _check_then_reviewer_commits_to_dep1(tmp_path, config_path, monkeypatch)
    _git(["reset", "--soft", "HEAD~2"], cwd=reviewer_dep1)
    _git(["commit", "-m", "dep1: squashed"], cwd=reviewer_dep1)
    squashed_sha = _git(["rev-parse", "HEAD"], cwd=reviewer_dep1).stdout.decode().strip()

    refused = _run(tmp_path, config_path, monkeypatch, "accept", "005")

    assert refused.exit_code != 0
    assert _contains(refused.output, "diverged from bare")
    assert not (tmp_path / "canon" / "docs" / "plans" / "done" / "005_foo.md").exists()

    result = _run(tmp_path, config_path, monkeypatch, "accept", "--force-push", "005")

    assert result.exit_code == 0, result.output
    assert _bare_sha(tmp_path, "dep1", "add-feature") == squashed_sha


def test_prompt_dispatch_fetches_code_repos_and_only_warns_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    plan_dir = tmp_path / "planner" / "docs" / "plans" / "planning"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "005_foo.md").write_text("# 005: Foo\n", encoding="utf-8")
    canon_dep1 = tmp_path / "canon" / "dep1"
    _git(["push", "bare", "main:refs/heads/upstream-news"], cwd=canon_dep1)
    _git(["remote", "set-url", "bare", str(tmp_path / "nowhere")], cwd=tmp_path / "planner" / "mypackage")
    monkeypatch.chdir(tmp_path)
    send = MagicMock()
    monkeypatch.setattr(tmux_dispatch, "send_prompt_to_agent", send)

    result = CliRunner().invoke(main, ["--config", str(config_path), "ask", "005"])

    assert result.exit_code == 0, result.output
    send.assert_called_once()
    assert _contains(result.output, "fetch skipped in")
    assert _git(["rev-parse", "--verify", "refs/remotes/bare/upstream-news"], cwd=tmp_path / "planner" / "dep1")


def test_new_in_oob_prepends_branches_frontmatter_for_every_code_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    # dep1's default branch is master (as seen from the planner clone)
    _git(["branch", "-m", "main", "master"], cwd=tmp_path / "bare" / "dep1")
    planner_dep1 = tmp_path / "planner" / "dep1"
    _git(["fetch", "--prune", "bare"], cwd=planner_dep1)
    _git(["remote", "set-head", "bare", "-a"], cwd=planner_dep1)
    monkeypatch.setattr(wc, "_open_in_editor", MagicMock())
    monkeypatch.setattr(wc, "git_add", MagicMock())

    result = _run(tmp_path, config_path, monkeypatch, "new", "foo")

    assert result.exit_code == 0, result.output
    intent = tmp_path / "canon" / "docs" / "plans" / "intent" / "001_foo.md"
    text = intent.read_text(encoding="utf-8")
    fm, body = strip_frontmatter(text)
    assert fm is not None
    assert list(fm.branches) == [repo.name for repo in read_metadata(tmp_path).code_repos]
    assert fm.branches["mypackage"] == RepoBranch(branch="feat/001_foo", base="main")
    assert fm.branches["dep1"] == RepoBranch(branch="feat/001_foo", base="master")
    assert body.startswith("# 001: Foo")
