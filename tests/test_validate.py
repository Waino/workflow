"""Tests for workflow.validate."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path, PurePosixPath

import pytest

import workflow.validate as dc
from workflow.config import AgentConfig, StateConfig, WorkflowConfig
from workflow.exceptions import WorkflowError
from workflow.metadata import ProjectMetadata
from workflow.models import Plan, PlanState


def _config(project_dir: Path) -> WorkflowConfig:
    return WorkflowConfig(
        project_dir=project_dir,
        project_name="workflow",
        metadata=ProjectMetadata(),
        agents=[
            AgentConfig(name="planner", model="test"),
            AgentConfig(name="builder", model="test"),
            AgentConfig(name="reviewer", model="test"),
        ],
        states={
            PlanState.INTENT: StateConfig(description="Plans being explored.", agent="planner"),
            PlanState.PLANNING: StateConfig(description="Plans being drafted.", agent="planner"),
            PlanState.TODO: StateConfig(description="Plans approved for implementation.", agent=None),
            PlanState.IMPLEMENTING: StateConfig(description="Plans under active implementation.", agent="builder"),
            PlanState.CHECKING: StateConfig(
                description="Implementation complete, review in progress.",
                agent="reviewer",
            ),
            PlanState.DONE: StateConfig(description="Merged to main.", agent=None),
            PlanState.REJECTED: StateConfig(description="Will not be implemented.", agent=None),
            PlanState.REVIEW: StateConfig(description="Codebase-wide review tasks.", agent="reviewer"),
        },
    )


def _plan(state: PlanState, repo: str) -> Plan:
    return Plan(sequence_number=[5], slug="foo", state=state, path=Path(f"{repo}/docs/plans/{state.value}/005_foo.md"))


def test_infer_target_repo_uses_state_agent_or_canon(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert dc.infer_target_worktree(_plan(PlanState.PLANNING, "planner"), config) == "planner"
    assert dc.infer_target_worktree(_plan(PlanState.CHECKING, "reviewer"), config) == "reviewer"
    assert dc.infer_target_worktree(_plan(PlanState.TODO, "canon"), config) == "canon"
    assert dc.infer_target_worktree(_plan(PlanState.CHECKING, "reviewer"), config, "builder") == "builder"


@pytest.mark.parametrize(
    ("target_repo", "changed_files", "status"),
    [
        ("planner", ["docs/plans/planning/005_foo.md"], "pass"),
        ("planner", ["workflow/cli.py"], "fail"),
        ("builder", ["docs/plans/intent/001_idea.md"], "fail"),
        ("reviewer", ["docs/plans/intent/001_idea.md"], "fail"),
        ("canon", ["workflow/cli.py"], "pass"),
    ],
)
def test_check_file_scope_enforces_repo_rules(target_repo: str, changed_files: list[str], status: str) -> None:
    result = dc._check_file_scope(changed_files, target_repo, PurePosixPath("docs/plans"))
    assert result.status == status


def test_check_noqa_nosec_reports_only_added_comments() -> None:
    diff_text = """ context
+x = 1  # noqa: F401
-y = 2  # nosec
+z = 3  # nosec B101"""
    result = dc._check_noqa_nosec(diff_text)
    assert result.status == "warn"
    assert result.details == ["x = 1  # noqa: F401", "z = 3  # nosec B101"]


