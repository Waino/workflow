"""Oob-mode tests for workflow.validate (023.6).

Builds a real oob project via `init --oob` (docker/tmux mocked or gracefully degraded,
as in test_init_oob.py) and drives `validate()` directly against real git repos.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_init_oob import _git, _make_oob_project, _run_init
from workflow.config import load_config
from workflow.exceptions import WorkflowError
from workflow.frontmatter import PlanFrontmatter, RepoBranch, write_frontmatter
from workflow.metadata import METADATA_FILENAME
from workflow.models import Plan, PlanState
from workflow.validate import CheckResult, validate

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _setup_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hook_repos: tuple[str, ...] = ("mypackage", "dep1"),
) -> Path:
    """Build a real oob project (docs, mypackage, dep1) via `init --oob`; return its config path.

    Code repos outside *hook_repos* ship no hook and are passed as ``--skip-hook``.
    """
    _make_oob_project(tmp_path, ["mypackage", "dep1"], hook_repos=hook_repos)
    skips = [arg for repo in ("mypackage", "dep1") if repo not in hook_repos for arg in ("--skip-hook", repo)]
    result = _run_init(tmp_path, monkeypatch, *skips)
    assert result.exit_code == 0, result.output
    return tmp_path / "config.yaml"


def _plan(tmp_path: Path, repo: str, state: PlanState, name: str = "005_foo.md") -> Plan:
    plan_dir = tmp_path / repo / "docs" / "plans" / state.value
    plan_dir.mkdir(parents=True, exist_ok=True)
    if not (plan_dir / name).exists():
        (plan_dir / name).write_text("# 005: Foo\n\n## Goal\nDo stuff.\n", encoding="utf-8")
    return Plan(sequence_number=[5], slug="foo", state=state, path=Path(f"{repo}/docs/plans/{state.value}/{name}"))


def _one(results: list[CheckResult], check_name: str) -> CheckResult:
    matches = [r for r in results if r.check_name == check_name]
    assert len(matches) == 1, f"expected exactly one {check_name!r} result, got {len(matches)}"
    return matches[0]


def test_planner_touching_only_plans_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    docs_repo = tmp_path / "planner" / "docs"
    _git(["checkout", "-b", "plan/005_foo"], cwd=docs_repo)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: plan foo"], cwd=docs_repo)

    results = validate(plan, config)

    assert _one(results, "planner-code-commits").status == "pass"
    assert all(r.status == "pass" for r in results if r.check_name == "file-scope")


def test_planner_commit_in_code_repo_fails_planner_code_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)
    docs_repo = tmp_path / "planner" / "docs"
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: plan foo"], cwd=docs_repo)

    dep1_repo = tmp_path / "planner" / "dep1"
    (dep1_repo / "oops.txt").write_text("oops\n", encoding="utf-8")
    _git(["add", "."], cwd=dep1_repo)
    _git(["commit", "-m", "oops: planner committed to dep1"], cwd=dep1_repo)

    results = validate(plan, config)

    result = _one(results, "planner-code-commits")
    assert result.status == "fail"
    assert any("dep1" in d for d in result.details)


def test_builder_editing_body_of_agents_fails_file_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "builder", PlanState.IMPLEMENTING)
    docs_repo = tmp_path / "builder" / "docs"
    (docs_repo / "body_of_agents.md").write_text("hacked\n", encoding="utf-8")
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)

    results = validate(plan, config)

    result = next(r for r in results if r.check_name == "file-scope" and r.repo == "docs")
    assert result.status == "fail"
    assert any("body_of_agents.md" in d for d in result.details)


def test_builder_editing_code_repo_files_passes_file_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "builder", PlanState.IMPLEMENTING)
    docs_repo = tmp_path / "builder" / "docs"
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)

    mypackage_repo = tmp_path / "builder" / "mypackage"
    (mypackage_repo / "anything.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "."], cwd=mypackage_repo)
    _git(["commit", "-m", "mypackage: anything"], cwd=mypackage_repo)

    results = validate(plan, config)

    scope_results = [r for r in results if r.check_name == "file-scope"]
    assert scope_results
    assert all(r.status == "pass" for r in scope_results)


def test_stray_hook_warns_in_code_repo_but_fails_in_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "builder", PlanState.IMPLEMENTING)
    docs_repo = tmp_path / "builder" / "docs"
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)

    for repo_name in ("docs", "mypackage"):
        hooks_dir = tmp_path / "builder" / repo_name / ".git" / "hooks"
        stray = hooks_dir / "post-commit"
        stray.write_text("#!/bin/sh\n", encoding="utf-8")
        stray.chmod(0o755)

    results = validate(plan, config)

    docs_result = next(r for r in results if r.check_name == "git-hooks" and r.repo == "docs")
    mypackage_result = next(r for r in results if r.check_name == "git-hooks" and r.repo == "mypackage")
    assert docs_result.status == "fail"
    assert mypackage_result.status == "warn"


def test_drifted_agents_md_in_reviewer_fails_agents_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "builder", PlanState.IMPLEMENTING)
    docs_repo = tmp_path / "builder" / "docs"
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)

    (tmp_path / "reviewer" / "AGENTS.md").write_text("drifted\n", encoding="utf-8")

    results = validate(plan, config)

    result = _one(results, "agents-md")
    assert result.status == "fail"
    assert any("reviewer" in d for d in result.details)


def test_implementing_plan_without_branches_frontmatter_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "builder", PlanState.IMPLEMENTING)
    docs_repo = tmp_path / "builder" / "docs"
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)

    results = validate(plan, config)

    assert _one(results, "branches-frontmatter").status == "fail"


def test_implementing_plan_with_branches_for_every_repo_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "builder", PlanState.IMPLEMENTING)
    docs_repo = tmp_path / "builder" / "docs"
    fm = PlanFrontmatter(
        branches={
            "mypackage": RepoBranch(branch="feat/widget", base="main"),
            "dep1": None,
        },
    )
    write_frontmatter(tmp_path / plan.path, fm)
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)

    results = validate(plan, config)

    assert _one(results, "branches-frontmatter").status == "pass"


def _commit_builder_plan(tmp_path: Path, frontmatter: PlanFrontmatter | None = None) -> Plan:
    plan = _plan(tmp_path, "builder", PlanState.IMPLEMENTING)
    if frontmatter is not None:
        write_frontmatter(tmp_path / plan.path, frontmatter)
    docs_repo = tmp_path / "builder" / "docs"
    _git(["add", "."], cwd=docs_repo)
    _git(["commit", "-m", "005: implement foo"], cwd=docs_repo)
    return plan


def test_default_project_passes_git_hooks_in_every_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Code repos ship the hook and init symlinks it, so validate passes the git-hooks check everywhere."""
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)

    results = validate(plan, config)

    hook_results = [r for r in results if r.check_name == "git-hooks"]
    assert {r.repo for r in hook_results} == {"docs", "mypackage", "dep1"}
    assert all(r.status == "pass" for r in hook_results), hook_results
    assert not [r for r in results if r.status == "fail"], results


