"""End-to-end integration test: init -> todo -> implement -> check -> accept."""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

import workflow.commands.workflow as wc
import workflow.git_helpers as git_helpers_module
from workflow.audit import read_audit_log
from workflow.cli import main
from workflow.config import WORKFLOW_ROOT_MARKER
from workflow.git_helpers import CommitResult
from workflow.names import RepoName
from workflow.validate import CheckResult, _check_agents_md

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT = shutil.which("git") or "git"

_GIT_CLOCK = itertools.count(int(time.time()), 60)
"""Commit timestamps handed to git, one minute apart, starting around now."""


def _advance_clock() -> None:
    """Move the commit clock forward for every git process, ours and the workflow's.

    The multi-source plan resolver breaks same-second ties within a single repo's own
    history by git log traversal order, not recency; stacked-plan tests commit several
    times per second. Stamping the date via the environment keeps resolution
    deterministic without sleeping.
    """
    stamp = f"{next(_GIT_CLOCK)} +0000"
    os.environ["GIT_AUTHOR_DATE"] = stamp
    os.environ["GIT_COMMITTER_DATE"] = stamp


def _git(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([_GIT, *args], cwd=cwd, check=check, capture_output=True, **kwargs)  # noqa: S603


def _git_commit_without_noverify(
    repo_path: Path,
    msg: str,
    *,
    dry_run: bool = False,
    no_verify: bool = False,  # noqa: ARG001 — preserves git_commit signature
) -> None:
    """Real-git stand-in for git_commit: the sandbox git wrapper rejects --no-verify."""
    if not dry_run:
        _git(["commit", "-m", msg], cwd=repo_path)


def _git_commit_if_changed_without_noverify(
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
        [_GIT, "commit", "-m", msg],
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


def _init_canon_repo(tmp_path: Path) -> Path:
    """Create canon/ as a real git repo with one initial commit on 'main', return its path."""
    canon = tmp_path / "canon"
    canon.mkdir()
    _git(["init", str(canon)])
    _git(["config", "user.email", "test@example.com"], cwd=canon)
    _git(["config", "user.name", "Test"], cwd=canon)
    _git(["checkout", "-b", "main"], cwd=canon, check=False)
    (canon / "README.md").write_text("initial\n", encoding="utf-8")
    _git(["add", "."], cwd=canon)
    _git(["commit", "-m", "initial commit"], cwd=canon)
    return canon


def _make_integration_config(tmp_path: Path) -> Path:
    """Write a minimal workflow config derived from the default, return its path."""
    with (tmp_path / "run_agent_in_docker.sh").open("w") as dummy_script:
        dummy_script.write("#!/bin/bash")
    (tmp_path / "run_agent_in_docker.sh").chmod(0o755)
    src = Path(__file__).parent.parent / "docs" / "default_config.yaml"
    dest = tmp_path / "config.yaml"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    (tmp_path / "canon" / ".git-pre-commit-hook").touch()
    (tmp_path / "env.list").touch()
    return dest


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_happy_path_init_through_accept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0915
    """Full happy path: init -> planner edit -> todo -> implement -> builder edit -> check -> accept."""
    canon = _init_canon_repo(tmp_path)

    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    # Patch out expensive / unavailable side-effects
    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "_run_pre_commit_hook", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())

    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    # Step 2: workflow init (also wires origin remote and push config in all repos)
    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output

    # AGENTS.md must exist in canon, contain both markers, and be reachable
    # via the bare clone — proving the "commit before bare clone" ordering.
    canon_agents_md = canon / "AGENTS.md"
    assert canon_agents_md.exists()
    agents_md_text = canon_agents_md.read_text(encoding="utf-8")
    assert "<!-- WORKFLOW BEGIN -->" in agents_md_text
    assert "<!-- WORKFLOW END -->" in agents_md_text
    bare_blob_type = _git(["cat-file", "-t", "refs/heads/main:AGENTS.md"], cwd=tmp_path / "bare").stdout
    assert bare_blob_type.strip() == b"blob"

    # Set git user identity in each cloned repo (needed for commits made by the test).
    # push.autoSetupRemote, push.default=current, and the origin remote are set up by init.
    for repo_name in ("planner", "builder", "reviewer"):
        repo = tmp_path / repo_name
        _git(["config", "user.email", "test@example.com"], cwd=repo)
        _git(["config", "user.name", "Test"], cwd=repo)

    # Step 3: simulate planner edit - create plan on a feature branch
    planner = tmp_path / "planner"
    _git(["checkout", "-b", "plan/005_foo"], cwd=planner)
    plan_dir = planner / "docs" / "plans" / "planning"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "005_foo.md").write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")
    _git(["add", "."], cwd=planner)
    _git(["commit", "-m", "add planning plan"], cwd=planner)

    # Step 4: workflow todo - transfers branch + transitions to todo
    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "005_foo"])
    assert result.exit_code == 0, result.output

    # Step 5: workflow implement - transitions to implementing
    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005_foo"])
    assert result.exit_code == 0, result.output

    # Step 6: simulate builder edit - `implement` already left the builder on
    # feat/005_foo (branched from a freshly-pulled main), so just do real work
    # and commit. Leave the plan in implementing/ — the `check` script is
    # responsible for the implementing -> checking transition.
    builder = tmp_path / "builder"
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=builder).stdout.strip() == b"feat/005_foo"
    (builder / "README.md").write_text("builder work\n", encoding="utf-8")
    _git(["add", "."], cwd=builder)
    _git(["commit", "-m", "builder work on 005"], cwd=builder)

    # Step 7: workflow check - transitions implementing -> checking, then pushes.
    reviewer = tmp_path / "reviewer"
    result = CliRunner().invoke(main, ["--config", str(config_path), "check", "005_foo"])
    assert result.exit_code == 0, result.output
    # check must have moved the plan to checking/
    assert (reviewer / "docs" / "plans" / "checking" / "005_foo.md").exists()
    assert not (reviewer / "docs" / "plans" / "implementing" / "005_foo.md").exists()

    # Step 8: omitted (reviewer already on the feature branch)

    # Step 9: workflow accept
    result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005_foo"])
    assert result.exit_code == 0, result.output

    # Step 10: assert plan is now in canon/docs/plans/done/
    assert (canon / "docs" / "plans" / "done" / "005_foo.md").exists()

    # Step 11: assert audit log has all expected entries in order
    entries = read_audit_log(tmp_path)
    commands = [e.command for e in entries]
    for expected in ("init", "todo", "implement", "check", "accept"):
        assert expected in commands, f"Missing audit entry for {expected!r}"
    assert all(e.actor == "human" for e in entries)
    # Entries must be in chronological order
    idx = {cmd: commands.index(cmd) for cmd in ("init", "todo", "implement", "check", "accept")}
    assert idx["init"] < idx["todo"] < idx["implement"] < idx["check"] < idx["accept"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_check_agents_md_detects_branch_modification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate roundtrip through _check_agents_md after init.

    After init, _check_agents_md must fail when AGENTS.md is modified on a feature
    branch and pass once reverted.
    """
    _init_canon_repo(tmp_path)

    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output

    # Simulate a feature branch in builder modifying AGENTS.md.
    builder = tmp_path / "builder"
    _git(["config", "user.email", "test@example.com"], cwd=builder)
    _git(["config", "user.name", "Test"], cwd=builder)
    _git(["checkout", "-b", "feat/touch_agents_md"], cwd=builder)
    pristine = (builder / "AGENTS.md").read_text(encoding="utf-8")
    (builder / "AGENTS.md").write_text(pristine + "\nunsanctioned addition\n", encoding="utf-8")
    _git(["add", "AGENTS.md"], cwd=builder)
    _git(["commit", "-m", "tamper"], cwd=builder)

    # Hash mismatch on the modified branch -> fail with the hash-mismatch message.
    result_fail = _check_agents_md(builder, tmp_path, ["AGENTS.md"])
    assert result_fail.status == "fail"
    assert "hash does not match" in result_fail.message

    # Revert and assert the check passes (same content as canon, file not in changed_files).
    (builder / "AGENTS.md").write_text(pristine, encoding="utf-8")
    result_pass = _check_agents_md(builder, tmp_path, [])
    assert result_pass.status == "pass"


# ---------------------------------------------------------------------------
# init resume / --continue tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_init_rerun_without_continue_aborts_and_prints_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running `init` once the agent clones exist must abort with the resume preview, unmutated."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    first = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert first.exit_code == 0, first.output

    bare_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "bare").stdout
    planner_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "planner").stdout

    second = CliRunner().invoke(main, ["--config", str(config_path), "init"])

    assert second.exit_code == 1
    assert "Resume plan for `init`:" in second.output
    assert "--continue" in second.output
    # Nothing should have been mutated by the aborted rerun.
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "bare").stdout == bare_head_before
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "planner").stdout == planner_head_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_init_rerun_with_continue_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`init --continue` accepts the existing clones as-is and completes successfully."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    first = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert first.exit_code == 0, first.output

    second = CliRunner().invoke(main, ["--config", str(config_path), "init", "--continue"])

    assert second.exit_code == 0, second.output
    assert (tmp_path / WORKFLOW_ROOT_MARKER).exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_init_rerun_with_continue_does_not_redo_satisfied_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed init must not regenerate AGENTS.md or re-push canon's already-wired origin.

    Regression test for Fix 2 (020.1): _run_init_steps used to re-derive postconditions
    instead of consulting the classify pass, so a fully-satisfied resume still rewrote
    AGENTS.md and re-ran `git push -u origin HEAD` every time.
    """
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    first = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert first.exit_code == 0, first.output

    agents_md_mtime_before = (tmp_path / "canon" / "AGENTS.md").stat().st_mtime_ns
    canon_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout
    bare_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "bare").stdout

    fake_generate = MagicMock(side_effect=wc.generate_and_write_agents_md)
    monkeypatch.setattr(wc, "generate_and_write_agents_md", fake_generate)
    run_command_calls: list[list[str]] = []
    real_run_command = wc.run_command

    def _spy_run_command(
        cmd: list[str],
        *,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[bytes] | None:
        run_command_calls.append(cmd)
        return real_run_command(cmd, dry_run=dry_run, **kwargs)

    monkeypatch.setattr(wc, "run_command", _spy_run_command)

    second = CliRunner().invoke(main, ["--config", str(config_path), "init", "--continue"])

    assert second.exit_code == 0, second.output
    fake_generate.assert_not_called()
    assert ["git", "push", "-u", "origin", "HEAD"] not in run_command_calls
    assert (tmp_path / "canon" / "AGENTS.md").stat().st_mtime_ns == agents_md_mtime_before
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout == canon_head_before
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "bare").stdout == bare_head_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_init_dry_run_previews_resume_without_mutating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partially-completed init followed by a dry-run must preview accurately without mutating."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    first = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert first.exit_code == 0, first.output

    marker_before = (tmp_path / WORKFLOW_ROOT_MARKER).exists()
    bare_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "bare").stdout

    # Rerunning without --continue hits the rough (clone-exists) gate even in dry-run;
    # the point of this test is that the *read-only* guards still ran for real and
    # produced an accurate preview, and nothing was mutated either way.
    result = CliRunner().invoke(main, ["--dry-run", "--config", str(config_path), "init"])

    assert result.exit_code == 1
    assert "[skip] bare clone" in result.output
    assert "[skip] AGENTS.md committed" in result.output
    assert "--continue" in result.output
    assert marker_before == (tmp_path / WORKFLOW_ROOT_MARKER).exists()
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "bare").stdout == bare_head_before

    # With --continue, dry-run previews the same plan and completes without mutating.
    result_cont = CliRunner().invoke(main, ["--dry-run", "--config", str(config_path), "init", "--continue"])
    assert result_cont.exit_code == 0, result_cont.output
    assert "[skip] bare clone" in result_cont.output
    assert marker_before == (tmp_path / WORKFLOW_ROOT_MARKER).exists()
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "bare").stdout == bare_head_before


