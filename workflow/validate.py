"""Validate verification suite.

Implements the pre-merge checks and exposes them via the reusable
``validate`` function.  The CLI command is registered in
``workflow/commands/workflow.py``.

In-band: one repo, four checks, unchanged from before 023.6.  Oob: ``validate``
iterates ``layout.repos()`` in the acting worktree instead of a single repo, and
gains two oob-only checks (``planner-code-commits``, ``branches-frontmatter``).
``file-scope`` and ``git-hooks`` become per-repo (``CheckResult.repo`` set);
``noqa-nosec`` and ``agents-md`` stay single, worktree-level results (``repo=None``)
covering every repo/worktree internally. See ``CHECK_NAMES_IN_BAND``/``CHECK_NAMES_OOB``.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from workflow.agents_md import HashFileV2, parse_hash_file
from workflow.exceptions import AgentsMdGenerationError, WorkflowError
from workflow.git_helpers import (
    git_changed_files_not_in_bare,
    git_check_ref_format,
    git_commits_not_in_bare,
    git_diff_branch,
    git_diff_text_not_in_bare,
    git_fetch,
)
from workflow.metadata import METADATA_FILENAME, ProjectMode
from workflow.models import PlanState
from workflow.names import DOCS_REPO, WorktreeName
from workflow.stack import read_plan_frontmatter

if TYPE_CHECKING:
    from workflow.config import WorkflowConfig
    from workflow.layout import Layout
    from workflow.models import Plan

CHECK_NAMES_IN_BAND: list[str] = ["file-scope", "noqa-nosec", "git-hooks", "agents-md"]
CHECK_NAMES_OOB: list[str] = [
    "file-scope",
    "planner-code-commits",
    "noqa-nosec",
    "git-hooks",
    "agents-md",
    "branches-frontmatter",
]
_BRANCHES_FRONTMATTER_REQUIRED_STATES = frozenset({PlanState.IMPLEMENTING, PlanState.CHECKING})
_OOB_DOCS_ROOT_FILES = frozenset({"body_of_agents.md", "bootstrap_venv.sh"})
"""Hand-written files at the oob docs repo root that only planner/canon may touch."""


def check_names_for_mode(mode: ProjectMode) -> list[str]:
    """Return the fixed check-name list for *mode* (used by dry-run previews and validate())."""
    return CHECK_NAMES_OOB if mode == ProjectMode.oob else CHECK_NAMES_IN_BAND


# ---------------------------------------------------------------------------
# CheckResult model
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    """Result of a single validate verification."""

    check_name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    details: list[str] = []
    repo: str | None = None
    """Which repo this result covers; ``None`` for a worktree-level check (e.g. agents-md)."""


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

    if dry_run:
        return []

    if layout.metadata.mode != ProjectMode.oob:
        return _validate_in_band(layout, config, target_repo)

    try:
        worktree = WorktreeName(target_repo)
    except ValueError:
        # Custom agent name with no matching worktree (e.g. a manual-shell-only agent):
        # fall back to the same single-repo-diff behaviour in-band mode uses, against
        # whatever directory a plain path join resolves to.
        return _validate_in_band(layout, config, target_repo)

    return _validate_oob(layout, config, plan, worktree)


# ---------------------------------------------------------------------------
# In-band validate
# ---------------------------------------------------------------------------


def _validate_in_band(layout: Layout, config: WorkflowConfig, target_repo: WorktreeName | str) -> list[CheckResult]:
    repo_path = _repo_path(layout, target_repo)
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


def _managed_hook_issues(repo_path: Path, config: WorkflowConfig) -> list[str]:
    """Return one message per managed hook that is missing or points at the wrong target."""
    hooks_dir = repo_path / ".git" / "hooks"
    issues: list[str] = []
    for hook_name, hook_source in config.managed_hooks.items():
        hook_path = hooks_dir / hook_name
        expected = (repo_path / hook_source).resolve()
        if not hook_path.is_symlink():
            issues.append(f"{hook_name}: not a symlink (expected {expected})")
        elif hook_path.resolve() != expected:
            issues.append(f"{hook_name}: symlink points to wrong target (expected {expected})")
    return issues


def _unexpected_hook_issues(repo_path: Path, config: WorkflowConfig) -> list[str]:
    """Return one message per executable hook file not accounted for by ``config.managed_hooks``."""
    hooks_dir = repo_path / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return []
    issues = []
    for hook_file in sorted(hooks_dir.iterdir()):
        if hook_file.suffix == ".sample" or hook_file.name in config.managed_hooks:
            continue
        if hook_file.is_file() and os.access(hook_file, os.X_OK):
            issues.append(f"unexpected executable hook: {hook_file.name}")
    return issues


def _check_git_hooks(
    repo_path: Path,
    config: WorkflowConfig,
    *,
    repo: str | None = None,
    downgrade_unexpected: bool = False,
) -> CheckResult:
    """Check the managed hooks are correctly symlinked, and flag any unmanaged executable hook.

    An unexpected executable hook is a hard failure unless *downgrade_unexpected*
    (used for code repos, where a husky/pre-commit-framework hook is routine).
    """
    fail_issues = _managed_hook_issues(repo_path, config)
    unexpected_issues = _unexpected_hook_issues(repo_path, config)
    warn_issues: list[str] = []
    if downgrade_unexpected:
        warn_issues = unexpected_issues
    else:
        fail_issues += unexpected_issues

    if fail_issues:
        return CheckResult(
            check_name="git-hooks",
            repo=repo,
            status="fail",
            message=f"{len(fail_issues)} git hook issue(s)",
            details=[*fail_issues, *warn_issues],
        )
    if warn_issues:
        return CheckResult(
            check_name="git-hooks",
            repo=repo,
            status="warn",
            message=f"{len(warn_issues)} git hook issue(s)",
            details=warn_issues,
        )
    return CheckResult(check_name="git-hooks", repo=repo, status="pass", message="Git hooks correctly configured")


def _stale_hash_inputs(project_dir: Path, trusted: HashFileV2) -> list[str]:
    """Return which of config.yaml/.workflow.json no longer match *trusted*'s recorded hashes."""
    config_yaml = project_dir / "config.yaml"
    config_yaml_sha = hashlib.sha256(config_yaml.read_bytes()).hexdigest() if config_yaml.exists() else ""
    workflow_json = project_dir / METADATA_FILENAME
    workflow_json_sha = hashlib.sha256(workflow_json.read_bytes()).hexdigest() if workflow_json.exists() else ""
    stale: list[str] = []
    if config_yaml_sha != trusted.config_yaml_sha:
        stale.append("config.yaml")
    if workflow_json_sha != trusted.workflow_json_sha:
        stale.append(f"{METADATA_FILENAME} (e.g. add-repo)")
    return stale


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
        return CheckResult(
            check_name="agents-md",
            status="fail",
            message=f"Hash file invalid: {exc} Run `workflow agents_md` to regenerate it.",
        )

    actual_hash = hashlib.sha256(agents_md.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    agents_md_modified = "AGENTS.md" in changed_files

    if actual_hash != trusted.agents_md_sha:
        stale_inputs = _stale_hash_inputs(project_dir, trusted)
        if stale_inputs:
            verb = "has" if len(stale_inputs) == 1 else "have"
            message = (
                f"AGENTS.md hash does not match trusted value; {' and '.join(stale_inputs)} {verb} changed "
                "since AGENTS.md was last generated — run `workflow agents_md`."
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


# ---------------------------------------------------------------------------
# Oob validate
# ---------------------------------------------------------------------------


def _validate_oob(layout: Layout, config: WorkflowConfig, plan: Plan, worktree: WorktreeName) -> list[CheckResult]:
    results: list[CheckResult] = []
    all_diffs: list[str] = []

    for repo in layout.repos():
        repo_path = layout.git_repo_root(worktree, repo)
        try:
            git_fetch(repo_path)
        except subprocess.CalledProcessError as exc:
            msg = f"git fetch failed in {repo_path}: {exc}"
            raise WorkflowError(msg) from exc

        if repo == DOCS_REPO:
            full_diff = _read_branch_diff(repo_path)
            changed_files = [line for line in _read_branch_diff(repo_path, "--name-only").splitlines() if line]
        else:
            full_diff = git_diff_text_not_in_bare(repo_path)
            changed_files = git_changed_files_not_in_bare(repo_path)
        all_diffs.append(full_diff)

        results.append(_check_file_scope_oob(changed_files, worktree, repo))
        if _repo_runs_hook(layout, repo):
            results.append(_check_git_hooks(repo_path, config, repo=repo, downgrade_unexpected=repo != DOCS_REPO))

    results.append(_check_noqa_nosec("\n".join(all_diffs)))
    results.append(_check_agents_md_oob(layout))
    results.append(_check_planner_code_commits(layout))
    results.append(_check_branches_frontmatter(layout, plan))
    return results


def _repo_runs_hook(layout: Layout, repo: str) -> bool:
    """Return whether *repo* has the pre-commit hook installed (docs always does)."""
    if repo == DOCS_REPO:
        return True
    return next((r.run_hook for r in layout.metadata.code_repos if r.name == repo), True)


def _check_file_scope_oob(changed_files: list[str], worktree: WorktreeName, repo: str) -> CheckResult:
    if repo != DOCS_REPO:
        # Unrestricted: the tool cannot know a third-party repo's layout. The plan's
        # frontmatter and human review are the scope guard here (see Risk 2); a planner
        # commit specifically is instead caught by planner-code-commits.
        return CheckResult(check_name="file-scope", repo=repo, status="pass", message="Unrestricted for code repos")

    violations = [f for f in changed_files if not _is_allowed_oob_docs(f, worktree)]
    if violations:
        return CheckResult(
            check_name="file-scope",
            repo=repo,
            status="fail",
            message=f"{len(violations)} file(s) outside allowed scope for {worktree!r}",
            details=violations,
        )
    return CheckResult(check_name="file-scope", repo=repo, status="pass", message="All changed files within scope")


def _is_allowed_oob_docs(file_path: str, worktree: WorktreeName) -> bool:
    """Return True if *file_path* (relative to the oob docs repo root) may be changed by *worktree*.

    The oob counterpart to :func:`_is_allowed`: plans live at ``plans/...`` (not
    ``docs/plans/...``, since the repo root here already is the ``docs`` subdirectory),
    and ``body_of_agents.md``/``bootstrap_venv.sh`` at the repo root are reserved for
    planner/canon -- builder/reviewer must not edit their own instructions or the script
    that builds their env.
    """
    parts = Path(file_path).parts
    under_plans = parts[:1] == ("plans",)
    is_intent = under_plans and parts[1:2] == ("intent",)
    is_review = under_plans and parts[1:2] == ("reviews",)
    is_reserved_root_file = file_path in _OOB_DOCS_ROOT_FILES

    if worktree == WorktreeName.canon:
        return True
    if worktree == WorktreeName.planner:
        return (under_plans and not is_review) or is_reserved_root_file
    if worktree == WorktreeName.builder:
        return not is_intent and not is_review and not is_reserved_root_file
    if worktree == WorktreeName.reviewer:
        return not is_intent and not is_reserved_root_file
    return True  # unknown worktree: allow


def _check_agents_md_oob(layout: Layout) -> CheckResult:
    """Verify every worktree-root AGENTS.md copy matches the trusted hash.

    No "modified on this branch" arm here: AGENTS.md is an unversioned build artifact
    in oob, so editing it isn't a git diff at all -- editing its hand-written source,
    ``body_of_agents.md``, is instead caught by ``file-scope``.
    """
    hash_file = layout.project_dir / ".agents_md_hash"
    if not hash_file.exists():
        msg = f"Trusted hash file not found: {hash_file}"
        raise WorkflowError(msg)

    try:
        trusted = parse_hash_file(hash_file.read_text(encoding="utf-8"))
    except AgentsMdGenerationError as exc:
        return CheckResult(
            check_name="agents-md",
            status="fail",
            message=f"Hash file invalid: {exc} Run `workflow agents_md` to regenerate it.",
        )

    mismatched: list[str] = []
    for worktree in WorktreeName:
        agents_md = layout.worktree_root(worktree) / "AGENTS.md"
        if not agents_md.exists():
            mismatched.append(f"{worktree}: AGENTS.md not found")
            continue
        actual_hash = hashlib.sha256(agents_md.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if actual_hash != trusted.agents_md_sha:
            mismatched.append(f"{worktree}: hash does not match trusted value")

    if mismatched:
        stale_inputs = _stale_hash_inputs(layout.project_dir, trusted)
        hint = f" ({' and '.join(stale_inputs)} changed since it was last generated)" if stale_inputs else ""
        return CheckResult(
            check_name="agents-md",
            status="fail",
            message=f"{len(mismatched)} AGENTS.md cop{'y' if len(mismatched) == 1 else 'ies'} out of date{hint} "
            "— run `workflow agents_md`.",
            details=mismatched,
        )
    return CheckResult(check_name="agents-md", status="pass", message="AGENTS.md integrity verified in every worktree")


def _check_planner_code_commits(layout: Layout) -> CheckResult:
    """Fail when the planner worktree has committed to a code repo (it may only explore/check out).

    Fixed check: always inspects the planner worktree specifically, regardless of which
    worktree/plan is actually being validated in this call (see the module docstring).
    """
    dirty: list[str] = []
    for repo in layout.code_repos():
        repo_path = layout.git_repo_root(WorktreeName.planner, repo)
        if not repo_path.is_dir():
            continue
        try:
            git_fetch(repo_path)
        except subprocess.CalledProcessError as exc:
            msg = f"git fetch failed in {repo_path}: {exc}"
            raise WorkflowError(msg) from exc
        count = git_commits_not_in_bare(repo_path)
        if count:
            dirty.append(f"{repo}: {count} commit(s) not in bare")

    if dirty:
        return CheckResult(
            check_name="planner-code-commits",
            status="fail",
            message="planner has committed to a code repo",
            details=dirty,
        )
    return CheckResult(
        check_name="planner-code-commits",
        status="pass",
        message="planner has not committed to any code repo",
    )


def _check_branches_frontmatter(layout: Layout, plan: Plan) -> CheckResult:
    """Require a ``branches`` frontmatter entry for every code repo, on an in-flight plan.

    Only checked while the plan is ``implementing``/``checking``; nothing else is
    validated (no existence check on the named branches/bases).
    """
    if plan.state not in _BRANCHES_FRONTMATTER_REQUIRED_STATES:
        return CheckResult(
            check_name="branches-frontmatter",
            status="pass",
            message=f"not required in {plan.state.value} state",
        )

    fm = read_plan_frontmatter(layout, plan)
    if fm is None or not fm.branches:
        return CheckResult(
            check_name="branches-frontmatter",
            status="fail",
            message="plan file has no branches frontmatter block",
        )

    issues = [f"missing entry for {repo!r}" for repo in layout.code_repos() if repo not in fm.branches]
    for repo, repo_branch in fm.branches.items():
        if repo_branch is not None and not git_check_ref_format(repo_branch.branch):
            issues.append(f"{repo}: {repo_branch.branch!r} is not a valid branch name")

    if issues:
        return CheckResult(
            check_name="branches-frontmatter",
            status="fail",
            message=f"{len(issues)} branches frontmatter issue(s)",
            details=issues,
        )
    return CheckResult(
        check_name="branches-frontmatter",
        status="pass",
        message="branches frontmatter present for every code repo",
    )
