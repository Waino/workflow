"""Plan state transition helpers."""

from __future__ import annotations

from pathlib import Path

from workflow.exceptions import PlanCollisionError, WorkflowError
from workflow.git_helpers import git_mv
from workflow.models import Plan, PlanState
from workflow.plan_resolver import find_plan_file_in_repo


def _locate_source(project_dir: Path, repo_name: str, plan_file_name: str) -> Path | None:
    """Locate *plan_file_name* in *repo_name*, translating a repo-wide collision into advice.

    The lookup scans the whole repo, so an unrelated sequence-number collision elsewhere in
    it also surfaces here -- with a message about the wrong plan unless it is reframed.
    """
    try:
        return find_plan_file_in_repo(project_dir, repo_name, plan_file_name)
    except PlanCollisionError as exc:
        msg = (
            f"cannot transition {plan_file_name} while sequence number {exc.sequence_number!r} collides "
            f"in {repo_name} ({', '.join(str(p.path) for p in exc.colliding)}); renumber one of them first"
        )
        raise WorkflowError(msg) from exc


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
    (e.g. ``planner/docs/plans/planning/005_foo.md``). *repo_override* redirects the
    transition to a different repo — in practice always ``"canon"``, because tickets
    are pushed and pulled there before transitioning.

    The source file is located by name in *repo_name* rather than trusted from
    *plan.path*'s state directory: *plan.path* reflects whichever repo the resolver
    found the plan in, which can disagree with *repo_override* (e.g. a plan resolved
    from a feature branch's ``checking/`` while canon's ``main`` still has it under
    ``implementing/``).
    """
    repo_name = repo_override or plan.path.parts[0]
    repo_path = project_dir / repo_name
    source = _locate_source(project_dir, repo_name, plan.path.name)
    if source is None:
        if not dry_run:
            msg = (
                f"{plan.path.name} not found in {repo_name}; push it to main first "
                f"(workflow push {plan.path.name}) or pass --target-repo"
            )
            raise WorkflowError(msg)
        # Preview-only fallback: an earlier step in this same dry run (e.g. the branch
        # transfer) may not have actually landed the file yet, since dry-run never
        # pushes for real. Guess the path a real run would find it at -- git_mv only
        # prints the command in dry-run, so this guess is never touched on disk.
        source = repo_path / "docs" / "plans" / plan.state.value / plan.path.name
    target_dir = repo_path / "docs" / "plans" / target_state.value
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / plan.path.name
    git_mv(repo_path, source, target, dry_run=dry_run)
    new_path = Path(repo_name) / "docs" / "plans" / target_state.value / plan.path.name
    return plan.model_copy(update={"state": target_state, "path": new_path})
