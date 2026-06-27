"""End-to-end integration test: init -> todo -> implement -> check -> accept."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

import workflow.commands.workflow as wc
from workflow.audit import read_audit_log
from workflow.cli import main
from workflow.config import WORKFLOW_ROOT_MARKER
from workflow.git_helpers import CommitResult
from workflow.validate import CheckResult, _check_agents_md

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT = shutil.which("git") or "git"


def _git(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([_GIT, *args], cwd=cwd, check=check, capture_output=True, **kwargs)  # noqa: S603


def _make_integration_config(tmp_path: Path) -> Path:
    """Write a minimal workflow config derived from the default, return its path."""
    (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    with (tmp_path / "run_agent_in_docker.sh").open("w") as dummy_script:
        dummy_script.write("#!/bin/bash")
    (tmp_path / "run_agent_in_docker.sh").chmod(0o755)
    src = Path(__file__).parent.parent / "docs" / "default_config.yaml"
    dest = tmp_path / "config.yaml"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_happy_path_init_through_accept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0915
    """Full happy path: init -> planner edit -> todo -> implement -> builder edit -> check -> accept."""
    # Step 1: create canon/ git repo with one initial commit on 'main'
    canon = tmp_path / "canon"
    canon.mkdir()
    _git(["init", str(canon)])
    _git(["config", "user.email", "test@example.com"], cwd=canon)
    _git(["config", "user.name", "Test"], cwd=canon)
    # Ensure we are on 'main'
    _git(["checkout", "-b", "main"], cwd=canon, check=False)
    (canon / "README.md").write_text("initial\n", encoding="utf-8")
    _git(["add", "."], cwd=canon)
    _git(["commit", "-m", "initial commit"], cwd=canon)

    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    # Patch out expensive / unavailable side-effects
    passing = [CheckResult(check_name="ok", status="pass", message="ok")]
    monkeypatch.setattr(wc, "run_validate", MagicMock(return_value=passing))
    monkeypatch.setattr(wc, "_run_pre_commit_in_sandbox", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())

    # The sandbox git wrapper rejects --no-verify; use a plain commit instead.
    def _git_commit_without_noverify(
        repo_path: Path,
        msg: str,
        *,
        dry_run: bool = False,
        no_verify: bool = False,  # noqa: ARG001 — preserves git_commit signature
    ) -> None:
        if not dry_run:
            _git(["commit", "-m", msg], cwd=repo_path)

    def _git_commit_if_changed_without_noverify(
        repo_path: Path,
        msg: str,
        *,
        dry_run: bool = False,
        no_verify: bool = False,  # noqa: ARG001 — preserves signature
    ) -> CommitResult:
        if dry_run:
            return CommitResult.COMMITTED
        proc = subprocess.run(  # noqa: S603
            [_GIT, "commit", "-m", msg],
            cwd=repo_path,
            check=False,
            capture_output=True,
        )
        if proc.returncode != 0 and b"nothing to commit" in (proc.stdout + proc.stderr):
            return CommitResult.NOTHING_TO_COMMIT
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
        return CommitResult.COMMITTED

    monkeypatch.setattr(wc, "git_commit", _git_commit_without_noverify)
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
    plan_dir.mkdir(parents=True)
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
    result = CliRunner().invoke(main, ["--config", str(config_path), "check", "005_foo"])
    assert result.exit_code == 0, result.output
    # check must have moved the plan to checking/
    assert (builder / "docs" / "plans" / "checking" / "005_foo.md").exists()
    assert not (builder / "docs" / "plans" / "implementing" / "005_foo.md").exists()

    # Step 8: reviewer fetches and checks out the feature branch
    reviewer = tmp_path / "reviewer"
    _git(["fetch", "origin"], cwd=reviewer)
    _git(["checkout", "-b", "feat/005_foo", "origin/feat/005_foo"], cwd=reviewer)

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
    canon = tmp_path / "canon"
    canon.mkdir()
    _git(["init", str(canon)])
    _git(["config", "user.email", "test@example.com"], cwd=canon)
    _git(["config", "user.name", "Test"], cwd=canon)
    _git(["checkout", "-b", "main"], cwd=canon, check=False)
    (canon / "README.md").write_text("initial\n", encoding="utf-8")
    _git(["add", "."], cwd=canon)
    _git(["commit", "-m", "initial commit"], cwd=canon)

    config_path = _make_integration_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    def _git_commit_if_changed_without_noverify(
        repo_path: Path,
        msg: str,
        *,
        dry_run: bool = False,
        no_verify: bool = False,  # noqa: ARG001
    ) -> CommitResult:
        if dry_run:
            return CommitResult.COMMITTED
        proc = subprocess.run(  # noqa: S603
            [_GIT, "commit", "-m", msg],
            cwd=repo_path,
            check=False,
            capture_output=True,
        )
        if proc.returncode != 0 and b"nothing to commit" in (proc.stdout + proc.stderr):
            return CommitResult.NOTHING_TO_COMMIT
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
        return CommitResult.COMMITTED

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
