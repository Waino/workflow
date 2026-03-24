"""Plan state transition helpers."""

from __future__ import annotations

from pathlib import Path

from workflow.git_helpers import git_mv
from workflow.models import Plan, PlanState


def transition_plan(
    plan: Plan,
    target_state: PlanState,
    project_dir: Path,
    repo_override: str | None = None,
    *,
    dry_run: bool = False,
) -> Plan:
    """Move a plan file from its current state subdirectory to the target state subdirectory.

    Uses ``git mv`` so the rename is tracked by git.
    Returns a new Plan object with the updated state and path (still relative to *project_dir*).
    Does NOT commit — the caller is responsible for committing.

    *plan.path* is relative to *project_dir* and its first component is the repo name
    (e.g. ``planner/docs/plans/planning/005_foo.md``).  *repo_override* replaces that
    component when the transition must be performed in a different repo — in practice
    always ``"master"``, because plans are pushed and pulled there before transitioning.
    """
    repo_name = repo_override if repo_override is not None else plan.path.parts[0]
    repo_path = project_dir / repo_name
    source = project_dir / plan.path
    target_dir = repo_path / "docs" / "plans" / target_state.value
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / plan.path.name
    git_mv(repo_path, source, target, dry_run=dry_run)
    new_path = Path(repo_name) / "docs" / "plans" / target_state.value / plan.path.name
    return plan.model_copy(update={"state": target_state, "path": new_path})
