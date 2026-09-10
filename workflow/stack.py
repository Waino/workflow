"""Stack resolution: which plans share a feature branch, and in what order.

Single home for everything stack-shaped: ``branch_for_plan`` is THE branch-name
helper (no ``"feat/"`` literal belongs anywhere else in the package), and
``resolve_stack``/``stack_child``/``tip_plan_for_branch`` are how commands
discover a stack's other members. Imports :mod:`workflow.frontmatter` and
:mod:`workflow.plan_resolver`, never the reverse.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - used at runtime by Pydantic

from pydantic import BaseModel, ConfigDict

from workflow.config import WorkflowConfig
from workflow.exceptions import AmbiguousPlanError, PlanCollisionError, PlanNotFoundError, WorkflowError
from workflow.frontmatter import PlanFrontmatter, parse_frontmatter
from workflow.models import Plan, PlanState, Review
from workflow.names import DOCS_REPO, RepoName
from workflow.plan_resolver import find_all_plans_in_repo, resolve_plan
from workflow.state import locate_plan_file

BRANCH_PREFIX = "feat/"

_MAX_STACK_DEPTH = 10
"""Runaway guard against a hand-edited `onto` cycle that the `seen`-set check would miss."""


class StackEntry(BaseModel):
    """One stack member, as needed for prompt rendering."""

    model_config = ConfigDict(frozen=True)

    name: str
    path: Path
    """As resolved, worktree-prefixed, matching the existing ``{{ plan.path }}`` convention."""

    commit_range: str
    """E.g. 'origin/main..7f3a91c' for the root, '7f3a91c..HEAD' for the tip."""


def read_plan_frontmatter(project_dir: Path, plan: Plan) -> PlanFrontmatter | None:
    """Return *plan*'s parsed frontmatter, or ``None`` if it has none.

    Reads from *plan*'s resolved path, falling back to canon by file name: during a
    stack's life canon's ``main`` and the branch disagree about state directories.
    """
    path = project_dir / plan.path
    if not path.is_file():
        located = locate_plan_file(project_dir, RepoName.canon, plan.path.name)
        if located is None:
            return None
        path = located
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text, path=path)


def branch_for_plan(project_dir: Path, plan: Plan) -> str:
    """Return the git branch *plan* lives (or will live) on.

    One frontmatter read, no chain walk -- this is why the resolved branch is stored
    rather than derived: a broken ``onto`` must not make a plan unroutable.
    """
    fm = read_plan_frontmatter(project_dir, plan)
    if fm is not None:
        repo_branch = fm.branches.get(DOCS_REPO)
        if repo_branch is not None:
            return repo_branch.branch
    return f"{BRANCH_PREFIX}{plan.name}"


def resolve_stack(project_dir: Path, plan: Plan, config: WorkflowConfig) -> list[Plan]:
    """Resolve the full stack *plan* belongs to, base-first, including *plan* itself.

    Walks ``onto`` upward. Raises :class:`WorkflowError` on a cycle, an unresolvable
    ``onto`` target, or a chain deeper than :data:`_MAX_STACK_DEPTH`.
    """
    chain: list[Plan] = [plan]
    seen: set[str] = {plan.name}
    current = plan
    while True:
        fm = read_plan_frontmatter(project_dir, current)
        onto = fm.onto if fm is not None else None
        if onto is None:
            break
        if onto in seen:
            msg = f"onto cycle detected: {current.name!r} points at {onto!r}, which is already in the stack"
            raise WorkflowError(msg)
        if len(chain) >= _MAX_STACK_DEPTH:
            msg = f"stack depth exceeds {_MAX_STACK_DEPTH} while resolving {current.name!r}'s onto; check for a cycle"
            raise WorkflowError(msg)
        try:
            base_plan = resolve_plan(onto, project_dir, config=config)
        except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError, WorkflowError) as exc:
            msg = f"{current.name}'s onto={onto!r} does not resolve to a plan: {exc}"
            raise WorkflowError(msg) from exc
        chain.append(base_plan)
        seen.add(onto)
        current = base_plan
    chain.reverse()
    return chain


def stack_child(project_dir: Path, plan: Plan) -> Plan | None:
    """Return the non-rejected plan whose ``onto`` is *plan*, or ``None``.

    Rejected plans are ignored, which is what makes "reject the tip, then accept the
    base" work: once the child is rejected, the base becomes actionable again.
    """
    for candidate in find_all_plans_in_repo(project_dir, RepoName.canon):
        if isinstance(candidate, Review) or candidate.state == PlanState.REJECTED or candidate.name == plan.name:
            continue
        fm = read_plan_frontmatter(project_dir, candidate)
        if fm is not None and fm.onto == plan.name:
            return candidate
    return None


def tip_plan_for_branch(project_dir: Path, branch: str) -> Plan | None:
    """Return the tip plan (the stack member with no child) resolving to *branch*, or ``None``."""
    candidates = [
        candidate
        for candidate in find_all_plans_in_repo(project_dir, RepoName.canon)
        if not isinstance(candidate, Review)
        and candidate.state != PlanState.REJECTED
        and branch_for_plan(project_dir, candidate) == branch
    ]
    for candidate in candidates:
        if stack_child(project_dir, candidate) is None:
            return candidate
    return candidates[0] if candidates else None


def stack_entries(project_dir: Path, plan: Plan, config: WorkflowConfig) -> list[StackEntry]:
    """Return prompt-rendering entries for *plan*'s stack, base-first, *plan* itself last.

    Empty for an unstacked plan. ``commit_range`` is assembled from the frontmatter
    anchors only (no git calls): member *i* spans ``base(i)..base(i+1)``, the root
    starts at ``origin/main``, the tip ends at ``HEAD``.
    """
    chain = resolve_stack(project_dir, plan, config)
    if len(chain) == 1:
        return []
    bases: list[str] = []
    for member in chain[1:]:
        fm = read_plan_frontmatter(project_dir, member)
        repo_branch = fm.branches.get(DOCS_REPO) if fm is not None else None
        if repo_branch is None:
            msg = f"{member.name} is stacked but has no recorded branch base"
            raise WorkflowError(msg)
        bases.append(repo_branch.base)
    boundaries = ["origin/main", *bases, "HEAD"]
    return [
        StackEntry(name=member.name, path=member.path, commit_range=f"{boundaries[i]}..{boundaries[i + 1]}")
        for i, member in enumerate(chain)
    ]
