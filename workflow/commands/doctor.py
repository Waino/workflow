"""``workflow doctor``: read-only diagnostics for manual recovery.

Reports the resting-state of every clone (canon, bare, planner, builder,
reviewer) and flags plan files duplicated across state directories within a
single repo (the signature of a botched `git mv` transition). Never mutates
anything; exits non-zero when any anomaly is found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import click
from rich.table import Table

from workflow.exceptions import PlanCollisionError
from workflow.git_helpers import git_current_branch, git_index_lock_present, git_is_valid_repo, git_status_porcelain
from workflow.models import PlanState
from workflow.names import RepoName
from workflow.plan_resolver import find_all_plans_in_repo
from workflow.registry import register_script
from workflow.shell import console, error_console

if TYPE_CHECKING:
    from pathlib import Path

    from workflow.config import WorkflowConfig

_ACTIVE_FEATURE_BRANCH_STATES = (PlanState.IMPLEMENTING, PlanState.CHECKING)

# Plan states that occupy a single pipeline slot per (sequence, slug); a plan
# legitimately co-exists with its originating INTENT file and/or REVIEW documents,
# so those are excluded rather than enumerated positively (avoids drift if a new
# pipeline state is added to PlanState).
_PIPELINE_STATES = frozenset(PlanState) - {PlanState.INTENT, PlanState.REVIEW}


@dataclass
class RepoCheck:
    """Diagnostic snapshot of a single clone."""

    name: str
    exists: bool
    valid: bool | None = None  # None when exists is False
    branch: str | None = None  # None for the bare repo (no worktree)
    dirty: bool | None = None  # None for the bare repo
    index_lock: bool | None = None  # None when exists is False
    anomalies: list[str] = field(default_factory=list)


def _active_feature_branches(project_dir: Path) -> set[str]:
    """Return `feat/<name>` branches expected for plans currently mid-implementation/review."""
    branches: set[str] = set()
    for repo in RepoName:
        if not (project_dir / repo).is_dir():
            continue
        try:
            plans = find_all_plans_in_repo(project_dir, repo)
        except PlanCollisionError:
            continue
        branches.update(f"feat/{plan.name}" for plan in plans if plan.state in _ACTIVE_FEATURE_BRANCH_STATES)
    return branches


def _check_bare(bare_path: Path) -> RepoCheck:
    check = RepoCheck(name="bare", exists=bare_path.is_dir())
    if not check.exists:
        check.anomalies.append("missing")
        return check
    check.valid = git_is_valid_repo(bare_path, bare=True)
    if not check.valid:
        check.anomalies.append("not a valid bare repo")
    check.index_lock = git_index_lock_present(bare_path)
    if check.index_lock:
        check.anomalies.append("stale .git/index.lock")
    return check


def _check_worktree(repo_name: RepoName, repo_path: Path, *, active_feature_branches: set[str]) -> RepoCheck:
    check = RepoCheck(name=repo_name.value, exists=repo_path.is_dir())
    if not check.exists:
        check.anomalies.append("missing")
        return check

    check.valid = git_is_valid_repo(repo_path)
    if not check.valid:
        check.anomalies.append("not a valid git repo")
        return check

    check.index_lock = git_index_lock_present(repo_path)
    if check.index_lock:
        check.anomalies.append("stale .git/index.lock")

    check.branch = git_current_branch(repo_path)
    check.dirty = bool(git_status_porcelain(repo_path))
    if check.dirty:
        check.anomalies.append("uncommitted changes")

    if repo_name == RepoName.canon and check.branch != "main":
        check.anomalies.append(f"expected 'main', on {check.branch!r}")
    elif (
        repo_name in (RepoName.builder, RepoName.reviewer)
        and check.branch != "main"
        and check.branch not in active_feature_branches
    ):
        check.anomalies.append(f"stray branch {check.branch!r}: no plan is implementing/checking on it")
    return check


def _duplicate_plan_anomalies(project_dir: Path) -> list[str]:
    """Return one message per plan that appears under more than one state dir in the same repo."""
    anomalies: list[str] = []
    for repo in RepoName:
        if not (project_dir / repo).is_dir():
            continue
        try:
            plans = find_all_plans_in_repo(project_dir, repo)
        except PlanCollisionError as exc:
            anomalies.append(f"{repo}: sequence number collision on {exc.sequence_number!r}")
            continue
        by_key: dict[str, list[str]] = {}
        for plan in plans:
            if plan.state not in _PIPELINE_STATES:
                continue
            key = f"{plan.sequence_number_str}:{plan.slug}"
            by_key.setdefault(key, []).append(plan.state.value)
        for key, states in by_key.items():
            if len(states) > 1:
                anomalies.append(f"{repo}: {key} appears in multiple state dirs: {', '.join(sorted(states))}")
    return anomalies


def _render_table(checks: list[RepoCheck]) -> Table:
    table = Table(title="Workflow Doctor")
    table.add_column("repo")
    table.add_column("exists")
    table.add_column("branch")
    table.add_column("clean")
    table.add_column("lock")
    table.add_column("valid")
    table.add_column("anomalies")
    for check in checks:
        table.add_row(
            check.name,
            "yes" if check.exists else "no",
            check.branch or "",
            "" if check.dirty is None else ("no" if check.dirty else "yes"),
            "" if check.index_lock is None else ("yes" if check.index_lock else "no"),
            "" if check.valid is None else ("yes" if check.valid else "no"),
            "; ".join(check.anomalies) if check.anomalies else "",
        )
    return table


@register_script("doctor")
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Report clone health and plan-file anomalies; exit non-zero if any are found."""
    config: WorkflowConfig = ctx.obj["config"]

    if config.project_dir is None:
        error_console.print("[red]Error: not inside a workflow project directory.[/red]")
        ctx.exit(1)
        return
    project_dir: Path = config.project_dir

    active_feature_branches = _active_feature_branches(project_dir)
    checks = [_check_bare(project_dir / "bare")]
    checks.extend(
        _check_worktree(repo, project_dir / repo, active_feature_branches=active_feature_branches) for repo in RepoName
    )
    console.print(_render_table(checks))

    duplicate_anomalies = _duplicate_plan_anomalies(project_dir)
    if duplicate_anomalies:
        console.print("[bold]Duplicated plan files:[/bold]")
        for msg in duplicate_anomalies:
            console.print(f"  - {msg}")

    any_anomaly = any(c.anomalies for c in checks) or bool(duplicate_anomalies)
    ctx.exit(1 if any_anomaly else 0)
