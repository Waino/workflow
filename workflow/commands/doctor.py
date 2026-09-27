"""``workflow doctor``: read-only diagnostics for manual recovery.

Reports the resting-state of every clone (canon, bare, planner, builder,
reviewer) and flags plan files duplicated across state directories within a
single repo (the signature of a botched `git mv` transition). Never mutates
anything; exits non-zero when any anomaly is found.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.table import Table

from workflow.agents_md import parse_hash_file
from workflow.exceptions import (
    AgentsMdGenerationError,
    AmbiguousPlanError,
    PlanCollisionError,
    PlanNotFoundError,
    WorkflowError,
)
from workflow.git_helpers import (
    BARE_REMOTE,
    LEGACY_BARE_REMOTE,
    git_branch_exists,
    git_current_branch,
    git_index_lock_present,
    git_is_ancestor,
    git_is_valid_repo,
    git_list_remotes,
    git_remote_url,
    git_rev_parse,
    git_status_porcelain,
    resolve_upstream_default_branch,
)
from workflow.metadata import LEGACY_ROOT_MARKER, ProjectMode
from workflow.models import PlanState
from workflow.names import DOCS_REPO, WorktreeName
from workflow.plan_resolver import find_all_plans_in_worktree, resolve_plan
from workflow.registry import register_script
from workflow.shell import console, error_console
from workflow.stack import BRANCH_PREFIX, branch_for_plan, read_plan_frontmatter

if TYPE_CHECKING:
    from workflow.config import WorkflowConfig
    from workflow.layout import Layout
    from workflow.models import Plan

_ACTIVE_FEATURE_BRANCH_STATES = (PlanState.IMPLEMENTING, PlanState.CHECKING)

# Plan states that occupy a single pipeline slot per (sequence, slug); a plan
# legitimately co-exists with its originating INTENT file and/or REVIEW documents,
# so those are excluded rather than enumerated positively (avoids drift if a new
# pipeline state is added to PlanState).
_PIPELINE_STATES = frozenset(PlanState) - {PlanState.INTENT, PlanState.REVIEW}


@dataclass
class WorktreeCheck:
    """Diagnostic snapshot of a single clone."""

    name: str
    exists: bool
    valid: bool | None = None  # None when exists is False
    branch: str | None = None  # None for the bare repo (no worktree)
    dirty: bool | None = None  # None for the bare repo
    index_lock: bool | None = None  # None when exists is False
    anomalies: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """Informational, non-anomalous observations -- rendered but excluded from the exit code."""
    repo: str | None = None
    """Which repo this row covers; ``None`` in-band (the repo column is omitted there)."""


def _active_branches(layout: Layout) -> set[str]:
    """Return `plan/<name>`/stack branches expected for plans currently in flight."""
    branches: set[str] = set()
    for worktree in WorktreeName:
        if not layout.worktree_root(worktree).is_dir():
            continue
        try:
            plans = find_all_plans_in_worktree(layout, worktree)
        except PlanCollisionError:
            continue
        for plan in plans:
            if plan.state == PlanState.PLANNING:
                branches.add(f"plan/{plan.name}")
            elif plan.state in _ACTIVE_FEATURE_BRANCH_STATES:
                branches.add(branch_for_plan(layout, plan))
    return branches


def _check_bare(bare_path: Path, *, repo: str | None = None) -> WorktreeCheck:
    check = WorktreeCheck(name="bare", exists=bare_path.is_dir(), repo=repo)
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


def _check_staleness(check: WorktreeCheck, repo_path: Path, bare_path: Path, active_branches: set[str]) -> None:
    """Compare *repo_path* against *bare_path* directly: fetch-free, matching doctor's contract."""
    if not bare_path.is_dir():
        return
    bare_main = git_rev_parse(bare_path, "refs/heads/main")
    repo_main = git_rev_parse(repo_path, "refs/heads/main")
    if bare_main is not None and repo_main is not None and bare_main != repo_main:
        check.anomalies.append("main differs from bare (stale or unpushed)")

    branch_on_bare = check.branch in active_branches and git_branch_exists(bare_path, check.branch)
    if branch_on_bare and not git_is_ancestor(bare_path, "refs/heads/main", f"refs/heads/{check.branch}"):
        check.notes.append("not based on bare main; accept will rebase")


