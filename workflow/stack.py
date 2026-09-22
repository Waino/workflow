"""Stack resolution: which plans share a feature branch, and in what order.

Single home for everything stack-shaped: ``branch_for_plan`` is THE branch-name
helper (no ``"feat/"`` literal belongs anywhere else in the package), and
``resolve_stack``/``stack_child``/``tip_plan_for_branch`` are how commands
discover a stack's other members. Imports :mod:`workflow.frontmatter` and
:mod:`workflow.plan_resolver`'s ``find_all_plans_in_worktree`` only, never the reverse.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - used at runtime by Pydantic
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from workflow.exceptions import WorkflowError
from workflow.frontmatter import PlanFrontmatter, parse_frontmatter
from workflow.models import Plan, PlanState, Review
from workflow.names import DOCS_REPO, WorktreeName
from workflow.plan_resolver import find_all_plans_in_worktree
from workflow.state import locate_plan_file

if TYPE_CHECKING:
    from workflow.layout import Layout

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


_FRONTMATTER_CACHE: dict[Path, tuple[int, int, PlanFrontmatter | None]] = {}
"""path -> (mtime_ns, size, parsed): keyed on file identity, so a rewrite invalidates itself."""


def _read_frontmatter_cached(path: Path) -> PlanFrontmatter | None:
    """Parse *path*'s frontmatter, reusing the last parse while the file is unchanged.

    Stack resolution re-reads the same handful of plan files many times per command
    (one `branch_for_plan` plus one `onto` lookup per plan, per scan).
    """
    # ponytail: unbounded dict, bounded in practice by the plan count of one CLI process.
    stat = path.stat()
    cached = _FRONTMATTER_CACHE.get(path)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    parsed = parse_frontmatter(path.read_text(encoding="utf-8"), path=path)
    _FRONTMATTER_CACHE[path] = (stat.st_mtime_ns, stat.st_size, parsed)
    return parsed


def read_plan_frontmatter(layout: Layout, plan: Plan) -> PlanFrontmatter | None:
    """Return *plan*'s parsed frontmatter, or ``None`` if it has none.

    Reads from *plan*'s resolved path, falling back to canon by file name: during a
    stack's life canon's ``main`` and the branch disagree about state directories.
    """
    path = layout.project_dir / plan.path
    if not path.is_file():
        located = locate_plan_file(layout, WorktreeName.canon, plan.path.name)
        if located is None:
            return None
        path = located
    return _read_frontmatter_cached(path)


def branch_for_plan(layout: Layout, plan: Plan) -> str:
    """Return the git branch *plan* lives (or will live) on.

    One frontmatter read, no chain walk -- this is why the resolved branch is stored
    rather than derived: a broken ``onto`` must not make a plan unroutable.
    """
    fm = read_plan_frontmatter(layout, plan)
    if fm is not None:
        repo_branch = fm.branches.get(DOCS_REPO)
        if repo_branch is not None:
            return repo_branch.branch
    return f"{BRANCH_PREFIX}{plan.name}"


def _normalize_stack_reference(reference: str) -> str:
    """Strip any state prefix and `.md` suffix, and zero-pad a bare sequence number.

    Zero-padding keeps hand-written references like ``--onto 25`` or ``onto: 23.1``
    working, since plan file names always carry the three-digit form.
    """
    normalized = reference.rsplit("/", 1)[-1].removesuffix(".md")
    head, _, rest = normalized.partition(".")
    if head.isdigit():
        return f"{head.zfill(3)}{'.' if rest else ''}{rest}"
    return normalized


def find_stack_member(layout: Layout, reference: str, prefer_repo: WorktreeName = WorktreeName.canon) -> Plan | None:
    """Find the plan named by a stack reference (`onto` value or `--onto` argument).

    Matches the canonical plan name, or the (optionally unpadded) sequence number for a
    hand-written reference. Searches *prefer_repo* first, then canon, so a stack resolved
    from an agent's clone keeps that clone's paths. Not state-filtered: a rejected or done
    base must still be findable so a stack with such a member does not become unresolvable.
    """
    normalized = _normalize_stack_reference(reference)
    for worktree in dict.fromkeys([prefer_repo, WorktreeName.canon]):
        candidates = [
            candidate for candidate in find_all_plans_in_worktree(layout, worktree) if not isinstance(candidate, Review)
        ]
        for candidate in candidates:
            if candidate.name == normalized:
                return candidate
        for candidate in candidates:
            if candidate.sequence_number_str == normalized:
                return candidate
    return None


def resolve_stack(layout: Layout, plan: Plan) -> list[Plan]:
    """Resolve the full stack *plan* belongs to, base-first, including *plan* itself.

    Walks ``onto`` upward. Raises :class:`WorkflowError` on a cycle, an unresolvable
    ``onto`` target, or a chain deeper than :data:`_MAX_STACK_DEPTH`.
    """
    chain: list[Plan] = [plan]
    seen: set[str] = {plan.name}
    current = plan
    while True:
        fm = read_plan_frontmatter(layout, current)
        onto = fm.onto if fm is not None else None
        if onto is None:
            break
        if onto in seen:
            msg = f"onto cycle detected: {current.name!r} points at {onto!r}, which is already in the stack"
            raise WorkflowError(msg)
        if len(chain) >= _MAX_STACK_DEPTH:
            msg = f"stack depth exceeds {_MAX_STACK_DEPTH} while resolving {current.name!r}'s onto; check for a cycle"
            raise WorkflowError(msg)
        # Plan paths are repo-prefixed; fall back to canon rather than crash on a hand-built path.
        head = current.path.parts[0] if current.path.parts else ""
        prefer_repo = WorktreeName(head) if head in WorktreeName.__members__ else WorktreeName.canon
        base_plan = find_stack_member(layout, onto, prefer_repo=prefer_repo)
        if base_plan is None:
            msg = (
                f"{current.name}'s onto={onto!r} does not resolve to a plan: "
                f"searched {prefer_repo!r} and {WorktreeName.canon!r}"
            )
            raise WorkflowError(msg)
        chain.append(base_plan)
        seen.add(onto)
        current = base_plan
    chain.reverse()
    return chain


def _stackable_plans(layout: Layout) -> list[Plan]:
    """Canon's plans that can take part in a stack: no reviews, no rejected plans.

    Rejected plans are ignored, which is what makes "reject the tip, then accept the
    base" work: once the child is rejected, the base becomes actionable again.
    """
    return [
        candidate
        for candidate in find_all_plans_in_worktree(layout, WorktreeName.canon)
        if not isinstance(candidate, Review) and candidate.state != PlanState.REJECTED
    ]


def _children_by_onto(layout: Layout) -> dict[str, Plan]:
    """Map each stacked-onto plan name to the plan stacked on it -- one scan for all lookups."""
    children: dict[str, Plan] = {}
    for candidate in _stackable_plans(layout):
        fm = read_plan_frontmatter(layout, candidate)
        if fm is not None and fm.onto is not None and fm.onto != candidate.name:
            children[fm.onto] = candidate
    return children


def stack_child(layout: Layout, plan: Plan) -> Plan | None:
    """Return the non-rejected plan whose ``onto`` is *plan*, or ``None``."""
    return _children_by_onto(layout).get(plan.name)


def tip_plan_for_branch(layout: Layout, branch: str) -> Plan | None:
    """Return the tip plan (the stack member with no child) resolving to *branch*, or ``None``.

    Raises :class:`WorkflowError` when plans claim *branch* but none of them is a tip,
    which means their ``onto`` chain is cyclic.
    """
    children = _children_by_onto(layout)
    candidates = [candidate for candidate in _stackable_plans(layout) if branch_for_plan(layout, candidate) == branch]
    if not candidates:
        return None
    tips = [candidate for candidate in candidates if candidate.name not in children]
    if not tips:
        names = ", ".join(candidate.name for candidate in candidates)
        msg = f"every plan on branch {branch!r} has a child stacked on it ({names}); the onto chain is cyclic"
        raise WorkflowError(msg)
    return tips[0]


def stack_entries(layout: Layout, plan: Plan) -> list[StackEntry]:
    """Return prompt-rendering entries for *plan*'s stack, base-first, *plan* itself last.

    Empty for an unstacked plan. ``commit_range`` is assembled from the frontmatter
    anchors only (no git calls): member *i* spans ``base(i)..base(i+1)``, the root
    starts at ``origin/main``, the tip ends at ``HEAD``.
    """
    chain = resolve_stack(layout, plan)
    if len(chain) == 1:
        return []
    bases: list[str] = []
    for member in chain[1:]:
        fm = read_plan_frontmatter(layout, member)
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