# ---------------------------------------------------------------------------
# check / accept / implement resume tests
# ---------------------------------------------------------------------------


def _drive_pipeline_through_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run init -> planner edit -> todo -> implement -> builder edit -> check, for real."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "_run_pre_commit_hook", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output

    for repo_name in ("planner", "builder", "reviewer"):
        repo = tmp_path / repo_name
        _git(["config", "user.email", "test@example.com"], cwd=repo)
        _git(["config", "user.name", "Test"], cwd=repo)

    planner = tmp_path / "planner"
    _git(["checkout", "-b", "plan/005_foo"], cwd=planner)
    plan_dir = planner / "docs" / "plans" / "planning"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "005_foo.md").write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")
    _git(["add", "."], cwd=planner)
    _git(["commit", "-m", "add planning plan"], cwd=planner)

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "005_foo"])
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005_foo"])
    assert result.exit_code == 0, result.output

    builder = tmp_path / "builder"
    (builder / "README.md").write_text("builder work\n", encoding="utf-8")
    _git(["add", "."], cwd=builder)
    _git(["commit", "-m", "builder work on 005"], cwd=builder)

    result = CliRunner().invoke(main, ["--config", str(config_path), "check", "005_foo"])
    assert result.exit_code == 0, result.output


def _drive_pipeline_through_todo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run init -> planner edit -> todo, for real."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output

    for repo_name in ("planner", "builder", "reviewer"):
        repo = tmp_path / repo_name
        _git(["config", "user.email", "test@example.com"], cwd=repo)
        _git(["config", "user.name", "Test"], cwd=repo)

    planner = tmp_path / "planner"
    _git(["checkout", "-b", "plan/005_foo"], cwd=planner)
    plan_dir = planner / "docs" / "plans" / "planning"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "005_foo.md").write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")
    _git(["add", "."], cwd=planner)
    _git(["commit", "-m", "add planning plan"], cwd=planner)

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "005_foo"])
    assert result.exit_code == 0, result.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_todo_rerun_after_full_success_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running `todo` after it already fully succeeded must be idempotent."""
    _drive_pipeline_through_todo(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    assert (tmp_path / "canon" / "docs" / "plans" / "todo" / "005_foo.md").exists()

    canon_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout
    planner_branch_before = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "planner").stdout

    # See the qualified-path comment in test_check_rerun_after_full_success_is_a_noop.
    result = CliRunner().invoke(
        main,
        ["--config", str(config_path), "todo", "canon/docs/plans/todo/005_foo.md"],
    )

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout == canon_head_before
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "planner").stdout == planner_branch_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_check_rerun_after_full_success_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running `check` after it already fully succeeded must be idempotent."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"

    reviewer_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "reviewer").stdout
    builder_branch_before = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "builder").stdout

    # A fully-qualified path sidesteps a pre-existing plan_resolver limitation: two audit
    # entries for the same command ("check" here) disagree on repo (subcommand pins
    # builder, the second run's own audit entry pins reviewer) when resolved by bare name.
    # builder itself already moved on to 'main' (last check step), so the transitioned
    # file is only reachable via the reviewer clone (still on the feature branch).
    result = CliRunner().invoke(
        main,
        ["--config", str(config_path), "check", "reviewer/docs/plans/checking/005_foo.md"],
    )

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "reviewer").stdout == reviewer_head_before
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "builder").stdout == builder_branch_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_accept_rerun_after_full_success_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running `accept` after it already fully succeeded must be idempotent."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"

    result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005_foo"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "canon" / "docs" / "plans" / "done" / "005_foo.md").exists()

    canon_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout
    reviewer_branch_before = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "reviewer").stdout

    # See the qualified-path comment in test_check_rerun_after_full_success_is_a_noop.
    result = CliRunner().invoke(
        main,
        ["--config", str(config_path), "accept", "canon/docs/plans/done/005_foo.md"],
    )

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout == canon_head_before
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "reviewer").stdout == reviewer_branch_before


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_todo_dry_run_previews_rebase_without_mutating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run on a rebase-pending `todo` previews the rebase step and mutates nothing."""
    _drive_pipeline_through_todo(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    planner = tmp_path / "planner"

    _git(["checkout", "-b", "plan/006_bar"], cwd=planner)
    _make_plan(planner, "planning", "006_bar.md")
    _git(["add", "."], cwd=planner)
    _git(["commit", "-m", "add planning plan 006"], cwd=planner)
    _advance_canon_main_unrelated(tmp_path)

    planner_head_before = _git(["rev-parse", "HEAD"], cwd=planner).stdout
    canon_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout
    bare_main_before = _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare").stdout

    result = CliRunner().invoke(main, ["--dry-run", "--config", str(config_path), "todo", "006_bar"])

    assert result.exit_code == 0, result.output
    assert "rebase planner branch onto main" in result.output
    assert _git(["rev-parse", "HEAD"], cwd=planner).stdout == planner_head_before
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout == canon_head_before
    assert _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare").stdout == bare_main_before


def _advance_canon_main_unrelated(tmp_path: Path, *, filename: str = "unrelated.txt") -> None:
    """Add and push an unrelated commit to canon's main, simulating an interleaved transition."""
    canon = tmp_path / "canon"
    (canon / filename).write_text("unrelated\n", encoding="utf-8")
    _git(["add", "."], cwd=canon)
    _git(["commit", "-m", "unrelated main commit"], cwd=canon)
    _git(["push"], cwd=canon)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_accept_rebases_when_main_has_advanced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`accept` rebases the feature branch when main advanced after check; main fast-forwards."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    _advance_canon_main_unrelated(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005_foo"])

    assert result.exit_code == 0, result.output
    assert "rebased" in result.output.lower()
    canon = tmp_path / "canon"
    assert (canon / "docs" / "plans" / "done" / "005_foo.md").exists()
    assert (canon / "unrelated.txt").exists()
    assert (canon / "README.md").read_text(encoding="utf-8") == "builder work\n"
    bare_main = _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare").stdout.decode().strip()
    canon_head = _git(["rev-parse", "HEAD"], cwd=canon).stdout.decode().strip()
    assert bare_main == canon_head


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_accept_conflict_fails_fast_and_leaves_branch_unrebased(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine conflict aborts the rebase; the branch and main are both left untouched."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    canon = tmp_path / "canon"
    reviewer = tmp_path / "reviewer"
    reviewer_head_before = _git(["rev-parse", "HEAD"], cwd=reviewer).stdout
    canon_head_before = _git(["rev-parse", "HEAD"], cwd=canon).stdout

    # Conflicting edit to the same file builder touched (README.md), pushed to main.
    (canon / "README.md").write_text("conflicting main edit\n", encoding="utf-8")
    _git(["add", "."], cwd=canon)
    _git(["commit", "-m", "conflicting main commit"], cwd=canon)
    _git(["push"], cwd=canon)

    result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005_foo"])

    assert result.exit_code == 1, result.output
    assert "README.md" in result.output
    assert "workflow rebase" in result.output
    assert _git(["rev-parse", "HEAD"], cwd=reviewer).stdout == reviewer_head_before
    assert _git(["rev-parse", "HEAD"], cwd=canon).stdout != canon_head_before  # canon's own commit landed, untouched
    assert not (canon / "docs" / "plans" / "done" / "005_foo.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_accept_resumes_as_fast_forward_after_canon_merge_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the rebase/force-push succeed but canon's own push fails, a rerun is a plain fast-forward."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    _advance_canon_main_unrelated(tmp_path)

    real_git_push = git_helpers_module.git_push
    call_count = {"n": 0}

    def _flaky_canon_push(repo_path: Path, *, dry_run: bool = False) -> None:
        call_count["n"] += 1
        # Let the source repo's force-push through; fail canon's own push once.
        if repo_path == tmp_path / "canon":
            msg = "simulated transient push failure"
            raise subprocess.CalledProcessError(1, ["git", "push"], msg.encode())
        real_git_push(repo_path, dry_run=dry_run)

    monkeypatch.setattr(git_helpers_module, "git_push", _flaky_canon_push)
    first = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005_foo"])
    assert first.exit_code == 1, first.output

    monkeypatch.setattr(git_helpers_module, "git_push", real_git_push)
    second = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005_foo"])

    assert second.exit_code == 0, second.output
    assert "rebased" not in second.output.lower()  # the rerun is a plain fast-forward, not a rebase
    canon = tmp_path / "canon"
    assert (canon / "docs" / "plans" / "done" / "005_foo.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_accept_dry_run_previews_rebase_without_mutating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run on a rebase-pending accept previews the rebase step and mutates nothing."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    _advance_canon_main_unrelated(tmp_path)
    reviewer_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "reviewer").stdout
    canon_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout
    bare_main_before = _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare").stdout

    result = CliRunner().invoke(main, ["--dry-run", "--config", str(config_path), "accept", "005_foo"])

    assert result.exit_code == 0, result.output
    assert "rebase reviewer branch onto main" in result.output
    # The cleanup preview must not warn about the branch not being merged: nothing merged it.
    assert "not (yet) known to be merged" not in result.output
    assert "would delete feat/005_foo" in result.output
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "reviewer").stdout == reviewer_head_before
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout == canon_head_before
    assert _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare").stdout == bare_main_before


def _init_with_two_sibling_plan_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """init, two planner branches cut from the same base, first one landed. Returns the hook mock.

    The concurrently-cut sibling case (as ``subdivide``/``mvp`` produce): landing
    ``plan/005_foo`` advances canon's ``main`` past ``plan/006_bar``'s base, so the second
    handoff genuinely cannot fast-forward and must rebase. Planner is left on
    ``plan/006_bar``, ready for the handoff under test.
    """
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    hook = MagicMock()
    monkeypatch.setattr(wc, "_run_pre_commit_hook", hook)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output

    planner = tmp_path / "planner"
    _git(["config", "user.email", "test@example.com"], cwd=planner)
    _git(["config", "user.name", "Test"], cwd=planner)
    for plan_name in ("005_foo", "006_bar"):
        _git(["checkout", "main"], cwd=planner)
        _git(["checkout", "-b", f"plan/{plan_name}"], cwd=planner)
        _make_plan(planner, "planning", f"{plan_name}.md")
        _git(["add", "."], cwd=planner)
        _git(["commit", "-m", f"add planning plan {plan_name}"], cwd=planner)

    _git(["checkout", "plan/005_foo"], cwd=planner)
    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "005_foo"])
    assert result.exit_code == 0, result.output
    _git(["checkout", "plan/006_bar"], cwd=planner)
    hook.reset_mock()
    return hook


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_todo_rebases_sibling_branch_cut_from_the_same_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`todo` on a concurrently-cut sibling branch rebases it, verifying the rebase in planner."""
    hook = _init_with_two_sibling_plan_branches(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "006_bar"])

    assert result.exit_code == 0, result.output
    assert "rebased" in result.output.lower()
    canon = tmp_path / "canon"
    assert (canon / "docs" / "plans" / "todo" / "006_bar.md").exists()
    # The commit already on main (the first sibling's own handoff) survived the rebase.
    assert (canon / "docs" / "plans" / "todo" / "005_foo.md").exists()
    assert (
        _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare").stdout
        == _git(
            ["rev-parse", "HEAD"],
            cwd=canon,
        ).stdout
    )
    # post_rebase_verify re-ran the pre-commit hook, against planner (the rebased repo).
    assert [call.args[1] for call in hook.call_args_list] == [RepoName.planner]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_rebases_sibling_branch_cut_from_the_same_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`implement` straight from planning rebases the same way, and still creates the feat branch."""
    hook = _init_with_two_sibling_plan_branches(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "006_bar"])

    assert result.exit_code == 0, result.output
    assert "rebased" in result.output.lower()
    canon = tmp_path / "canon"
    assert (canon / "docs" / "plans" / "implementing" / "006_bar.md").exists()
    assert (canon / "docs" / "plans" / "todo" / "005_foo.md").exists()
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path / "builder").stdout.strip() == b"feat/006_bar"
    assert [call.args[1] for call in hook.call_args_list] == [RepoName.planner]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_accept_deletes_feature_branch_locally_and_on_bare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`accept` deletes the merged feature branch in reviewer, builder, and on bare."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"

    result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005_foo"])

    assert result.exit_code == 0, result.output
    reviewer_check = _git(
        ["rev-parse", "--verify", "--quiet", "refs/heads/feat/005_foo"],
        cwd=tmp_path / "reviewer",
        check=False,
    )
    builder_check = _git(
        ["rev-parse", "--verify", "--quiet", "refs/heads/feat/005_foo"],
        cwd=tmp_path / "builder",
        check=False,
    )
    remote_check = _git(
        ["ls-remote", "--exit-code", "--heads", "origin", "feat/005_foo"],
        cwd=tmp_path / "canon",
        check=False,
    )
    assert reviewer_check.returncode != 0
    assert builder_check.returncode != 0
    assert remote_check.returncode != 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_reject_checking_plan_through_default_canon_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`reject`'s default invocation (--target-repo canon) on a `checking` plan must succeed.

    Regression test for the path-mismatch defect (plan 024 Phase 1): canon's `main` still
    has the plan file under `implementing/` (the `checking` transition only ever landed on
    the feature branch), so the `git mv` source must be located by scanning canon, not by
    trusting the resolved plan's (feature-branch) state directory.
    """
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    canon = tmp_path / "canon"
    assert (canon / "docs" / "plans" / "implementing" / "005_foo.md").exists()

    result = CliRunner().invoke(main, ["--config", str(config_path), "reject", "005_foo"])

    assert result.exit_code == 0, result.output
    assert (canon / "docs" / "plans" / "rejected" / "005_foo.md").exists()
    assert not (canon / "docs" / "plans" / "implementing" / "005_foo.md").exists()
    assert "left unmerged" in result.output
    # The feature branch must not have been merged into canon's main.
    canon_log = _git(["log", "--oneline", "-1"], cwd=canon).stdout.decode()
    assert "builder work" not in canon_log


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_reject_resumes_after_mv_without_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A resumed `reject` (file already `git mv`-ed but not committed) must complete."""
    _drive_pipeline_through_check(tmp_path, monkeypatch)
    config_path = tmp_path / "config.yaml"
    reviewer = tmp_path / "reviewer"

    # Simulate a prior reject run interrupted right after `git mv`, before the commit.
    rejected_dir = reviewer / "docs" / "plans" / "rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    _git(["mv", "docs/plans/checking/005_foo.md", "docs/plans/rejected/005_foo.md"], cwd=reviewer)
    assert not (reviewer / "docs" / "plans" / "checking" / "005_foo.md").exists()
    assert (rejected_dir / "005_foo.md").exists()

    # Fully-qualified path (see the qualified-path comment on test_check_rerun_after_full_
    # success_is_a_noop): resolves straight to REJECTED from the file's current directory,
    # exercising the no-early-exit-shortcut path in `reject`.
    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(config_path),
            "reject",
            "--target-repo",
            "reviewer",
            "reviewer/docs/plans/rejected/005_foo.md",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (rejected_dir / "005_foo.md").exists()
    assert not _git(["status", "--porcelain"], cwd=reviewer).stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_rerun_after_full_success_leaves_builder_on_feature_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running `implement` after success must not force builder back onto 'main'."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)
    monkeypatch.setattr(wc.time, "sleep", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output
    for repo_name in ("planner", "builder", "reviewer"):
        repo = tmp_path / repo_name
        _git(["config", "user.email", "test@example.com"], cwd=repo)
        _git(["config", "user.name", "Test"], cwd=repo)

    _make_plan(tmp_path / "canon", "todo", "005_foo.md")
    _git(["add", "."], cwd=tmp_path / "canon")
    _git(["commit", "-m", "add todo plan"], cwd=tmp_path / "canon")
    _git(["push"], cwd=tmp_path / "canon")

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005_foo"])
    assert result.exit_code == 0, result.output

    builder = tmp_path / "builder"
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=builder).stdout.strip() == b"feat/005_foo"
    # Simulate work in progress on the feature branch.
    (builder / "in_progress.txt").write_text("wip\n", encoding="utf-8")
    _git(["add", "."], cwd=builder)
    _git(["commit", "-m", "wip"], cwd=builder)
    wip_head = _git(["rev-parse", "HEAD"], cwd=builder).stdout

    # See the qualified-path comment in test_check_rerun_after_full_success_is_a_noop.
    result = CliRunner().invoke(
        main,
        ["--config", str(config_path), "implement", "canon/docs/plans/implementing/005_foo.md"],
    )

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=builder).stdout.strip() == b"feat/005_foo"
    assert _git(["rev-parse", "HEAD"], cwd=builder).stdout == wip_head