def _check_bare_remote(check: WorktreeCheck, repo_path: Path, bare_path: Path) -> None:
    """Flag the pre-migration remote shape.

    Anomalies: a missing ``bare`` remote, or a leftover ``origin`` pointing at the
    project's bare path (the 023.3 migration renames it to ``bare``).
    """
    if git_remote_url(repo_path, BARE_REMOTE) is not None:
        return
    legacy_url = git_remote_url(repo_path, LEGACY_BARE_REMOTE)
    if legacy_url is not None and Path(legacy_url).resolve() == bare_path.resolve():
        check.anomalies.append(f"{LEGACY_BARE_REMOTE!r} remote points at bare; run scripts/migrate_023.py")
    else:
        check.anomalies.append(f"no {BARE_REMOTE!r} remote configured")


def _check_oob_single_remote(check: WorktreeCheck, repo_path: Path, worktree: WorktreeName) -> None:
    """Oob-only: planner/builder/reviewer clones must have exactly one remote (``bare``).

    The load-bearing guarantee that an agent cannot reach any repo's real upstream.
    Canon is exempt: it legitimately keeps ``origin`` alongside ``bare`` for every code
    repo, so `workflow sync` has something to fetch from.
    """
    if worktree == WorktreeName.canon:
        return
    remotes = git_list_remotes(repo_path)
    if remotes != [BARE_REMOTE]:
        check.anomalies.append(f"expected exactly one remote ({BARE_REMOTE!r}), found {remotes}")


def _check_worktree(
    worktree: WorktreeName,
    repo_path: Path,
    *,
    active_branches: set[str],
    bare_path: Path,
    repo: str | None = None,
) -> WorktreeCheck:
    """Check *worktree*'s clone of *repo* (``docs`` in-band), rooted at *repo_path*.

    *repo* is ``None`` in-band; a non-``None`` value both labels the row and turns on the
    oob-only single-remote check (the two only ever come as a pair -- see ``doctor()``).
    """
    check = WorktreeCheck(name=worktree.value, exists=repo_path.is_dir(), repo=repo)
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

    _check_bare_remote(check, repo_path, bare_path)
    if repo is not None:
        _check_oob_single_remote(check, repo_path, worktree)

    check.branch = git_current_branch(repo_path)
    tracked_dirty = bool(git_status_porcelain(repo_path, untracked=False))
    check.dirty = tracked_dirty
    if tracked_dirty:
        check.anomalies.append("uncommitted changes")
    elif git_status_porcelain(repo_path):
        check.notes.append("untracked files present")

    if worktree == WorktreeName.canon and check.branch != "main":
        check.anomalies.append(f"expected 'main', on {check.branch!r}")
    elif (
        worktree in (WorktreeName.builder, WorktreeName.reviewer)
        and check.branch != "main"
        and check.branch not in active_branches
    ):
        check.anomalies.append(f"stray branch {check.branch!r}: no plan is implementing/checking on it")

    _check_staleness(check, repo_path, bare_path, active_branches)
    return check


def _duplicate_plan_anomalies(layout: Layout) -> list[str]:
    """Return one message per plan that appears under more than one state dir in the same repo."""
    anomalies: list[str] = []
    for worktree in WorktreeName:
        if not layout.worktree_root(worktree).is_dir():
            continue
        try:
            plans = find_all_plans_in_worktree(layout, worktree)
        except PlanCollisionError as exc:
            anomalies.append(f"{worktree}: sequence number collision on {exc.sequence_number!r}")
            continue
        by_key: dict[str, list[str]] = {}
        for plan in plans:
            if plan.state not in _PIPELINE_STATES:
                continue
            key = f"{plan.sequence_number_str}:{plan.slug}"
            by_key.setdefault(key, []).append(plan.state.value)
        for key, states in by_key.items():
            if len(states) > 1:
                anomalies.append(f"{worktree}: {key} appears in multiple state dirs: {', '.join(sorted(states))}")
    return anomalies


