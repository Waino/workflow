"""Plan state transition helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from workflow.exceptions import PlanCollisionError, WorkflowError
from workflow.git_helpers import git_mv
from workflow.models import Plan, PlanState
from workflow.names import WorktreeName
from workflow.plan_resolver import find_plan_file_in_worktree

if TYPE_CHECKING:
    from workflow.layout import Layout


def locate_plan_file(layout: Layout, worktree: WorktreeName, plan_file_name: str) -> Path | None:
    """Locate *plan_file_name* in *worktree*, translating a repo-wide collision into advice.

    The lookup scans the whole repo, so an unrelated sequence-number collision elsewhere in
    it also surfaces here -- with a message about the wrong plan unless it is reframed.
    """
    try:
        return find_plan_file_in_worktree(layout, worktree, plan_file_name)
    except PlanCollisionError as exc:
        msg = (
            f"cannot transition {plan_file_name} while sequence number {exc.sequence_number!r} collides "
            f"in {worktree} ({', '.join(str(p.path) for p in exc.colliding)}); renumber one of them first"
        )
        raise WorkflowError(msg) from exc


def transition_plan(
    plan: Plan,
    target_state: PlanState,
    layout: Layout,
    worktree_override: WorktreeName | None = None,
    *,
    dry_run: bool = False,
) -> Plan:
    """Move a plan file from its current state subdirectory to the target state subdirectory.

    Uses ``git mv`` so the rename is tracked by git.
    Returns a new Plan object with the updated state and path (still relative to *layout.project_dir*).
    Does NOT commit — the caller is responsible for committing.

    *plan.path* is relative to *layout.project_dir* and its first component is the worktree
    name (e.g. ``planner/docs/plans/planning/005_foo.md``). *worktree_override* redirects the
    transition to a different worktree — in practice always ``"canon"``, because tickets
    are pushed and pulled there before transitioning.

    The source file is located by name in *worktree* rather than trusted from
    *plan.path*'s state directory: *plan.path* reflects whichever worktree the resolver
    found the plan in, which can disagree with *worktree_override* (e.g. a plan resolved
    from a feature branch's ``checking/`` while canon's ``main`` still has it under
    ``implementing/``).
    """
    worktree = worktree_override or WorktreeName(plan.path.parts[0])
    repo_root = layout.git_repo_root(worktree)
    source = locate_plan_file(layout, worktree, plan.path.name)
    if source is None:
        if not dry_run:
            msg = (
                f"{plan.path.name} not found in {worktree}; push it to main first "
                f"(workflow push {plan.path.name}) or pass --target-repo"
            )
            raise WorkflowError(msg)
        # Preview-only fallback: an earlier step in this same dry run (e.g. the branch
        # transfer) may not have actually landed the file yet, since dry-run never
        # pushes for real. Guess the path a real run would find it at -- git_mv only
        # prints the command in dry-run, so this guess is never touched on disk.
        source = layout.plans_root(worktree) / plan.state.value / plan.path.name
    target_dir = layout.plans_root(worktree) / target_state.value
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / plan.path.name
    git_mv(repo_root, source, target, dry_run=dry_run)
    new_path = Path(worktree.value) / "docs" / "plans" / target_state.value / plan.path.name
    return plan.model_copy(update={"state": target_state, "path": new_path})
