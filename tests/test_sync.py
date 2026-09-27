"""Tests for the ``workflow sync`` command."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from workflow.cli import main
from workflow.metadata import CodeRepoMeta, ProjectMetadata, ProjectMode, write_metadata
from workflow.names import DOCS_REPO

_GIT = shutil.which("git") or "git"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([_GIT, *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603


def _contains(text: str, phrase: str) -> bool:
    """Whitespace-agnostic substring check (rich wraps long lines at the console width)."""

    def strip(s: str) -> str:
        return "".join(s.split())

    return strip(phrase) in strip(text)


def _init_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", branch], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "initial commit"], cwd=path)
    return path


def _make_code_repo(tmp_path: Path, name: str, *, branch: str = "main") -> tuple[Path, Path, Path]:
    """Create a wired-up code repo: (canon, bare, upstream). ``origin`` is a real bare remote."""
    seed = _init_repo(tmp_path / f"{name}-seed", branch=branch)
    upstream = tmp_path / f"{name}-upstream.git"
    _git(["init", "--bare", "-b", branch, str(upstream)], cwd=tmp_path)
    _git(["push", str(upstream), branch], cwd=seed)
    canon_repo = tmp_path / "canon" / name
    _git(["clone", str(upstream), str(canon_repo)], cwd=tmp_path)
    bare_repo = tmp_path / "bare" / name
    _git(["clone", "--bare", str(canon_repo), str(bare_repo)], cwd=tmp_path)
    _git(["remote", "add", "bare", str(bare_repo)], cwd=canon_repo)
    return canon_repo, bare_repo, upstream


def _advance_upstream(upstream: Path, tmp_path: Path, name: str, *, branch: str = "main") -> str:
    pusher = tmp_path / f"{name}-pusher"
    _git(["clone", str(upstream), str(pusher)], cwd=tmp_path)
    (pusher / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "."], cwd=pusher)
    _git(["commit", "-m", "advance upstream"], cwd=pusher)
    _git(["push"], cwd=pusher)
    return _git(["rev-parse", branch], cwd=pusher).stdout.decode().strip()


def _make_project(tmp_path: Path, repos: list[str]) -> None:
    """Minimal project scaffold for the ``sync`` CLI command: env.list, config.yaml, .workflow.json."""
    (tmp_path / "env.list").touch()
    shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    script = tmp_path / "run_agent_in_docker.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o755)
    _init_repo(tmp_path / "canon" / DOCS_REPO)
    write_metadata(tmp_path, ProjectMetadata(mode=ProjectMode.oob, code_repos=[CodeRepoMeta(name=r) for r in repos]))


def _run_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra_args: str) -> Result:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"
    return CliRunner().invoke(main, ["--config", str(config_path), "sync", *extra_args])


def test_sync_advances_bare_to_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path, ["mypackage"])
    canon_repo, bare_repo, upstream = _make_code_repo(tmp_path, "mypackage")
    new_sha = _advance_upstream(upstream, tmp_path, "mypackage")
    del canon_repo

    result = _run_sync(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "refs/heads/main"], cwd=bare_repo).stdout.decode().strip() == new_sha
    assert new_sha in result.output


def test_sync_skips_repo_with_only_bare_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A code repo whose canon clone has no ``origin`` (only ``bare``) is skipped, not failed."""
    _make_project(tmp_path, ["mypackage"])
    canon_repo = tmp_path / "canon" / "mypackage"
    _init_repo(canon_repo)
    bare_repo = tmp_path / "bare" / "mypackage"
    _git(["clone", "--bare", str(canon_repo), str(bare_repo)], cwd=tmp_path)
    _git(["remote", "add", "bare", str(bare_repo)], cwd=canon_repo)

    result = _run_sync(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert "no upstream remote" in result.output


def test_sync_branch_option_pushes_extra_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path, ["mypackage"])
    canon_repo, bare_repo, upstream = _make_code_repo(tmp_path, "mypackage")
    _git(["checkout", "-b", "feat/extra"], cwd=canon_repo)
    (canon_repo / "extra.txt").write_text("extra\n", encoding="utf-8")
    _git(["add", "."], cwd=canon_repo)
    _git(["commit", "-m", "extra branch commit"], cwd=canon_repo)
    _git(["push", "-u", "origin", "feat/extra"], cwd=canon_repo)
    _git(["checkout", "main"], cwd=canon_repo)
    del upstream

    result = _run_sync(tmp_path, monkeypatch, "--branch", "feat/extra")

    assert result.exit_code == 0, result.output
    extra_sha = _git(["rev-parse", "refs/heads/feat/extra"], cwd=canon_repo).stdout.decode().strip()
    assert _git(["rev-parse", "refs/heads/feat/extra"], cwd=bare_repo).stdout.decode().strip() == extra_sha