def _bare_staleness_notes(layout: Layout) -> list[str]:
    """Return one note per code repo whose ``bare`` default branch lags canon's ``origin``.

    Fetch-free like the rest of ``doctor``: reads whatever ``origin`` state canon's own
    last fetch left behind (no live network fetch here), and reads ``bare/<repo>``'s ref
    directly off disk. A note, not an anomaly -- falling behind upstream between
    ``workflow sync`` runs is the expected steady state, not a problem.
    """
    notes: list[str] = []
    for repo in layout.code_repos():
        canon_repo = layout.git_repo_root(WorktreeName.canon, repo)
        bare_repo = layout.bare_repo(repo)
        if not (canon_repo.is_dir() and bare_repo.is_dir()):
            continue
        try:
            default_branch = resolve_upstream_default_branch(canon_repo)
        except WorkflowError:
            continue
        origin_tip = git_rev_parse(canon_repo, f"refs/remotes/origin/{default_branch}")
        bare_tip = git_rev_parse(bare_repo, f"refs/heads/{default_branch}")
        if origin_tip is None or bare_tip is None or origin_tip == bare_tip:
            continue
        if not git_is_ancestor(canon_repo, bare_tip, origin_tip):
            continue  # bare holds commits origin doesn't -- not the "behind upstream" case this note covers
        behind = _commits_between(canon_repo, bare_tip, origin_tip)
        notes.append(f"{repo}: bare is {behind} commit(s) behind upstream ({default_branch}); run workflow sync")
    return notes