def test_check_git_hooks_passes_for_managed_symlink(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo_path = tmp_path / "planner"
    hooks_dir = repo_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    source = repo_path / ".git-pre-commit-hook"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    (hooks_dir / "pre-commit").symlink_to(source)

    result = dc._check_git_hooks(repo_path, config.managed_hooks)
    assert result.status == "pass"


def test_check_git_hooks_fails_for_unexpected_executable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo_path = tmp_path / "planner"
    hooks_dir = repo_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    source = repo_path / ".git-pre-commit-hook"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    (hooks_dir / "pre-commit").symlink_to(source)
    extra = hooks_dir / "post-commit"
    extra.write_text("#!/bin/sh\n", encoding="utf-8")
    extra.chmod(0o755)

    result = dc._check_git_hooks(repo_path, config.managed_hooks)
    assert result.status == "fail"
    assert "unexpected executable hook: post-commit" in result.details


def test_check_agents_md_passes_with_matching_hash(tmp_path: Path) -> None:
    repo_path = tmp_path / "planner"
    repo_path.mkdir()
    agents_md = repo_path / "AGENTS.md"
    agents_md.write_text("trusted\n", encoding="utf-8")
    digest = hashlib.sha256(b"trusted\n").hexdigest()
    config_sha = "a" * 64
    workflow_json_sha = "c" * 64
    (tmp_path / ".agents_md_hash").write_text(f"v2 {digest} {config_sha} {workflow_json_sha}\n", encoding="utf-8")

    result = dc._check_agents_md(repo_path, tmp_path, [])
    assert result.status == "pass"


def test_check_agents_md_fails_when_file_modified_on_branch(tmp_path: Path) -> None:
    repo_path = tmp_path / "planner"
    repo_path.mkdir()
    agents_md = repo_path / "AGENTS.md"
    agents_md.write_text("trusted\n", encoding="utf-8")
    digest = hashlib.sha256(b"trusted\n").hexdigest()
    config_sha = "a" * 64
    workflow_json_sha = "c" * 64
    (tmp_path / ".agents_md_hash").write_text(f"v2 {digest} {config_sha} {workflow_json_sha}\n", encoding="utf-8")

    result = dc._check_agents_md(repo_path, tmp_path, ["AGENTS.md"])
    assert result.status == "fail"
    assert result.message == "AGENTS.md was modified on this branch"


def test_check_agents_md_fails_on_hash_mismatch(tmp_path: Path) -> None:
    repo_path = tmp_path / "planner"
    repo_path.mkdir()
    agents_md = repo_path / "AGENTS.md"
    agents_md.write_text("modified\n", encoding="utf-8")
    wrong_digest = "a" * 64
    config_sha = "b" * 64
    workflow_json_sha = "c" * 64
    (tmp_path / ".agents_md_hash").write_text(f"v2 {wrong_digest} {config_sha} {workflow_json_sha}\n", encoding="utf-8")

    result = dc._check_agents_md(repo_path, tmp_path, [])
    assert result.status == "fail"
    assert "hash does not match" in result.message


def test_check_agents_md_suggests_regeneration_when_config_changed(tmp_path: Path) -> None:
    repo_path = tmp_path / "planner"
    repo_path.mkdir()
    agents_md = repo_path / "AGENTS.md"
    agents_md.write_text("modified\n", encoding="utf-8")
    wrong_digest = "a" * 64
    # config.yaml exists but its sha doesn't match the trusted one
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("different: content\n", encoding="utf-8")
    stale_config_sha = "b" * 64  # deliberately wrong
    workflow_json_sha = "c" * 64
    (tmp_path / ".agents_md_hash").write_text(
        f"v2 {wrong_digest} {stale_config_sha} {workflow_json_sha}\n",
        encoding="utf-8",
    )

    result = dc._check_agents_md(repo_path, tmp_path, [])
    assert result.status == "fail"
    assert "workflow agents_md" in result.message


def test_check_agents_md_ignores_workflow_json_in_band(tmp_path: Path) -> None:
    """In-band AGENTS.md doesn't render .workflow.json, so its hash isn't blamed for drift."""
    repo_path = tmp_path / "planner"
    repo_path.mkdir()
    agents_md = repo_path / "AGENTS.md"
    agents_md.write_text("modified\n", encoding="utf-8")
    wrong_digest = "a" * 64
    (tmp_path / ".workflow.json").write_text('{"different": true}', encoding="utf-8")
    stale_workflow_json_sha = "b" * 64  # deliberately wrong
    (tmp_path / ".agents_md_hash").write_text(
        f"v2 {wrong_digest} {'c' * 64} {stale_workflow_json_sha}\n",
        encoding="utf-8",
    )

    result = dc._check_agents_md(repo_path, tmp_path, [])
    assert result.status == "fail"
    assert ".workflow.json" not in result.message


def test_check_agents_md_fails_on_non_v2_hash_file(tmp_path: Path) -> None:
    repo_path = tmp_path / "planner"
    repo_path.mkdir()
    (repo_path / "AGENTS.md").write_text("anything\n", encoding="utf-8")
    (tmp_path / ".agents_md_hash").write_text(f"v1 {'a' * 64} {'b' * 64} {'c' * 64}\n", encoding="utf-8")

    result = dc._check_agents_md(repo_path, tmp_path, [])
    assert result.status == "fail"
    assert "Hash file invalid" in result.message
    assert "v2" in result.message


def test_validate_wraps_fetch_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    (tmp_path / "planner").mkdir()
    monkeypatch.setattr(dc, "git_fetch", lambda _repo: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["git"])))

    with pytest.raises(WorkflowError, match="git fetch failed"):
        dc.validate(_plan(PlanState.PLANNING, "planner"), config)


def test_validate_wraps_diff_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    (tmp_path / "planner").mkdir()
    monkeypatch.setattr(dc, "git_fetch", lambda _repo: None)

    def raise_diff(_repo: Path, *_args: str) -> str:
        raise subprocess.CalledProcessError(1, ["git"])

    monkeypatch.setattr(dc, "git_diff_branch", raise_diff)

    with pytest.raises(WorkflowError, match="git diff failed"):
        dc.validate(_plan(PlanState.PLANNING, "planner"), config)