def test_sync_fails_when_default_branch_unresolvable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path, ["mypackage"])
    canon_repo, _bare_repo, _upstream = _make_code_repo(tmp_path, "mypackage", branch="trunk")
    _git(["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=canon_repo)

    result = _run_sync(tmp_path, monkeypatch)

    assert result.exit_code != 0
    assert "cannot resolve upstream default branch" in result.output


def test_sync_dry_run_mutates_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path, ["mypackage"])
    canon_repo, bare_repo, upstream = _make_code_repo(tmp_path, "mypackage")
    _advance_upstream(upstream, tmp_path, "mypackage")
    before = _git(["rev-parse", "refs/heads/main"], cwd=bare_repo).stdout.decode().strip()
    del canon_repo

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "config.yaml"), "--dry-run", "sync"],
    )

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "refs/heads/main"], cwd=bare_repo).stdout.decode().strip() == before


def test_sync_refuses_non_fast_forward_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``bare`` holds a commit origin does not: refuse rather than silently discard it."""
    _make_project(tmp_path, ["mypackage"])
    canon_repo, bare_repo, upstream = _make_code_repo(tmp_path, "mypackage")
    # Simulate an agent's push straight to bare (bypassing sync), diverging from origin.
    agent_clone = tmp_path / "agent-clone"
    _git(["clone", "-o", "bare", str(bare_repo), str(agent_clone)], cwd=tmp_path)
    (agent_clone / "agent.txt").write_text("agent\n", encoding="utf-8")
    _git(["add", "."], cwd=agent_clone)
    _git(["commit", "-m", "agent work directly on bare main"], cwd=agent_clone)
    _git(["push", "bare", "main"], cwd=agent_clone)
    bare_sha_before = _git(["rev-parse", "refs/heads/main"], cwd=bare_repo).stdout.decode().strip()
    _advance_upstream(upstream, tmp_path, "mypackage")
    del canon_repo

    result = _run_sync(tmp_path, monkeypatch)

    assert result.exit_code != 0
    assert _contains(result.output, "is not an ancestor")
    assert _git(["rev-parse", "refs/heads/main"], cwd=bare_repo).stdout.decode().strip() == bare_sha_before


def test_sync_force_overwrites_non_fast_forward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path, ["mypackage"])
    canon_repo, bare_repo, upstream = _make_code_repo(tmp_path, "mypackage")
    agent_clone = tmp_path / "agent-clone"
    _git(["clone", "-o", "bare", str(bare_repo), str(agent_clone)], cwd=tmp_path)
    (agent_clone / "agent.txt").write_text("agent\n", encoding="utf-8")
    _git(["add", "."], cwd=agent_clone)
    _git(["commit", "-m", "agent work directly on bare main"], cwd=agent_clone)
    _git(["push", "bare", "main"], cwd=agent_clone)
    new_sha = _advance_upstream(upstream, tmp_path, "mypackage")
    del canon_repo

    result = _run_sync(tmp_path, monkeypatch, "--force")

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "refs/heads/main"], cwd=bare_repo).stdout.decode().strip() == new_sha


def test_sync_restricts_to_named_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path, ["mypackage", "dep1"])
    _canon1, bare1, upstream1 = _make_code_repo(tmp_path, "mypackage")
    _canon2, bare2, upstream2 = _make_code_repo(tmp_path, "dep1")
    sha1 = _advance_upstream(upstream1, tmp_path, "mypackage")
    _advance_upstream(upstream2, tmp_path, "dep1")
    before2 = _git(["rev-parse", "refs/heads/main"], cwd=bare2).stdout.decode().strip()

    result = _run_sync(tmp_path, monkeypatch, "--repo", "mypackage")

    assert result.exit_code == 0, result.output
    assert _git(["rev-parse", "refs/heads/main"], cwd=bare1).stdout.decode().strip() == sha1
    assert _git(["rev-parse", "refs/heads/main"], cwd=bare2).stdout.decode().strip() == before2


def test_sync_unknown_repo_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path, ["mypackage"])
    _make_code_repo(tmp_path, "mypackage")

    result = _run_sync(tmp_path, monkeypatch, "--repo", "nosuchrepo")

    assert result.exit_code != 0
    assert "unknown repo" in result.output