def _commits_between(repo_path: Path, old: str, new: str) -> int:
    """Return ``git rev-list --count old..new`` in *repo_path*. Read-only guard query."""
    result = subprocess.run(  # noqa: S603
        ["git", "rev-list", "--count", f"{old}..{new}"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    return int(result.stdout.decode().strip())


def _agents_md_hash_anomalies(layout: Layout) -> list[str]:
    """Oob only: flag a worktree-root AGENTS.md copy that doesn't hash-match the trusted value.

    Four separate copies (one per worktree) make drift detection essential -- an agent
    silently working off different instructions than its siblings has no other signal.
    Fetch-free like the rest of ``doctor``: reads whatever is already on disk.
    """
    if layout.metadata.mode != ProjectMode.oob:
        return []

    hash_file = layout.project_dir / ".agents_md_hash"
    if not hash_file.exists():
        return [f"{hash_file} not found; run workflow agents_md"]
    try:
        trusted = parse_hash_file(hash_file.read_text(encoding="utf-8"))
    except AgentsMdGenerationError as exc:
        return [f"trusted hash file invalid: {exc}"]

    anomalies: list[str] = []
    for worktree in WorktreeName:
        agents_md = layout.worktree_root(worktree) / "AGENTS.md"
        if not agents_md.exists():
            anomalies.append(f"{worktree}: AGENTS.md not found")
            continue
        actual_hash = hashlib.sha256(agents_md.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if actual_hash != trusted.agents_md_sha:
            anomalies.append(f"{worktree}: AGENTS.md hash does not match trusted value; run workflow agents_md")
    return anomalies


def _legacy_marker_anomalies(project_dir: Path) -> list[str]:
    """Return an anomaly if a leftover legacy .workflow_root marker is present."""
    if (project_dir / LEGACY_ROOT_MARKER).exists():
        return ["legacy .workflow_root marker present; run scripts/migrate_023.py"]
    return []


def _branch_exists_anywhere(layout: Layout, branch: str) -> bool:
    """Return whether *branch* exists in bare or in any worktree.

    ponytail: one git process per repo per queried branch; fine for a handful of
    in-flight plans, would want a single `for-each-ref` per repo if that grows.
    """
    bare_path = layout.bare_repo()
    if bare_path.is_dir() and git_branch_exists(bare_path, branch):
        return True
    return any(
        git_branch_exists(layout.git_repo_root(worktree), branch)
        for worktree in WorktreeName
        if layout.worktree_root(worktree).is_dir()
    )


def _stack_anomalies(layout: Layout, config: WorkflowConfig) -> list[str]:
    """Return one message per stack-related anomaly.

    Covers a broken `onto`, an orphaned stack branch, or a `branches` entry pointing at a
    branch that exists nowhere.
    """
    try:
        plans = [p for p in find_all_plans_in_worktree(layout, WorktreeName.canon) if p.state in _PIPELINE_STATES]
    except PlanCollisionError:
        return []  # already reported by _duplicate_plan_anomalies

    anomalies: list[str] = []
    branch_members: dict[str, list[Plan]] = defaultdict(list)
    for plan in plans:
        fm = read_plan_frontmatter(layout, plan)
        branch_members[branch_for_plan(layout, plan)].append(plan)
        if fm is None:
            continue
        if fm.onto is not None:
            try:
                resolve_plan(fm.onto, config=config)
            except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError, WorkflowError):
                anomalies.append(f"{plan.name}: onto={fm.onto!r} does not resolve to a plan")
        repo_branch = fm.branches.get(DOCS_REPO)
        # Only while the plan is in flight: `accept` deletes the shared branch but leaves
        # the frontmatter naming it, which is a record, not an anomaly.
        if (
            repo_branch is not None
            and plan.state in _ACTIVE_FEATURE_BRANCH_STATES
            and not _branch_exists_anywhere(layout, repo_branch.branch)
        ):
            anomalies.append(f"{plan.name}: recorded branch {repo_branch.branch!r} exists nowhere")

    for branch, members in branch_members.items():
        if not branch.startswith(BRANCH_PREFIX) or not _branch_exists_anywhere(layout, branch):
            continue
        if all(member.state in (PlanState.DONE, PlanState.REJECTED) for member in members):
            anomalies.append(f"{branch}: exists but every plan on it is done/rejected")
    return anomalies


def _print_anomaly_section(title: str, messages: list[str]) -> None:
    if not messages:
        return
    console.print(f"[bold]{title}:[/bold]")
    for msg in messages:
        console.print(f"  - {msg}")


def _render_table(checks: list[WorktreeCheck]) -> Table:
    show_repo = any(check.repo is not None for check in checks)
    table = Table(title="Workflow Doctor")
    if show_repo:
        table.add_column("repo")
    table.add_column("worktree")
    table.add_column("exists")
    table.add_column("branch")
    table.add_column("clean")
    table.add_column("lock")
    table.add_column("valid")
    table.add_column("anomalies")
    table.add_column("notes")
    for check in checks:
        row = [check.repo or ""] if show_repo else []
        row += [
            check.name,
            "yes" if check.exists else "no",
            check.branch or "",
            "" if check.dirty is None else ("no" if check.dirty else "yes"),
            "" if check.index_lock is None else ("yes" if check.index_lock else "no"),
            "" if check.valid is None else ("yes" if check.valid else "no"),
            "; ".join(check.anomalies) if check.anomalies else "",
            "; ".join(check.notes) if check.notes else "",
        ]
        table.add_row(*row)
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
    layout = config.layout()
    oob = layout.metadata.mode == ProjectMode.oob

    active_branches = _active_branches(layout)
    checks: list[WorktreeCheck] = []
    for repo in layout.repos():
        bare_path = layout.bare_repo(repo)
        check_repo = repo if oob else None
        checks.append(_check_bare(bare_path, repo=check_repo))
        checks.extend(
            _check_worktree(
                worktree,
                layout.git_repo_root(worktree, repo),
                active_branches=active_branches,
                bare_path=bare_path,
                repo=check_repo,
            )
            for worktree in WorktreeName
        )
    console.print(_render_table(checks))

    duplicate_anomalies = _duplicate_plan_anomalies(layout)
    _print_anomaly_section("Duplicated plan files", duplicate_anomalies)

    stack_anomalies = _stack_anomalies(layout, config)
    _print_anomaly_section("Stack anomalies", stack_anomalies)

    agents_md_anomalies = _agents_md_hash_anomalies(layout)
    _print_anomaly_section("AGENTS.md anomalies", agents_md_anomalies)

    bare_staleness_notes = _bare_staleness_notes(layout)
    _print_anomaly_section("Bare staleness", bare_staleness_notes)

    legacy_marker_anomalies = _legacy_marker_anomalies(project_dir)
    _print_anomaly_section("Project anomalies", legacy_marker_anomalies)

    any_anomaly = (
        any(c.anomalies for c in checks)
        or bool(duplicate_anomalies)
        or bool(stack_anomalies)
        or bool(agents_md_anomalies)
        or bool(legacy_marker_anomalies)
    )
    ctx.exit(1 if any_anomaly else 0)
