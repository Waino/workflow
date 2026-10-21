"""Integration tests for out-of-band ``init``/``add-repo``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import workflow.commands.workflow as wc
from tests.conftest import commit_in_band_init_files
from workflow.cli import main
from workflow.commands.workflow import IN_BAND_BOOTSTRAP_PATH
from workflow.git_helpers import CommitResult
from workflow.metadata import METADATA_FILENAME, ProjectMode, read_metadata
from workflow.names import DOCS_REPO, WorktreeName

_GIT = shutil.which("git") or "git"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _contains(text: str, phrase: str) -> bool:
    """Whitespace-agnostic substring check.

    Rich wraps long lines at the console width, sometimes at a space (which the
    newline then replaces) and sometimes mid-word (an overlong, unbreakable token,
    where no character is consumed). Stripping all whitespace from both sides makes
    the check correct either way, at the cost of also ignoring genuine spaces in
    *phrase* -- fine for the short, unambiguous phrases these tests look for.
    """

    def strip(s: str) -> str:
        return "".join(s.split())

    return strip(phrase) in strip(text)


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
        if name == HOOK_FILENAME:
            target.chmod(0o755)
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "initial commit"], cwd=path)
    return path


def _add_upstream(repo: Path, upstream_root: Path) -> None:
    """Give *repo* an ``origin`` upstream (a bare repo under *upstream_root*) that ``main`` tracks."""
    upstream = upstream_root / f"{repo.name}.git"
    _git(["init", "--bare", "-b", "main", str(upstream)], cwd=upstream_root.parent)
    _git(["remote", "add", "origin", str(upstream)], cwd=repo)
    _git(["push", "-u", "origin", "main"], cwd=repo)


def _upstream_of_main(repo: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "main@{upstream}"], cwd=repo).stdout.decode().strip()


def _remotes(repo: Path) -> list[str]:
    return _git(["remote"], cwd=repo).stdout.decode().split()


def _hook_symlink(tmp_path: Path, worktree: str, repo: str) -> Path:
    return tmp_path / worktree / repo / ".git" / "hooks" / "pre-commit"


def _add_repo(tmp_path: Path, *args: str) -> Any:
    return CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "add-repo", *args])


HOOK_FILENAME = ".git-pre-commit-hook"
_AGENT_WORKTREES = ("planner", "builder", "reviewer")


def _make_oob_project(
    tmp_path: Path,
    code_repos: list[str],
    *,
    hook_repos: tuple[str, ...] = (),
    docs_hook: bool = False,
) -> Path:
    """Build project_dir/{env.list,config.yaml,run_agent_in_docker.sh,canon/{docs,*repos}}."""
    (tmp_path / "env.list").touch()
    shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    script = tmp_path / "run_agent_in_docker.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o755)

    docs_repo = _init_repo(
        tmp_path / "canon" / DOCS_REPO,
        extra_files={
            "body_of_agents.md": "# Hand-written body\n",
            **({HOOK_FILENAME: "#!/bin/bash\n"} if docs_hook else {}),
        },
    )
    bootstrap = docs_repo / "bootstrap_venv.sh"
    bootstrap.write_text("#!/bin/bash\necho bootstrap\n", encoding="utf-8")
    bootstrap.chmod(0o755)
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "add bootstrap script"], cwd=docs_repo)

    for name in code_repos:
        hook = {HOOK_FILENAME: "#!/bin/bash\n"} if name in hook_repos else None
        _init_repo(tmp_path / "canon" / name, extra_files=hook)

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
    commit_in_band_init_files(canon)
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
                if worktree != "canon":
                    assert _remotes(repo_path) == ["bare"]

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
        _make_oob_project(tmp_path, ["mypackage", "dep1"], hook_repos=("mypackage", "dep1"))

        result = _run_init(tmp_path, monkeypatch, "--skip-hook", "mypackage")

        assert result.exit_code == 0, result.output
        run_hooks = {repo.name: repo.run_hook for repo in read_metadata(tmp_path).code_repos}
        assert run_hooks == {"mypackage": False, "dep1": True}
        for worktree in _AGENT_WORKTREES:
            assert not _hook_symlink(tmp_path, worktree, "mypackage").is_symlink()
            assert _hook_symlink(tmp_path, worktree, "dep1").is_symlink()

        rerun = _run_init(tmp_path, monkeypatch, "--continue")
        assert rerun.exit_code == 0, rerun.output
        assert not _hook_symlink(tmp_path, "builder", "mypackage").is_symlink()

    def test_code_repo_with_hook_file_gets_symlink(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"], hook_repos=("mypackage",))
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        for worktree in _AGENT_WORKTREES:
            hook = _hook_symlink(tmp_path, worktree, "mypackage")
            assert hook.is_symlink()
            assert str(hook.readlink()) == "../../.git-pre-commit-hook"

    def test_skip_hook_naming_no_detected_repo_is_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        result = _run_init(tmp_path, monkeypatch, "--skip-hook", "mypackge")
        assert result.exit_code == 1
        assert _contains(result.output, "--skip-hook names no detected code repo: ['mypackge']")
        assert not (tmp_path / METADATA_FILENAME).exists()

    def test_skip_hook_cannot_change_on_rerun(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage", "dep1"])
        first = _run_init(tmp_path, monkeypatch, "--skip-hook", "mypackage")
        assert first.exit_code == 0, first.output

        changed = _run_init(tmp_path, monkeypatch, "--skip-hook", "dep1", "--continue")
        assert changed.exit_code == 1
        assert _contains(changed.output, "--skip-hook cannot be changed after init")

        same = _run_init(tmp_path, monkeypatch, "--skip-hook", "mypackage", "--continue")
        assert same.exit_code == 0, same.output

    def test_canon_code_repo_keeps_its_upstream(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        canon_repo = tmp_path / "canon" / "mypackage"
        (tmp_path / "upstream").mkdir()
        _add_upstream(canon_repo, tmp_path / "upstream")

        result = _run_init(tmp_path, monkeypatch)

        assert result.exit_code == 0, result.output
        assert _upstream_of_main(canon_repo) == "origin/main"
        for key in ("push.default", "push.autoSetupRemote"):
            assert _git(["config", "--local", "--get", key], cwd=canon_repo, check=False).returncode == 1
        assert (
            _git(["rev-parse", "main"], cwd=tmp_path / "bare" / "mypackage").stdout
            == _git(
                ["rev-parse", "main"],
                cwd=canon_repo,
            ).stdout
        )
        # The docs repo is the workflow's own: canon tracks bare there.
        assert _upstream_of_main(tmp_path / "canon" / DOCS_REPO) == "bare/main"

    def test_invalid_code_repo_dir_name_fails_cleanly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        _init_repo(tmp_path / "canon" / "bad name")
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert _contains(result.output, "invalid code repo name 'bad name'")

    def test_docs_repo_not_on_main_fails_precondition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        _git(["branch", "-m", "main", "master"], cwd=tmp_path / "canon" / DOCS_REPO)
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert _contains(result.output, "must have 'main' checked out")
        assert not (tmp_path / "bare").exists()

    def test_uncommitted_bootstrap_script_fails_precondition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        docs_repo = tmp_path / "canon" / DOCS_REPO
        _git(["rm", "--cached", "bootstrap_venv.sh"], cwd=docs_repo)
        _git(["commit", "-m", "untrack bootstrap"], cwd=docs_repo)
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert _contains(result.output, "must be committed as executable")

    def test_bootstrap_script_committed_non_executable_fails_precondition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        docs_repo = tmp_path / "canon" / DOCS_REPO
        _git(["update-index", "--chmod=-x", "bootstrap_venv.sh"], cwd=docs_repo)
        _git(["commit", "-m", "drop exec bit"], cwd=docs_repo)
        assert (docs_repo / "bootstrap_venv.sh").stat().st_mode & 0o111  # still executable on disk
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert _contains(result.output, "must be committed as executable")

    def test_dry_run_mutates_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        docs_repo = tmp_path / "canon" / DOCS_REPO
        head_before = _git(["rev-parse", "HEAD"], cwd=docs_repo).stdout
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["--dry-run", "--config", str(tmp_path / "config.yaml"), "init", "--oob"])

        assert result.exit_code == 0, result.output
        assert _contains(result.output, "Detected code repos: mypackage")
        assert _contains(result.output, "git clone --bare canon/mypackage bare/mypackage")
        assert not (tmp_path / "bare").exists()
        assert not any((tmp_path / worktree).exists() for worktree in _AGENT_WORKTREES)
        assert not (tmp_path / METADATA_FILENAME).exists()
        assert not (tmp_path / "canon" / "AGENTS.md").exists()
        assert _git(["rev-parse", "HEAD"], cwd=docs_repo).stdout == head_before
        assert _git(["status", "--porcelain"], cwd=docs_repo).stdout == b""
        assert _remotes(docs_repo) == []

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
        assert _contains(result.output, "no code repos found under")
        assert _contains(result.output, "out of scope")

    def test_missing_body_of_agents_fails_precondition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        (tmp_path / "canon" / DOCS_REPO / "body_of_agents.md").unlink()
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert _contains(result.output, "body_of_agents.md")

    def test_missing_bootstrap_script_fails_precondition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        (tmp_path / "canon" / DOCS_REPO / "bootstrap_venv.sh").unlink()
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert _contains(result.output, "bootstrap_venv.sh")

    def test_non_executable_bootstrap_script_fails_precondition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        (tmp_path / "canon" / DOCS_REPO / "bootstrap_venv.sh").chmod(0o644)
        result = _run_init(tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert _contains(result.output, "must be executable")

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
        assert _contains(result.output, "initialised in in_band mode")

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
        assert _contains(result.output, "initialised in oob mode")

    def test_skip_hook_without_oob_is_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "env.list").touch()
        shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "init", "--skip-hook", "x"])
        assert result.exit_code == 1
        assert _contains(result.output, "--skip-hook is only valid with --oob")


class TestInitInBand:
    def test_wires_a_single_bare_remote_everywhere(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_in_band_project(tmp_path)
        result = _run_in_band_init(tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        for worktree in WorktreeName:
            repo_path = tmp_path / worktree.value
            assert _remotes(repo_path) == ["bare"], worktree
            assert _git(["remote", "get-url", "bare"], cwd=repo_path).stdout.decode().strip() == str(tmp_path / "bare")
        assert _upstream_of_main(tmp_path / "canon") == "bare/main"

    @pytest.mark.parametrize(
        ("untrack", "message"),
        [
            pytest.param(["pyproject.toml"], "pyproject.toml", id="neither_pyproject_nor_bootstrap"),
            pytest.param([".git-pre-commit-hook"], ".git-pre-commit-hook must be committed", id="uncommitted_hook"),
        ],
    )
    def test_uncommitted_required_file_fails_precondition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        untrack: list[str],
        message: str,
    ) -> None:
        _make_in_band_project(tmp_path)
        _git(["rm", "--cached", *untrack], cwd=tmp_path / "canon")
        _git(["commit", "-m", "untrack"], cwd=tmp_path / "canon")

        result = _run_in_band_init(tmp_path, monkeypatch)

        assert result.exit_code == 1
        assert _contains(result.output, message)
        assert not (tmp_path / "bare").exists()

    def test_all_problems_are_listed_in_one_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_in_band_project(tmp_path)
        (tmp_path / "env.list").unlink()
        _git(["rm", "--cached", ".git-pre-commit-hook"], cwd=tmp_path / "canon")
        _git(["commit", "-m", "untrack"], cwd=tmp_path / "canon")
        (tmp_path / "bare").mkdir()

        result = _run_in_band_init(tmp_path, monkeypatch)

        assert result.exit_code == 1
        for phrase in ("env.list must exist", ".git-pre-commit-hook must be committed", "not a valid bare clone"):
            assert _contains(result.output, phrase)

    def test_missing_git_dir_suppresses_dependent_messages(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_in_band_project(tmp_path)
        (tmp_path / "env.list").unlink()
        shutil.rmtree(tmp_path / "canon" / ".git")

        result = _run_in_band_init(tmp_path, monkeypatch)

        assert result.exit_code == 1
        assert _contains(result.output, "not a git repository")
        assert _contains(result.output, "env.list must exist")
        for phrase in ("at least one commit", "pyproject.toml", ".git-pre-commit-hook"):
            assert not _contains(result.output, phrase)

    def test_repo_without_commit_fails_precondition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_in_band_project(tmp_path)
        shutil.rmtree(tmp_path / "canon" / ".git")
        _git(["init", "-b", "main"], cwd=tmp_path / "canon")

        result = _run_in_band_init(tmp_path, monkeypatch)

        assert result.exit_code == 1
        assert _contains(result.output, "must have at least one commit")

    @pytest.mark.parametrize(
        ("mode", "ok"),
        [pytest.param(0o755, True, id="executable"), pytest.param(0o644, False, id="not_executable")],
    )
    def test_bootstrap_script_replaces_pyproject(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mode: int,
        *,
        ok: bool,
    ) -> None:
        _make_in_band_project(tmp_path)
        canon = tmp_path / "canon"
        script = canon / IN_BAND_BOOTSTRAP_PATH
        script.parent.mkdir()
        script.write_text("#!/bin/bash\n", encoding="utf-8")
        script.chmod(mode)
        _git(["rm", "pyproject.toml"], cwd=canon)
        _git(["add", IN_BAND_BOOTSTRAP_PATH], cwd=canon)
        _git(["commit", "-m", "bootstrap instead of pyproject"], cwd=canon)

        result = _run_in_band_init(tmp_path, monkeypatch)

        assert (result.exit_code == 0) is ok, result.output
        assert ok or _contains(result.output, "must be executable")


class TestInitOobPreflight:
    def test_all_problems_are_listed_in_one_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        (tmp_path / "env.list").unlink()
        (tmp_path / "canon" / DOCS_REPO / "body_of_agents.md").unlink()
        shutil.rmtree(tmp_path / "canon" / "mypackage" / ".git")
        _git(["init", "-b", "main"], cwd=tmp_path / "canon" / "mypackage")
        (tmp_path / "bare" / DOCS_REPO).mkdir(parents=True)

        result = _run_init(tmp_path, monkeypatch)

        assert result.exit_code == 1
        for phrase in (
            "env.list must exist",
            "body_of_agents.md must exist",
            "mypackage must have at least one commit",
        ):
            assert _contains(result.output, phrase)
        assert _contains(result.output, "not a valid bare clone")


class TestAddRepo:
    def test_happy_path_registers_clones_and_updates_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        canon_repo = _init_repo(tmp_path / "canon" / "dep2", extra_files={".git-pre-commit-hook": "#!/bin/bash\n"})
        (tmp_path / "upstream").mkdir()
        _add_upstream(canon_repo, tmp_path / "upstream")

        result = _add_repo(tmp_path, "dep2")

        assert result.exit_code == 0, result.output
        assert _contains(result.output, "add it to docs/bootstrap_venv.sh")
        metadata = read_metadata(tmp_path)
        assert {repo.name: repo.run_hook for repo in metadata.code_repos} == {"mypackage": True, "dep2": True}
        bare_repo = tmp_path / "bare" / "dep2"
        assert _git(["rev-parse", "--is-bare-repository"], cwd=bare_repo).stdout.strip() == b"true"
        assert _git(["remote", "get-url", "bare"], cwd=canon_repo).stdout.decode().strip() == str(bare_repo)
        assert _upstream_of_main(canon_repo) == "origin/main"
        for worktree in _AGENT_WORKTREES:
            repo_path = tmp_path / worktree / "dep2"
            assert _remotes(repo_path) == ["bare"]
            assert _hook_symlink(tmp_path, worktree, "dep2").is_symlink()
        # AGENTS.md lists the code repos agents must cover in `branches` frontmatter.
        for worktree in ("canon", *_AGENT_WORKTREES):
            assert "dep2" in (tmp_path / worktree / "AGENTS.md").read_text(encoding="utf-8")
        check = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "agents_md", "--check"])
        assert check.exit_code == 0, check.output

    def test_skip_hook_is_recorded_and_no_symlink_installed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        _init_repo(tmp_path / "canon" / "dep2", extra_files={".git-pre-commit-hook": "#!/bin/bash\n"})

        result = _add_repo(tmp_path, "dep2", "--skip-hook")

        assert result.exit_code == 0, result.output
        run_hooks = {repo.name: repo.run_hook for repo in read_metadata(tmp_path).code_repos}
        assert run_hooks["dep2"] is False
        for worktree in _AGENT_WORKTREES:
            assert (tmp_path / worktree / "dep2" / ".git").exists()
            assert not _hook_symlink(tmp_path, worktree, "dep2").is_symlink()

    @pytest.mark.parametrize("name", ["sub/nested", "../outside", "-x", ".hidden"])
    def test_rejected_for_invalid_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        _init_repo(tmp_path / "canon" / "sub" / "nested")
        bare_before = sorted(path.name for path in (tmp_path / "bare").iterdir())

        result = _add_repo(tmp_path, "--", name)

        assert result.exit_code == 1
        assert _contains(result.output, f"invalid code repo name {name!r}")
        assert sorted(path.name for path in (tmp_path / "bare").iterdir()) == bare_before
        assert {repo.name for repo in read_metadata(tmp_path).code_repos} == {"mypackage"}

    def test_dry_run_mutates_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        _init_repo(tmp_path / "canon" / "dep2")
        metadata_before = (tmp_path / METADATA_FILENAME).read_text(encoding="utf-8")

        result = CliRunner().invoke(main, ["--dry-run", "--config", str(tmp_path / "config.yaml"), "add-repo", "dep2"])

        assert result.exit_code == 0, result.output
        assert _contains(result.output, "git clone --bare canon/dep2 bare/dep2")
        assert not (tmp_path / "bare" / "dep2").exists()
        assert not any((tmp_path / worktree / "dep2").exists() for worktree in _AGENT_WORKTREES)
        assert (tmp_path / METADATA_FILENAME).read_text(encoding="utf-8") == metadata_before
        assert _remotes(tmp_path / "canon" / "dep2") == []

    def test_rejected_in_in_band_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_in_band_project(tmp_path)
        first = _run_in_band_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = _add_repo(tmp_path, "dep2")
        assert result.exit_code == 1
        assert _contains(result.output, "only valid for out-of-band projects")

    def test_rejected_for_duplicate_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = _add_repo(tmp_path, "mypackage")
        assert result.exit_code == 1
        assert _contains(result.output, "already registered")

    def test_rejected_for_docs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = _add_repo(tmp_path, "docs")
        assert result.exit_code == 1
        assert _contains(result.output, "reserved for the docs repo")

    def test_problems_are_collected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output
        (tmp_path / "bare" / "dep2").mkdir()

        result = _add_repo(tmp_path, "dep2")

        assert result.exit_code == 1
        assert _contains(result.output, "not a git repository")
        assert _contains(result.output, "not a valid bare clone")

    def test_rejected_when_canon_repo_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_oob_project(tmp_path, ["mypackage"])
        first = _run_init(tmp_path, monkeypatch)
        assert first.exit_code == 0, first.output

        result = _add_repo(tmp_path, "dep2")
        assert result.exit_code == 1
        assert _contains(result.output, "not a git repository")
