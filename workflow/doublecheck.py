"""Doublecheck verification suite.

Implements the pre-merge checks and exposes them via the reusable
``doublecheck`` function.  The CLI command is registered in
``workflow/commands/workflow.py``.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from workflow.exceptions import WorkflowError
from workflow.git_helpers import git_diff_branch, git_fetch

if TYPE_CHECKING:
    from workflow.config import WorkflowConfig
    from workflow.models import Plan

CHECK_NAMES: list[str] = ["file-scope", "noqa-nosec", "git-hooks", "claude-md"]

# ---------------------------------------------------------------------------
# CheckResult model
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    """Result of a single doublecheck verification."""

    check_name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    details: list[str] = []


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def infer_target_repo(
    plan: Plan,
    config: WorkflowConfig,
    target_repo_override: Literal["planner", "builder", "reviewer", "master"] | None = None,
) -> str:
    """Return the repo name to inspect for *plan*, honouring any override.

    Falls back to the agent configured for the plan's state in *config*.
    States with no agent (e.g. ``todo``, ``done``, ``rejected``) map to ``"master"``.
    """
    if target_repo_override is not None:
        return target_repo_override
    state_cfg = config.states.get(plan.state)
    if state_cfg is None or state_cfg.agent is None:
        return "master"
    return state_cfg.agent


def doublecheck(
    plan: Plan,
    project_dir: Path,
    config: WorkflowConfig,
    *,
    target_repo_override: Literal["planner", "builder", "reviewer", "master"] | None = None,
    dry_run: bool = False,
) -> list[CheckResult]:
    """Run all verification checks.  Returns a list of results (pass/warn/fail).

    In dry-run mode returns an empty list without running any checks or fetching.
    Missing prerequisites (missing hash file, fetch failure) raise
    :class:`~workflow.exceptions.WorkflowError`.
    """
    target_repo = infer_target_repo(plan, config, target_repo_override)
    repo_path = project_dir / target_repo

    if dry_run:
        return []

    try:
        git_fetch(repo_path)
    except subprocess.CalledProcessError as exc:
        msg = f"git fetch failed in {repo_path}: {exc}"
        raise WorkflowError(msg) from exc

    return [
        _check_file_scope(repo_path, target_repo),
        _check_noqa_nosec(repo_path),
        _check_git_hooks(repo_path, config),
        _check_claude_md(repo_path, project_dir),
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_file_scope(repo_path: Path, target_repo: str) -> CheckResult:
    changed = [f for f in git_diff_branch(repo_path, "--name-only").splitlines() if f]
    violations = [f for f in changed if not _is_allowed(f, target_repo)]
    if violations:
        return CheckResult(
            check_name="file-scope",
            status="fail",
            message=f"{len(violations)} file(s) outside allowed scope for {target_repo!r}",
            details=violations,
        )
    return CheckResult(check_name="file-scope", status="pass", message="All changed files within scope")


def _is_allowed(file_path: str, target_repo: str) -> bool:
    """Return True if *file_path* (relative to repo root) may be changed by *target_repo*."""
    parts = Path(file_path).parts
    under_plans = parts[:2] == ("docs", "plans")
    is_intent = under_plans and parts[2:3] == ("intent",)
    is_review = under_plans and parts[2:3] == ("reviews",)

    if target_repo == "master":
        return True
    if target_repo == "planner":
        return under_plans and not is_review
    if target_repo == "builder":
        return not is_intent and not is_review
    if target_repo == "reviewer":
        return not is_intent
    return True  # unknown repo: allow


def _check_noqa_nosec(repo_path: Path) -> CheckResult:
    found = []
    for line in git_diff_branch(repo_path).splitlines():
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
            issues.append(f"{hook_name}: not a symlink")
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


def _check_claude_md(repo_path: Path, project_dir: Path) -> CheckResult:
    hash_file = project_dir / ".claude_md_hash"
    if not hash_file.exists():
        msg = f"Trusted hash file not found: {hash_file}"
        raise WorkflowError(msg)

    claude_md = repo_path / "CLAUDE.md"
    if not claude_md.exists():
        return CheckResult(check_name="claude-md", status="fail", message="CLAUDE.md not found in repo")

    trusted_hash = hash_file.read_text(encoding="utf-8").strip().split()[0]
    actual_hash = hashlib.sha256(claude_md.read_bytes()).hexdigest()

    changed_files = git_diff_branch(repo_path, "--name-only").splitlines()
    claude_modified = "CLAUDE.md" in changed_files

    if actual_hash != trusted_hash:
        return CheckResult(
            check_name="claude-md",
            status="fail",
            message="CLAUDE.md hash does not match trusted value",
        )
    if claude_modified:
        return CheckResult(
            check_name="claude-md",
            status="fail",
            message="CLAUDE.md was modified on this branch",
        )
    return CheckResult(check_name="claude-md", status="pass", message="CLAUDE.md integrity verified")
