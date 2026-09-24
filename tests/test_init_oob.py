"""Integration tests for out-of-band ``init``/``add-repo``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import workflow.commands.workflow as wc
from workflow.cli import main
from workflow.git_helpers import CommitResult
from workflow.metadata import METADATA_FILENAME, ProjectMode, read_metadata
from workflow.names import DOCS_REPO, WorktreeName

_GIT = shutil.which("git") or "git"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _unwrapped(text: str) -> str:
    """Undo rich's line-wrapping (which can split mid-word) for substring checks."""
    return text.replace("\n", "")


def _git(args: list[str], *, cwd: Path, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([_GIT, *args], cwd=cwd, check=check, capture_output=True, **kwargs)  # noqa: S603


def _git_commit_if_changed_without_noverify(
    repo_path: Path,
    msg: str,
    *,
    dry_run: bool = False,
    no_verify: bool = False,  # noqa: ARG001 — preserves signature; sandbox git rejects --no-verify
) -> CommitResult:
    if dry_run:
        return CommitResult.COMMITTED
    proc = subprocess.run([_GIT, "commit", "-m", msg], cwd=repo_path, check=False, capture_output=True)  # noqa: S603
    output = proc.stdout + proc.stderr
    if proc.returncode != 0 and (b"nothing to commit" in output or b"nothing added to commit" in output):
        return CommitResult.NOTHING_TO_COMMIT
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
    return CommitResult.COMMITTED


def _init_repo(path: Path, *, extra_files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    for name, content in (extra_files or {}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "initial commit"], cwd=path)
    return path


def _make_oob_project(tmp_path: Path, code_repos: list[str]) -> Path:
    """Build project_dir/{env.list,config.yaml,run_agent_in_docker.sh,canon/{docs,*repos}}."""
    (tmp_path / "env.list").touch()
    shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    script = tmp_path / "run_agent_in_docker.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o755)

    docs_repo = _init_repo(
        tmp_path / "canon" / DOCS_REPO,
        extra_files={"body_of_agents.md": "# Hand-written body\n"},
    )
    bootstrap = docs_repo / "bootstrap_venv.sh"
    bootstrap.write_text("#!/bin/bash\necho bootstrap\n", encoding="utf-8")
    bootstrap.chmod(0o755)
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "add bootstrap script"], cwd=docs_repo)

    for name in code_repos:
        _init_repo(tmp_path / "canon" / name)

    return tmp_path / "config.yaml"


def _run_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra_args: str) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)
    config_path = tmp_path / "config.yaml"
    return CliRunner().invoke(main, ["--config", str(config_path), "init", "--oob", *extra_args])


def _make_in_band_project(tmp_path: Path) -> Path:
    """Build a minimal real-git in-band project, return its config.yaml path."""
    (tmp_path / "env.list").touch()
    shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    script = tmp_path / "run_agent_in_docker.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o755)
    canon = _init_repo(tmp_path / "canon")
    (canon / ".git-pre-commit-hook").touch()
    return tmp_path / "config.yaml"


def _run_in_band_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_commit_if_changed", _git_commit_if_changed_without_noverify)
    return CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "init"])