def _make_plan(repo_path: Path, state: str, filename: str) -> None:
    plan_dir = repo_path / "docs" / "plans" / state
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / filename).write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_todo_succeeds_when_second_branch_cut_after_first_todo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 0 reproduction (plan 024): a second plan branch, cut only after the first `todo`.

    `todo 005_foo` succeeds, but (pre-fix) leaves planner's local `main` one commit behind
    bare's -- missing its own "Transition 005_foo to todo" commit, because `transfer_branch_
    to_main`'s pull of planner's `main` runs *before* that commit is pushed. Cutting
    `plan/006_bar` from that stale `main` and running `todo 006_bar` then fails its
    `--ff-only` merge in canon, because canon's `main` has since advanced past `006_bar`'s
    base. This is the ordering hazard fixed by Phase 2's `park_on_main` (issue 1); it does
    *not* require Phase 3's rebase fallback, unlike a `plan/*` pair cut concurrently from the
    same base (which genuinely diverges and needs a rebase regardless of parking).
    """
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output

    planner = tmp_path / "planner"
    _git(["config", "user.email", "test@example.com"], cwd=planner)
    _git(["config", "user.name", "Test"], cwd=planner)

    _git(["checkout", "-b", "plan/005_foo"], cwd=planner)
    _make_plan(planner, "planning", "005_foo.md")
    _git(["add", "."], cwd=planner)
    _git(["commit", "-m", "add planning plan 005"], cwd=planner)

    # `todo` 005 while planner is on plan/005_foo: succeeds.
    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "005_foo"])
    assert result.exit_code == 0, result.output

    # Cut the second branch only now, from whatever planner's local main ended up at.
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=planner).stdout.strip() == b"main"
    _git(["checkout", "-b", "plan/006_bar"], cwd=planner)
    _make_plan(planner, "planning", "006_bar.md")
    _git(["add", "."], cwd=planner)
    _git(["commit", "-m", "add planning plan 006"], cwd=planner)

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "006_bar"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "canon" / "docs" / "plans" / "todo" / "006_bar.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_needs_continue_for_leftover_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-existing feat/<plan> branch, while the plan is still `todo`, gates behind --continue."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)
    monkeypatch.setattr(wc.time, "sleep", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output
    for repo_name in ("planner", "builder", "reviewer"):
        repo = tmp_path / repo_name
        _git(["config", "user.email", "test@example.com"], cwd=repo)
        _git(["config", "user.name", "Test"], cwd=repo)

    _make_plan(tmp_path / "canon", "todo", "005_foo.md")
    _git(["add", "."], cwd=tmp_path / "canon")
    _git(["commit", "-m", "add todo plan"], cwd=tmp_path / "canon")
    _git(["push"], cwd=tmp_path / "canon")

    builder = tmp_path / "builder"
    _git(["checkout", "-b", "feat/005_foo"], cwd=builder)
    _git(["checkout", "main"], cwd=builder)

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005_foo"])

    assert result.exit_code == 1
    assert "pass --continue" in result.output
    assert (tmp_path / "canon" / "docs" / "plans" / "todo" / "005_foo.md").exists()

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005_foo", "--continue"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "canon" / "docs" / "plans" / "implementing" / "005_foo.md").exists()


# ---------------------------------------------------------------------------
# Stacked plans (`implement --onto`)
# ---------------------------------------------------------------------------


def _setup_stacked_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Init a project and set git identity in every agent clone; return the config path."""
    _init_canon_repo(tmp_path)
    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "_run_pre_commit_hook", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)
    for var in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(var, "")  # registers the var for teardown; _advance_clock owns the value
    _advance_clock()

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output
    for repo_name in ("planner", "builder", "reviewer"):
        repo = tmp_path / repo_name
        _git(["config", "user.email", "test@example.com"], cwd=repo)
        _git(["config", "user.name", "Test"], cwd=repo)
    return config_path


def _create_and_todo_plan(config_path: Path, tmp_path: Path, plan_name: str) -> None:
    """Planner-create a plan on its own `plan/*` branch, cut from current main, and `todo` it."""
    planner = tmp_path / "planner"
    _git(["checkout", "main"], cwd=planner)
    _git(["pull"], cwd=planner, check=False)
    _git(["checkout", "-b", f"plan/{plan_name}"], cwd=planner)
    _make_plan(planner, "planning", f"{plan_name}.md")
    _git(["add", "."], cwd=planner)
    _git(["commit", "-m", f"add planning plan {plan_name}"], cwd=planner)

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", plan_name])
    assert result.exit_code == 0, result.output
    _advance_clock()


def _builder_commit(tmp_path: Path, filename: str, message: str) -> None:
    builder = tmp_path / "builder"
    (builder / filename).write_text(f"{message}\n", encoding="utf-8")
    _git(["add", "."], cwd=builder)
    _git(["commit", "-m", message], cwd=builder)
    _advance_clock()


def _run(config_path: Path, *args: str) -> Any:
    """Invoke a workflow subcommand, then move the commit clock on for the next one."""
    result = CliRunner().invoke(main, ["--config", str(config_path), *args])
    _advance_clock()
    return result


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_stacked_plans_full_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Implement A -> commit -> implement B --onto A -> commit -> check B -> accept B."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    canon = tmp_path / "canon"
    builder = tmp_path / "builder"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    result = _run(config_path, "implement", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    result = _run(config_path, "implement", "006_bar", "--onto", "005_foo")
    assert result.exit_code == 0, result.output

    # No feat/006_bar branch was ever created; B lives on A's branch.
    assert _git(["branch", "--list", "feat/006_bar"], cwd=builder).stdout.strip() == b""
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=builder).stdout.strip() == b"feat/005_foo"
    assert (canon / "docs" / "plans" / "implementing" / "006_bar.md").exists()
    assert "onto: 005_foo" in (canon / "docs" / "plans" / "implementing" / "006_bar.md").read_text(encoding="utf-8")

    _builder_commit(tmp_path, "bar.txt", "builder work on bar")

    result = _run(config_path, "check", "006_bar")
    assert result.exit_code == 0, result.output
    reviewer = tmp_path / "reviewer"
    assert (reviewer / "docs" / "plans" / "checking" / "005_foo.md").exists()
    assert (reviewer / "docs" / "plans" / "checking" / "006_bar.md").exists()

    result = _run(config_path, "accept", "006_bar")
    assert result.exit_code == 0, result.output

    assert (canon / "docs" / "plans" / "done" / "005_foo.md").exists()
    assert (canon / "docs" / "plans" / "done" / "006_bar.md").exists()

    # The shared branch is gone everywhere.
    for repo in (canon, builder, reviewer, tmp_path / "bare"):
        assert _git(["branch", "--list", "feat/005_foo"], cwd=repo).stdout.strip() == b""

    # main carries both builder commits.
    assert (canon / "foo.txt").exists()
    assert (canon / "bar.txt").exists()

    entries = read_audit_log(tmp_path)
    check_entry = next(e for e in entries if e.command == "check" and e.input_plan == "006_bar")
    assert check_entry.output_plans == ["005_foo"]
    accept_entry = next(e for e in entries if e.command == "accept" and e.input_plan == "006_bar")
    assert accept_entry.output_plans == ["005_foo"]
    implement_entry = next(e for e in entries if e.command == "implement" and e.input_plan == "006_bar")
    assert implement_entry.stacked_on == ["005_foo"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_check_and_accept_require_stack_tip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`check`/`accept` on a mid-stack plan refuse, naming the tip to use instead."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    result = _run(config_path, "implement", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    result = _run(config_path, "implement", "006_bar", "--onto", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "bar.txt", "builder work on bar")

    # check on the base (not the tip) refuses.
    result = _run(config_path, "check", "005_foo")
    assert result.exit_code == 1
    assert "006_bar" in result.output

    # Cascade both to checking via the tip.
    result = _run(config_path, "check", "006_bar")
    assert result.exit_code == 0, result.output

    # accept on the base (not the tip) refuses while the child is still checking.
    result = _run(config_path, "accept", "005_foo")
    assert result.exit_code == 1
    assert "006_bar" in result.output

    # Closing out through the tip succeeds.
    result = _run(config_path, "accept", "006_bar")
    assert result.exit_code == 0, result.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_reject_tip_before_check_then_accept_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejecting a stacked tip while it is still `implementing` unblocks the base for `check`/`accept`.

    Rejecting after `check` has already cascaded the tip onto the shared branch is not
    exercised here: the branch would still carry the tip's (unrejected) commits, which is
    exactly what reject's rollback hint's manual `git reset --hard` is for.
    """
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    canon = tmp_path / "canon"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    result = _run(config_path, "implement", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    result = _run(config_path, "implement", "006_bar", "--onto", "005_foo")
    assert result.exit_code == 0, result.output

    # Reject the tip before check ever cascades it onto the branch.
    result = _run(config_path, "reject", "006_bar")
    assert result.exit_code == 0, result.output
    assert (canon / "docs" / "plans" / "rejected" / "006_bar.md").exists()

    # The base is actionable again: check/accept see a stack of one.
    result = _run(config_path, "check", "005_foo")
    assert result.exit_code == 0, result.output

    result = _run(config_path, "accept", "005_foo")
    assert result.exit_code == 0, result.output
    assert (canon / "docs" / "plans" / "done" / "005_foo.md").exists()
    assert (canon / "docs" / "plans" / "done" / "005_foo.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_onto_refused_when_base_already_has_a_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second `--onto` against the same base must be refused; use the tip instead."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    result = _run(config_path, "implement", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    result = _run(config_path, "implement", "006_bar", "--onto", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "bar.txt", "builder work on bar")

    _create_and_todo_plan(config_path, tmp_path, "007_baz")
    result = _run(config_path, "implement", "007_baz", "--onto", "005_foo")

    assert result.exit_code == 1
    assert "006_bar" in result.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_unstacked_implement_refused_while_builder_busy_on_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain `implement` refuses to run while builder sits on another plan's unfinished branch."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    result = _run(config_path, "implement", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    result = _run(config_path, "implement", "006_bar")

    assert result.exit_code == 1
    assert "005_foo" in result.output
    assert "--onto" in result.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_onto_infers_base_from_builder_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--onto` with no value infers the base from the branch builder is already on."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    canon = tmp_path / "canon"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    result = _run(config_path, "implement", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    result = _run(config_path, "implement", "006_bar", "--onto")

    assert result.exit_code == 0, result.output
    assert (canon / "docs" / "plans" / "implementing" / "006_bar.md").exists()
    assert "onto: 005_foo" in (canon / "docs" / "plans" / "implementing" / "006_bar.md").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_onto_dry_run_mutates_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    builder = tmp_path / "builder"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    result = _run(config_path, "implement", "005_foo")
    assert result.exit_code == 0, result.output
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    builder_head_before = _git(["rev-parse", "HEAD"], cwd=builder).stdout
    canon_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--config", str(config_path), "implement", "006_bar", "--onto", "005_foo"],
    )

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "HEAD"], cwd=builder).stdout == builder_head_before
    assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon").stdout == canon_head_before
    assert (tmp_path / "canon" / "docs" / "plans" / "todo" / "006_bar.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_stacked_accept_rebases_when_main_advanced_after_onto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` moving after `implement --onto` sends `accept` down the rebase path, merge commit and all."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    canon = tmp_path / "canon"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    assert _run(config_path, "implement", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    assert _run(config_path, "implement", "006_bar", "--onto", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "bar.txt", "builder work on bar")

    _advance_canon_main_unrelated(tmp_path)
    _advance_clock()

    assert _run(config_path, "check", "006_bar").exit_code == 0
    result = _run(config_path, "accept", "006_bar")

    assert result.exit_code == 0, result.output
    assert "rebased" in result.output.lower()
    for name in ("foo.txt", "bar.txt", "unrelated.txt"):
        assert (canon / name).exists(), name
    assert (canon / "docs" / "plans" / "done" / "005_foo.md").exists()
    assert (canon / "docs" / "plans" / "done" / "006_bar.md").exists()
    bare_main = _git(["rev-parse", "refs/heads/main"], cwd=tmp_path / "bare").stdout
    assert bare_main == _git(["rev-parse", "HEAD"], cwd=canon).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_onto_refused_when_reviewer_has_unpushed_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A base in `checking` with reviewer commits not yet pushed must not be stacked onto."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    reviewer = tmp_path / "reviewer"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    assert _run(config_path, "implement", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")
    assert _run(config_path, "check", "005_foo").exit_code == 0

    # Reviewer writes its review but has not pushed it yet.
    (reviewer / "review.md").write_text("findings\n", encoding="utf-8")
    _git(["add", "."], cwd=reviewer)
    _git(["commit", "-m", "review 005_foo"], cwd=reviewer)
    _advance_clock()

    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    result = _run(config_path, "implement", "006_bar", "--onto", "005_foo")

    assert result.exit_code == 1
    assert "not yet pushed" in result.output
    assert not (tmp_path / "canon" / "docs" / "plans" / "implementing" / "006_bar.md").exists()

    # Pushing the review unblocks the stack.
    _git(["push"], cwd=reviewer)
    _advance_clock()
    assert _run(config_path, "implement", "006_bar", "--onto", "005_foo").exit_code == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_implement_onto_refuses_to_stack_a_plan_onto_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare `--onto` while builder is on the plan's own branch must not write a self-referential onto."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    plan_file = tmp_path / "canon" / "docs" / "plans" / "implementing" / "005_foo.md"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    assert _run(config_path, "implement", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")

    result = _run(config_path, "implement", "canon/docs/plans/implementing/005_foo.md", "--onto")

    assert result.exit_code == 1
    assert "onto itself" in result.output
    assert "onto:" not in plan_file.read_text(encoding="utf-8")
    # The plan is still fully actionable.
    assert _run(config_path, "check", "005_foo").exit_code == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_reject_stacked_plan_prints_branch_reset_that_spares_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After `check`, builder is on main -- the rollback hint must retarget the branch, not reset main."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    builder = tmp_path / "builder"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    assert _run(config_path, "implement", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")
    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    assert _run(config_path, "implement", "006_bar", "--onto", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "bar.txt", "builder work on bar")
    assert _run(config_path, "check", "006_bar").exit_code == 0

    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=builder).stdout.strip() == b"main"
    result = _run(config_path, "reject", "006_bar")

    assert result.exit_code == 0, result.output
    printed = " ".join(result.output.split())
    assert "checkout feat/005_foo" in printed
    assert printed.index("checkout feat/005_foo") < printed.index("reset --hard")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_doctor_is_clean_after_an_accepted_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A done plan's frontmatter naming the deleted shared branch is a record, not an anomaly."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    assert _run(config_path, "implement", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")
    _create_and_todo_plan(config_path, tmp_path, "006_bar")
    assert _run(config_path, "implement", "006_bar", "--onto", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "bar.txt", "builder work on bar")
    assert _run(config_path, "check", "006_bar").exit_code == 0
    assert _run(config_path, "accept", "006_bar").exit_code == 0

    result = _run(config_path, "doctor")

    # Only the stack section is asserted on: this fixture's canon carries an untracked
    # pre-commit hook, which doctor rightly reports as a dirty worktree.
    assert "Stack anomalies" not in result.output, result.output
    assert "exists nowhere" not in result.output, result.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_check_refuses_when_builder_is_on_another_feature_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`check` pushes builder's current branch, so it must be the branch recorded for the plan."""
    config_path = _setup_stacked_project(tmp_path, monkeypatch)
    builder = tmp_path / "builder"

    _create_and_todo_plan(config_path, tmp_path, "005_foo")
    assert _run(config_path, "implement", "005_foo").exit_code == 0
    _builder_commit(tmp_path, "foo.txt", "builder work on foo")
    _git(["checkout", "-b", "feat/999_elsewhere"], cwd=builder)

    result = _run(config_path, "check", "005_foo")

    assert result.exit_code == 1
    assert "feat/005_foo" in result.output
    assert "feat/999_elsewhere" in result.output
    assert not (tmp_path / "reviewer" / "docs" / "plans" / "checking" / "005_foo.md").exists()