def test_code_repos_own_pre_commit_hook_only_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A third-party repo's own extra hook is routine."""
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)
    hook = tmp_path / "planner" / "mypackage" / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    hook.chmod(0o755)

    results = validate(plan, config)

    result = next(r for r in results if r.check_name == "git-hooks" and r.repo == "mypackage")
    assert result.status == "warn"
    assert result.details == ["unexpected executable hook: post-commit"]


def test_missing_managed_symlink_fails_when_repo_ships_the_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch, hook_repos=("mypackage",))
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)
    (tmp_path / "planner" / "mypackage" / ".git" / "hooks" / "pre-commit").unlink()

    results = validate(plan, config)

    result = next(r for r in results if r.check_name == "git-hooks" and r.repo == "mypackage")
    assert result.status == "fail"
    assert any("not a symlink" in d for d in result.details)


def test_missing_clone_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)
    shutil.rmtree(tmp_path / "planner" / "dep1")

    with pytest.raises(WorkflowError, match="dep1 is missing"):
        validate(plan, config)


def test_custom_agent_name_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)

    with pytest.raises(WorkflowError, match="not a worktree"):
        validate(plan, config, target_worktree_override="manual")


def test_agents_md_drift_names_changed_workflow_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)
    metadata = tmp_path / METADATA_FILENAME
    metadata.write_text(metadata.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    (tmp_path / "builder" / "AGENTS.md").write_text("drifted\n", encoding="utf-8")

    result = _one(validate(plan, config), "agents-md")

    assert result.status == "fail"
    assert METADATA_FILENAME in result.message


@pytest.mark.parametrize(
    ("branches", "expected_detail"),
    [
        pytest.param(
            {"mypackage": RepoBranch(branch="bad..name", base="main"), "dep1": None},
            "mypackage: 'bad..name' is not a valid branch name",
            id="invalid-branch-name",
        ),
        pytest.param(
            {"mypackage": None, "dep1": None, "dep9": None},
            "unknown repo 'dep9'",
            id="unknown-repo-key",
        ),
        pytest.param({"mypackage": None}, "missing entry for 'dep1'", id="missing-repo"),
    ],
)
def test_branches_frontmatter_flags_bad_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    branches: dict[str, RepoBranch | None],
    expected_detail: str,
) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _commit_builder_plan(tmp_path, PlanFrontmatter(branches=branches))

    result = _one(validate(plan, config), "branches-frontmatter")

    assert result.status == "fail"
    assert result.details == [expected_detail]


def test_branches_frontmatter_accepts_null_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``null`` records "not touched by this plan" -- still counts as an entry."""
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _commit_builder_plan(tmp_path, PlanFrontmatter(branches={"mypackage": None, "dep1": None}))

    assert _one(validate(plan, config), "branches-frontmatter").status == "pass"


def test_branches_frontmatter_not_required_while_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _setup_project(tmp_path, monkeypatch)
    config = load_config(config_path, tmp_path)
    plan = _plan(tmp_path, "planner", PlanState.PLANNING)

    result = _one(validate(plan, config), "branches-frontmatter")

    assert result.status == "pass"
    assert "not required" in result.message