class TestInitOob:
    def test_creates_bare_and_agent_clones_for_every_repo(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage", "dep1"])

        result = _run_init(tmp_path, monkeypatch)

        assert result.exit_code == 0, result.output
        for repo in (DOCS_REPO, "mypackage", "dep1"):
            bare_repo = tmp_path / "bare" / repo
            assert _git(["rev-parse", "--is-bare-repository"], cwd=bare_repo).stdout.strip() == b"true"
            for worktree in ("canon", "planner", "builder", "reviewer"):
                repo_path = tmp_path / worktree / repo
                assert (repo_path / ".git").exists(), f"{repo_path} missing"
                remote_url = _git(["remote", "get-url", "bare"], cwd=repo_path).stdout.decode().strip()
                assert remote_url == str(bare_repo)

        metadata = read_metadata(tmp_path)
        assert metadata.mode == ProjectMode.oob
        assert {repo.name for repo in metadata.code_repos} == {"mypackage", "dep1"}

        for worktree in WorktreeName:
            agents_md = tmp_path / worktree.value / "AGENTS.md"
            assert agents_md.exists()
            assert "# Hand-written body" in agents_md.read_text(encoding="utf-8")
        hashes = {(tmp_path / w.value / "AGENTS.md").read_text(encoding="utf-8") for w in WorktreeName}
        assert len(hashes) == 1
        assert (tmp_path / ".agents_md_hash").exists()

    def test_rerun_is_resumable_and_mutates_nothing_further(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        docs_head_before = _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon" / DOCS_REPO).stdout
        agents_md_before = (tmp_path / "builder" / "AGENTS.md").read_text(encoding="utf-8")

        second = _run_init(tmp_path, monkeypatch, "--continue")
        assert second.exit_code == 0, second.output
        assert "[skip]" in second.output
        assert _git(["rev-parse", "HEAD"], cwd=tmp_path / "canon" / DOCS_REPO).stdout == docs_head_before
        assert (tmp_path / "builder" / "AGENTS.md").read_text(encoding="utf-8") == agents_md_before

    def test_skip_hook_is_recorded_and_no_symlink_installed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        # mypackage carries no .git-pre-commit-hook: hook install must be silently skipped.
        result = _run_init(tmp_path, monkeypatch, "--skip-hook", "mypackage")

        assert result.exit_code == 0, result.output
        metadata = read_metadata(tmp_path)
        (repo,) = metadata.code_repos
        assert repo.run_hook is False
        hook = tmp_path / "builder" / "mypackage" / ".git" / "hooks" / "pre-commit"
        assert not hook.exists()

    def test_code_repo_without_hook_file_does_not_fail_init(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        hook = tmp_path / "builder" / "mypackage" / ".git" / "hooks" / "pre-commit"
        assert not hook.exists()

    def test_no_code_repos_fails_precondition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, [])
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "no code repos found under" in _unwrapped(result.output)
        assert "out of scope" in _unwrapped(result.output)

    def test_missing_body_of_agents_fails_precondition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        (tmp_path / "canon" / DOCS_REPO / "body_of_agents.md").unlink()
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "body_of_agents.md" in _unwrapped(result.output)

    def test_missing_bootstrap_script_fails_precondition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        (tmp_path / "canon" / DOCS_REPO / "bootstrap_venv.sh").unlink()
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "bootstrap_venv.sh" in _unwrapped(result.output)

    def test_non_executable_bootstrap_script_fails_precondition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        (tmp_path / "canon" / DOCS_REPO / "bootstrap_venv.sh").chmod(0o644)
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "must be executable" in result.output

    def test_oob_on_in_band_project_fails_with_immutability_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_in_band_project(tmp_path)
        first = _run_in_band_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "init", "--oob"])
        assert result.exit_code == 1
        assert "initialised in in_band mode" in _unwrapped(result.output)

    def test_in_band_on_oob_project_fails_with_immutability_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "init"])
        assert result.exit_code == 1
        assert "initialised in oob mode" in result.output

    def test_skip_hook_without_oob_is_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "env.list").touch()
        shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "init", "--skip-hook", "x"])
        assert result.exit_code == 1
        assert "--skip-hook is only valid with --oob" in result.output


class TestAddRepo:
    def test_happy_path_registers_clones_and_updates_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        _init_repo(tmp_path / "canon" / "dep2")

        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "add-repo", "dep2"])

        assert result.exit_code == 0, result.output
        metadata = read_metadata(tmp_path)
        assert {repo.name for repo in metadata.code_repos} == {"mypackage", "dep2"}
        bare_repo = tmp_path / "bare" / "dep2"
        assert _git(["rev-parse", "--is-bare-repository"], cwd=bare_repo).stdout.strip() == b"true"
        for worktree in ("planner", "builder", "reviewer"):
            repo_path = tmp_path / worktree / "dep2"
            assert (repo_path / ".git").exists()

    def test_rejected_in_in_band_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_in_band_project(tmp_path)
        first = _run_in_band_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "add-repo", "dep2"])
        assert result.exit_code == 1
        assert "only valid for out-of-band projects" in _unwrapped(result.output)

    def test_rejected_for_duplicate_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "add-repo", "mypackage"])
        assert result.exit_code == 1
        assert "already registered" in result.output

    def test_rejected_for_docs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "add-repo", "docs"])
        assert result.exit_code == 1
        assert "reserved for the docs repo" in result.output

    def test_rejected_when_canon_repo_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "add-repo", "dep2"])
        assert result.exit_code == 1
        assert "not a git repository" in result.output

    def test_metadata_file_is_valid_metadata_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: METADATA_FILENAME round-trips through read_metadata after add-repo."""
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        _init_repo(tmp_path / "canon" / "dep2")

        CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "add-repo", "dep2"])

        assert (tmp_path / METADATA_FILENAME).exists()
        read_metadata(tmp_path)  # does not raise
