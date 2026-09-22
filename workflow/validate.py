"""Validate verification suite.

Implements the pre-merge checks and exposes them via the reusable
``validate`` function.  The CLI command is registered in
``workflow/commands/workflow.py``.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from workflow.agents_md import parse_hash_file
from workflow.exceptions import AgentsMdGenerationError, WorkflowError
from workflow.git_helpers import git_diff_branch, git_fetch
from workflow.names import WorktreeName

if TYPE_CHECKING:
    from workflow.config import WorkflowConfig
    from workflow.layout import Layout
    from workflow.models import Plan

CHECK_NAMES: list[str] = ["file-scope", "noqa-nosec", "git-hooks", "agents-md"]

# ---------------------------------------------------------------------------
# CheckResult model
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    """Result of a single validate verification."""

    check_name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    details: list[str] = []


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def infer_target_worktree(
    plan: Plan,
    config: WorkflowConfig,
    target_worktree_override: WorktreeName | str | None = None,
) -> WorktreeName | str:
    """Return the repo name to inspect for *plan*, honouring any override.

    Falls back to the agent configured for the plan's state in *config*.
    States with no agent (e.g. ``todo``, ``done``, ``rejected``) map to ``"canon"``.
    """
    if target_worktree_override is not None:
        return target_worktree_override
    state_cfg = config.states.get(plan.state)
    if state_cfg is None or state_cfg.agent is None:
        return WorktreeName.canon
    return state_cfg.agent


def _repo_path(layout: Layout, target_repo: WorktreeName | str) -> Path:
    """Resolve *target_repo* to a directory, via the layout when it names a real worktree.

    Falls back to a direct join for a custom agent name with no matching worktree
    (e.g. a manual-shell-only agent) -- the same latitude the plain path join gave it
    before the layout refactor.
    """
    try:
        worktree = WorktreeName(target_repo)
    except ValueError:
        return layout.project_dir / target_repo
    return layout.git_repo_root(worktree)


def validate(
    plan: Plan,
    config: WorkflowConfig,
    *,
    target_worktree_override: WorktreeName | str | None = None,
    dry_run: bool = False,
) -> list[CheckResult]:
    """Run all verification checks.  Returns a list of results (pass/warn/fail).

    In dry-run mode returns an empty list without running any checks or fetching.
    Missing prerequisites (missing hash file, fetch failure) raise
    :class:`~workflow.exceptions.WorkflowError`.
    """
    layout = config.layout()
    target_repo = infer_target_worktree(plan, config, target_worktree_override)
    repo_path = _repo_path(layout, target_repo)

    if dry_run:
        return []

    try:
        git_fetch(repo_path)
    except subprocess.CalledProcessError as exc:
        msg = f"git fetch failed in {repo_path}: {exc}"
        raise WorkflowError(msg) from exc

    full_diff = _read_branch_diff(repo_path)
    changed_files = [line for line in _read_branch_diff(repo_path, "--name-only").splitlines() if line]

    return [
        _check_file_scope(changed_files, target_repo),
        _check_noqa_nosec(full_diff),
        _check_git_hooks(repo_path, config),
        _check_agents_md(repo_path, layout.project_dir, changed_files),
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_branch_diff(repo_path: Path, *extra_args: str) -> str:
    """Return branch diff text, wrapping git failures in :class:`WorkflowError`."""
    try:
        return git_diff_branch(repo_path, *extra_args)
    except subprocess.CalledProcessError as exc:
        msg = f"git diff failed in {repo_path}: {exc}"
        raise WorkflowError(msg) from exc


def _check_file_scope(changed_files: list[str], target_repo: WorktreeName | str) -> CheckResult:
    violations = [f for f in changed_files if not _is_allowed(f, target_repo)]
    if violations:
        return CheckResult(
            check_name="file-scope",
            status="fail",
            message=f"{len(violations)} file(s) outside allowed scope for {target_repo!r}",
            details=violations,
        )
    return CheckResult(check_name="file-scope", status="pass", message="All changed files within scope")


def _is_allowed(file_path: str, target_repo: WorktreeName | str) -> bool:
    """Return True if *file_path* (relative to repo root) may be changed by *target_repo*."""
    parts = Path(file_path).parts
    under_plans = parts[:2] == ("docs", "plans")
    is_intent = under_plans and parts[2:3] == ("intent",)
    is_review = under_plans and parts[2:3] == ("reviews",)

    if target_repo == WorktreeName.canon:
        return True
    if target_repo == WorktreeName.planner:
        return under_plans and not is_review
    if target_repo == WorktreeName.builder:
        return not is_intent and not is_review
    if target_repo == WorktreeName.reviewer:
        return not is_intent
    return True  # unknown repo: allow


def _check_noqa_nosec(diff_text: str) -> CheckResult:
    found = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:]
            if "noqa" in stripped or "nosec" in stripped:
                found.append(stripped.strip())
    if found:
        return CheckResult(
            check_name="noqa-nosec",
            status="warn",
            message=f"{len(found)} noqa/nosec comment(s) added",
            details=found,
        )
    return CheckResult(check_name="noqa-nosec", status="pass", message="No noqa/nosec comments added")


def _check_git_hooks(repo_path: Path, config: WorkflowConfig) -> CheckResult:
    hooks_dir = repo_path / ".git" / "hooks"
    issues: list[str] = []

    for hook_name, hook_source in config.managed_hooks.items():
        hook_path = hooks_dir / hook_name
        expected = (repo_path / hook_source).resolve()
        if not hook_path.is_symlink():
            issues.append(f"{hook_name}: not a symlink (expected {expected})")
        elif hook_path.resolve() != expected:
            issues.append(f"{hook_name}: symlink points to wrong target (expected {expected})")

    if hooks_dir.is_dir():
        for hook_file in sorted(hooks_dir.iterdir()):
            if hook_file.suffix == ".sample":
                continue
            if hook_file.name in config.managed_hooks:
                continue
            if hook_file.is_file() and os.access(hook_file, os.X_OK):
                issues.append(f"unexpected executable hook: {hook_file.name}")

    if issues:
        return CheckResult(
            check_name="git-hooks",
            status="fail",
            message=f"{len(issues)} git hook issue(s)",
            details=issues,
        )
    return CheckResult(check_name="git-hooks", status="pass", message="Git hooks correctly configured")


def _check_agents_md(repo_path: Path, project_dir: Path, changed_files: list[str]) -> CheckResult:
    hash_file = project_dir / ".agents_md_hash"
    if not hash_file.exists():
        msg = f"Trusted hash file not found: {hash_file}"
        raise WorkflowError(msg)

    agents_md = repo_path / "AGENTS.md"
    if not agents_md.exists():
        return CheckResult(check_name="agents-md", status="fail", message="AGENTS.md not found in repo")

    try:
        trusted = parse_hash_file(hash_file.read_text(encoding="utf-8"))
    except AgentsMdGenerationError as exc:
        return CheckResult(check_name="agents-md", status="fail", message=f"Hash file invalid: {exc}")

    actual_hash = hashlib.sha256(agents_md.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    agents_md_modified = "AGENTS.md" in changed_files

    if actual_hash != trusted.agents_md_sha:
        config_yaml = project_dir / "config.yaml"
        config_yaml_sha = hashlib.sha256(config_yaml.read_bytes()).hexdigest() if config_yaml.exists() else ""
        if config_yaml_sha != trusted.config_yaml_sha:
            message = (
                "AGENTS.md hash does not match trusted value; "
                "config.yaml has changed since AGENTS.md was last generated — run `workflow agents_md`."
            )
        else:
            message = "AGENTS.md hash does not match trusted value"
        return CheckResult(check_name="agents-md", status="fail", message=message)

    if agents_md_modified:
        return CheckResult(
            check_name="agents-md",
            status="fail",
            message="AGENTS.md was modified on this branch",
        )
    return CheckResult(check_name="agents-md", status="pass", message="AGENTS.md integrity verified")
