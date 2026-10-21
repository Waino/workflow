"""Workflow script commands: project setup, state transitions, and git operations."""

from __future__ import annotations

import importlib.resources
import os
import re
import shlex
import subprocess
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import click
import jinja2
from pydantic import BaseModel, ConfigDict, ValidationError
from rich.table import Table

from workflow.audit import write_audit_log
from workflow.commands.agents_md import (
    BODY_OF_AGENTS_FILENAME,
    generate_and_write_agents_md,
    prepare_agents_md_update,
)
from workflow.config import AgentConfig, WorkflowConfig
from workflow.config import ScriptCommand as _ScriptCommand
from workflow.exceptions import (
    AgentsMdGenerationError,
    AmbiguousPlanError,
    PlanCollisionError,
    PlanNotFoundError,
    WorkflowError,
)
from workflow.frontmatter import PlanFrontmatter, RepoBranch, render_frontmatter, write_frontmatter
from workflow.git_helpers import (
    BARE_REMOTE,
    HOOK_FILENAME,
    TransferResult,
    branch_needs_rebase,
    delete_merged_branch,
    fetch_code_repos_best_effort,
    git_add,
    git_branch_exists,
    git_checkout,
    git_commit_hash,
    git_commit_if_changed,
    git_commits_ahead_of_remote,
    git_commits_not_in_bare,
    git_config_get,
    git_current_branch,
    git_fetch,
    git_fetch_remote,
    git_file_mode_at_ref,
    git_index_lock_present,
    git_is_ancestor,
    git_is_valid_repo,
    git_merge,
    git_path_exists_at_ref,
    git_pull,
    git_push,
    git_push_branch,
    git_push_refspec,
    git_ref_exists_remote,
    git_remote_url,
    git_rev_parse,
    git_status,
    git_status_porcelain,
    park_on_main,
    pre_commit_hook_present,
    refresh_main,
    resolve_default_branch,
    transfer_branch_to_main,
)
from workflow.layout import Layout
from workflow.metadata import (
    METADATA_FILENAME,
    CodeRepoMeta,
    ProjectMetadata,
    ProjectMode,
    read_metadata,
    write_metadata,
)
from workflow.models import AuditLogEntry, Plan, PlanState
from workflow.names import DOCS_REPO, AgentName, WorktreeName
from workflow.next_step import find_command, format_next_step
from workflow.plan_resolver import SourcesDisagreeError, next_intent_number, resolve_plan
from workflow.registry import register_script
from workflow.shell import console, error_console, print_next_step, print_workflow_error, run_command
from workflow.stack import (
    BRANCH_PREFIX,
    branch_for_plan,
    find_stack_member,
    read_plan_frontmatter,
    resolve_stack,
    stack_child,
    tip_plan_for_branch,
)
from workflow.state import locate_plan_file, transition_plan
from workflow.steps import ResumePlan, require
from workflow.tmux_dispatch import (
    assert_in_workflow_session,
    attach_workflow_session,
    create_workflow_session,
    is_inside_workflow_session,
    kill_workflow_session,
    rotate_control_pane,
    workflow_session_exists,
)
from workflow.validate import CheckResult, check_names_for_mode, infer_target_worktree
from workflow.validate import validate as run_validate

# Hard state constraints for script commands
# Each command also accepts its own transition's *target* state, so a plan already
# moved by a prior (partially-failed) run of the same command can be resumed.
BOOTSTRAP_VENV_FILENAME = "bootstrap_venv.sh"
"""Executable venv-bootstrap script, version controlled in the docs repo (oob) or its ``docs/`` dir (in-band)."""
IN_BAND_BOOTSTRAP_PATH = f"docs/{BOOTSTRAP_VENV_FILENAME}"  # path inside the in-band project repo
_GIT_EXECUTABLE_MODE = "100755"

_EXPECTED_STATES_FOR: dict[str, set[PlanState]] = {
    "todo": {PlanState.PLANNING, PlanState.TODO},
    "implement": {PlanState.PLANNING, PlanState.TODO, PlanState.IMPLEMENTING},
    "check": {PlanState.IMPLEMENTING, PlanState.CHECKING},
    "accept": {PlanState.CHECKING, PlanState.DONE},
    "reject": set(PlanState) - {PlanState.REVIEW},
}


def _enforce_expected_state(plan: Plan, command_name: str) -> None:
    expected = _EXPECTED_STATES_FOR.get(command_name)
    if expected is None or plan.state in expected:
        return
    states = sorted(s.value for s in expected)
    msg = f"{command_name!r} expects plan in {states}, got {plan.state.value!r}."
    raise WorkflowError(msg)


def _write_script_audit_log(  # noqa: PLR0913
    config: WorkflowConfig,
    command: str,
    *,
    dry_run: bool,
    input_plan: str | None = None,
    output_plans: list[str] | None = None,
    stacked_on: list[str] | None = None,
    next_step: str | None = None,
) -> None:
    if dry_run:
        return
    project_dir = config.require_project_dir()
    write_audit_log(
        project_dir,
        AuditLogEntry(
            timestamp=datetime.now(UTC),
            actor="human",
            command=command,
            input_plan=input_plan,
            output_plans=output_plans or [],
            stacked_on=stacked_on or [],
            next_step=next_step,
        ),
        dry_run=dry_run,
    )


def _require_is_stack_tip(layout: Layout, plan: Plan) -> None:
    """Refuse to operate on a mid-stack plan; name the tip to use instead."""
    child = stack_child(layout, plan)
    if child is not None:
        msg = f"{plan.name} has {child.name} stacked on it; run this command on {child.name} instead."
        raise WorkflowError(msg)


def _stack_transition_commit_message(stack: list[Plan], target_state: PlanState) -> str:
    if len(stack) == 1:
        return _transition_commit_message(stack[0], target_state)
    names = ", ".join(member.name for member in stack)
    return f"Transition {names} to {target_state.value}"


def _transition_stack_members(  # noqa: PLR0913
    stack: list[Plan],
    target_state: PlanState,
    layout: Layout,
    *,
    worktree_override: WorktreeName,
    commit_worktree: WorktreeName,
    dry_run: bool,
) -> list[Plan]:
    """Transition every stack member not already in *target_state*; one commit for all.

    Mirrors :func:`_transition_if_needed`'s per-plan idempotent skip, but batches the
    commit across the whole stack so a cascade is one commit, one push (Risk 2: the
    same *stack* list drives both classification and execution).
    """
    new_members: list[Plan] = []
    for member in stack:
        target_rel = _plan_path_in_state(layout, worktree_override, target_state, member)
        if (layout.project_dir / target_rel).exists():
            new_members.append(member.model_copy(update={"state": target_state, "path": target_rel}))
        else:
            new_members.append(
                transition_plan(
                    member,
                    target_state,
                    layout,
                    worktree_override=worktree_override,
                    dry_run=dry_run,
                ),
            )
    git_commit_if_changed(
        layout.git_repo_root(commit_worktree),
        _stack_transition_commit_message(stack, target_state),
        dry_run=dry_run,
        no_verify=True,
    )
    return new_members


def _print_stack_note(branch: str, stack: list[Plan]) -> None:
    """Print the branch and the stack members touched, when *stack* has more than one member."""
    if len(stack) <= 1:
        return
    names = ", ".join(member.name for member in stack)
    console.print(f"[bold]Stack on {branch}:[/bold] {names}")


def _command_next_step(config: WorkflowConfig, state: PlanState, command_name: str, plan_name: str) -> str | None:
    """Return the next-step text configured for *command_name* in *state*, or ``None``."""
    command = find_command(config, state, command_name)
    return None if command is None else format_next_step(config, command, plan_name)


def _open_in_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)], check=True)  # noqa: S603


def _run_pre_commit_hook(config: WorkflowConfig, worktree: str, *, dry_run: bool, repo: str | None = None) -> None:
    """Run the pre-commit hook in *worktree*; *repo* selects an oob repo (``--repo`` on the docker script)."""
    project_dir = config.require_project_dir()
    project_name = config.project_name or project_dir.name
    script = _find_docker_script(project_dir)
    run_command(_hook_command(script, project_name, project_dir, worktree, repo=repo), dry_run=dry_run)


def _hook_enabled(layout: Layout, repo: str) -> bool:
    """Whether the pre-commit hook runs for *repo*: docs only if it ships a hook, code repos per ``run_hook``."""
    if repo == DOCS_REPO:
        # Judged on bare's main, not the agent's tree, so a branch deleting the hook cannot switch the gate off.
        return git_path_exists_at_ref(layout.bare_repo(DOCS_REPO), "main", HOOK_FILENAME)
    return layout.metadata.runs_hook(repo)


def _skip_hook_message(repo: str) -> str:
    reason = "no hook on main" if repo == DOCS_REPO else "run_hook: false"
    return f"[dim]skipping pre-commit hook for {repo} ({reason})[/dim]"


def _run_docs_hook(config: WorkflowConfig, layout: Layout, worktree: WorktreeName, *, dry_run: bool) -> None:
    """Run the docs repo's hook in *worktree* (oob: skipped when docs ships none)."""
    if layout.metadata.mode != ProjectMode.oob:
        _run_pre_commit_hook(config, worktree, dry_run=dry_run)
    elif _hook_enabled(layout, DOCS_REPO):
        _run_pre_commit_hook(config, worktree, dry_run=dry_run, repo=DOCS_REPO)
    else:
        console.print(_skip_hook_message(DOCS_REPO))


def _plan_path_in_state(layout: Layout, worktree: WorktreeName, state: PlanState, plan: Plan) -> Path:
    """Return where *plan* lives in *state* inside *worktree*, relative to the project dir."""
    return (layout.plans_root(worktree) / state.value / plan.path.name).relative_to(layout.project_dir)


def _transition_commit_message(plan: Plan, target_state: PlanState) -> str:
    return f"Transition {plan.name} to {target_state.value}"


def _rotate_best_effort(config: WorkflowConfig, target_window: str, *, dry_run: bool) -> None:
    """Rotate the control pane, downgrading tmux failures to a warning.

    Used by ``implement``/``check``, where the cross-repo git handoff has already
    committed/pushed, so a cosmetic tmux failure must not fail the command.
    """
    try:
        rotate_control_pane(config, target_window, dry_run=dry_run)
    except WorkflowError as exc:
        error_console.print(f"[yellow]tmux rotation skipped:[/yellow] {exc}")


def _manual_shell_repo(config: WorkflowConfig) -> WorktreeName | None:
    """Return the repo backing the manual shell window, or ``None`` if no agent sets it."""
    for agent in config.agents:
        if agent.manual_shell and agent.name in WorktreeName.__members__:
            return WorktreeName(agent.name)
    return None


def _hook_symlink_ok(repo_path: Path) -> bool:
    """Return whether the pre-commit hook symlink exists and points at the shared hook script."""
    hook = repo_path / ".git" / "hooks" / "pre-commit"
    return hook.is_symlink() and str(hook.readlink()) == "../../.git-pre-commit-hook"


def _ensure_pre_commit_hook(repo_path: Path, *, dry_run: bool) -> None:
    """Symlink .git/hooks/pre-commit to the shared hook script, replacing a broken link if needed."""
    if _hook_symlink_ok(repo_path):
        return
    hook = repo_path / ".git" / "hooks" / "pre-commit"
    if not dry_run and (hook.is_symlink() or hook.exists()):
        hook.unlink()
    run_command(["ln", "-s", "../../.git-pre-commit-hook", ".git/hooks/pre-commit"], cwd=repo_path, dry_run=dry_run)


def _ensure_push_config(repo_path: Path, *, dry_run: bool) -> None:
    """Set push.autoSetupRemote/push.default in *repo_path*, only touching settings that need it."""
    if git_config_get(repo_path, "push.autoSetupRemote") != "true":
        run_command(["git", "config", "push.autoSetupRemote", "true"], cwd=repo_path, dry_run=dry_run)
    if git_config_get(repo_path, "push.default") != "current":
        run_command(["git", "config", "push.default", "current"], cwd=repo_path, dry_run=dry_run)


def _push_config_ok(repo_path: Path) -> bool:
    return (
        git_config_get(repo_path, "push.autoSetupRemote") == "true"
        and git_config_get(repo_path, "push.default") == "current"
    )


def _agent_push_config_ok(repo_path: Path) -> bool:
    """Return whether push config and the pre-commit hook are already set up in *repo_path*.

    In-band only: the single canon/agent repo always carries ``.git-pre-commit-hook``
    (enforced at init preconditions), so the symlink is mandatory here.
    """
    return _push_config_ok(repo_path) and _hook_symlink_ok(repo_path)


def _ensure_agent_push_config(repo_path: Path, *, dry_run: bool) -> None:
    """Configure push behaviour and install the pre-commit hook symlink in *repo_path*.

    Push config lets feature branches push to a same-named remote branch without
    requiring a pre-existing upstream. Each setting is only (re)applied when not
    already correct, so this is safe to call on every ``init`` run.
    """
    if dry_run and not repo_path.exists():
        error_console.print(f"[yellow]_ensure_agent_push_config: repo {repo_path} does not exist (yet)[/yellow]")
        return
    _ensure_push_config(repo_path, dry_run=dry_run)
    _ensure_pre_commit_hook(repo_path, dry_run=dry_run)


def _wants_hook_symlink(repo_path: Path, *, run_hook: bool) -> bool:
    return run_hook and (repo_path / HOOK_FILENAME).is_file()


def _agent_repo_configure_ok(repo_path: Path, *, run_hook: bool) -> bool:
    """Like :func:`_agent_push_config_ok`, but the hook symlink is optional (third-party repos).

    Used for every repo in oob mode (docs included): unlike in-band's single combined
    repo, none of them are guaranteed to ship ``.git-pre-commit-hook``, and ``--skip-hook``
    (``run_hook=False``) opts a repo out of the symlink.
    """
    hook_ok = not _wants_hook_symlink(repo_path, run_hook=run_hook) or _hook_symlink_ok(repo_path)
    return _push_config_ok(repo_path) and hook_ok


def _ensure_agent_repo_configure(repo_path: Path, *, run_hook: bool, dry_run: bool) -> None:
    """Like :func:`_ensure_agent_push_config`, but only install the hook symlink if wanted and present."""
    if dry_run and not repo_path.exists():
        error_console.print(f"[yellow]_ensure_agent_repo_configure: repo {repo_path} does not exist (yet)[/yellow]")
        return
    _ensure_push_config(repo_path, dry_run=dry_run)
    if _wants_hook_symlink(repo_path, run_hook=run_hook):
        _ensure_pre_commit_hook(repo_path, dry_run=dry_run)


def _wire_canon_to_bare(canon_repo: Path, bare_path: Path, *, track: bool, dry_run: bool) -> None:
    """Add the ``bare`` remote in canon (pointing at bare) and push HEAD.

    *track* also makes ``bare`` canon's upstream and push default: right for the docs repo,
    wrong for an oob code repo, which keeps tracking the operator's own upstream.
    """
    if git_remote_url(canon_repo, BARE_REMOTE) is None:
        run_command(["git", "remote", "add", BARE_REMOTE, str(bare_path)], cwd=canon_repo, dry_run=dry_run)
    if track:
        _ensure_push_config(canon_repo, dry_run=dry_run)
        run_command(["git", "push", "-u", BARE_REMOTE, "HEAD"], cwd=canon_repo, dry_run=dry_run)
    else:
        run_command(["git", "push", BARE_REMOTE, "HEAD"], cwd=canon_repo, dry_run=dry_run)


# Step-name constants: the join key between _classify_init_steps and _run_init_steps.
# In-band only ever has one repo (docs), so its steps keep their original, repo-less names;
# oob has 2+ repos and every step name embeds the repo (and, where relevant, the agent).
_STEP_AGENTS_MD = "AGENTS.md committed"
_STEP_AGENTS_MD_OOB = "AGENTS.md generated"
_STEP_BARE_CLONE = "bare clone"
_STEP_WIRE_CANON = "wire canon to bare"
_STEP_MARKER = "project metadata"


def _is_oob(layout: Layout) -> bool:
    return layout.metadata.mode == ProjectMode.oob


def _agents_md_step_name(layout: Layout) -> str:
    return _STEP_AGENTS_MD_OOB if _is_oob(layout) else _STEP_AGENTS_MD


def _bare_clone_step_name(layout: Layout, repo: str) -> str:
    return f"bare clone {repo}" if _is_oob(layout) else _STEP_BARE_CLONE


def _wire_canon_step_name(layout: Layout, repo: str) -> str:
    return f"wire canon/{repo} to bare" if _is_oob(layout) else _STEP_WIRE_CANON


def _clone_step_name(layout: Layout, agent_name: str, repo: str) -> str:
    return f"clone {agent_name}/{repo}" if _is_oob(layout) else f"clone {agent_name}"


def _configure_step_name(layout: Layout, agent_name: str, repo: str) -> str:
    return f"configure {agent_name}/{repo}" if _is_oob(layout) else f"configure {agent_name}"


def _classify_agents_md_step(resume: ResumePlan, config: WorkflowConfig, layout: Layout) -> None:
    update = prepare_agents_md_update(
        config.model_copy(update={"project_dir": layout.project_dir, "metadata": layout.metadata}),
    )
    if update.agents_md_unchanged:
        resume.satisfied(_agents_md_step_name(layout), "already up to date")
    else:
        resume.will_run(_agents_md_step_name(layout))


def _run_agents_md_step(
    resume: ResumePlan,
    config: WorkflowConfig,
    layout: Layout,
    canon_repo: Path,
    *,
    dry_run: bool,
) -> None:
    if not resume.should_run(_agents_md_step_name(layout)) or dry_run:
        return
    try:
        generate_and_write_agents_md(
            config.model_copy(update={"project_dir": layout.project_dir, "metadata": layout.metadata}),
        )
        if not _is_oob(layout):
            git_add(canon_repo, Path("AGENTS.md"), dry_run=False)
            # no_verify: at init time the pre-commit hook would run pytest before the
            # workflow package is even installed in canon; bypassing is required here.
            git_commit_if_changed(canon_repo, "chore: generate initial AGENTS.md", dry_run=False, no_verify=True)
        # Oob: nothing to commit here (AGENTS.md is unversioned in every worktree);
        # the hand-written body is committed by the planner like any other docs change.
    except AgentsMdGenerationError as exc:
        raise WorkflowError(str(exc)) from exc


def _dir_needs_clone(path: Path) -> bool:
    """Return whether *path* still needs to be created via ``git clone`` (missing or empty).

    ``git clone`` tolerates cloning into an existing but empty directory, so an
    empty directory is equivalent to "not yet cloned", not a rough partial state.
    """
    return not path.exists() or not any(path.iterdir())


def _relative_to_project(layout: Layout, path: Path) -> str:
    """Render *path* relative to the project root, for ``git clone`` argv readability."""
    return str(path.relative_to(layout.project_dir))


def _classify_bare_clone_for_repo(resume: ResumePlan, layout: Layout, repo: str, step: str) -> None:
    if _dir_needs_clone(layout.bare_repo(repo)):
        resume.will_run(step)
    else:
        resume.satisfied(step, "bare is a valid clone of canon")


def _classify_bare_clone_steps(resume: ResumePlan, layout: Layout) -> None:
    for repo in layout.repos():
        _classify_bare_clone_for_repo(resume, layout, repo, _bare_clone_step_name(layout, repo))


def _run_bare_clone_for_repo(layout: Layout, repo: str, *, dry_run: bool) -> None:
    bare_path = layout.bare_repo(repo)
    if not dry_run:
        bare_path.parent.mkdir(parents=True, exist_ok=True)
    source = _relative_to_project(layout, layout.git_repo_root(WorktreeName.canon, repo))
    target = _relative_to_project(layout, bare_path)
    run_command(["git", "clone", "--bare", source, target], cwd=layout.project_dir, dry_run=dry_run)


def _classify_agent_clone_for_repo(resume: ResumePlan, layout: Layout, agent_name: str, repo: str, step: str) -> None:
    agent_repo = layout.git_repo_root(WorktreeName(agent_name), repo)
    bare_path = layout.bare_repo(repo)
    if _dir_needs_clone(agent_repo):
        resume.will_run(step)
    elif git_is_valid_repo(agent_repo) and _remote_points_at(agent_repo, bare_path):
        resume.needs_continue(step, "clone exists; pass --continue to keep it as-is")
    else:
        msg = f"{agent_repo} exists but is not a valid clone of bare; remove it or fix it."
        raise WorkflowError(msg)


def _remote_points_at(repo_path: Path, bare_path: Path) -> bool:
    url = git_remote_url(repo_path, BARE_REMOTE)
    return url is not None and Path(url).resolve() == bare_path.resolve()


def _classify_agent_clone_steps(resume: ResumePlan, config: WorkflowConfig, layout: Layout) -> None:
    for agent in config.agents:
        for repo in layout.repos():
            _classify_agent_clone_for_repo(resume, layout, agent.name, repo, _clone_step_name(layout, agent.name, repo))


def _run_agent_clone_for_repo(layout: Layout, agent_name: str, repo: str, *, dry_run: bool) -> None:
    agent_repo = layout.git_repo_root(WorktreeName(agent_name), repo)
    if not _dir_needs_clone(agent_repo):
        return
    if not dry_run:
        agent_repo.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "git",
            "clone",
            "-o",
            BARE_REMOTE,
            _relative_to_project(layout, layout.bare_repo(repo)),
            _relative_to_project(layout, agent_repo),
        ],
        cwd=layout.project_dir,
        dry_run=dry_run,
    )


def _classify_agent_configure_for_repo(  # noqa: PLR0913
    resume: ResumePlan,
    layout: Layout,
    agent_name: str,
    repo: str,
    step: str,
    *,
    hook_optional: bool,
) -> None:
    agent_repo = layout.git_repo_root(WorktreeName(agent_name), repo)
    if not agent_repo.exists():
        configure_ok = False
    elif hook_optional:
        configure_ok = _agent_repo_configure_ok(agent_repo, run_hook=layout.metadata.runs_hook(repo))
    else:
        configure_ok = _agent_push_config_ok(agent_repo)
    if configure_ok:
        resume.satisfied(step, "push config and pre-commit hook already set up")
    else:
        resume.will_run(step)


def _classify_agent_configure_steps(resume: ResumePlan, config: WorkflowConfig, layout: Layout) -> None:
    oob = _is_oob(layout)
    for agent in config.agents:
        for repo in layout.repos():
            _classify_agent_configure_for_repo(
                resume,
                layout,
                agent.name,
                repo,
                _configure_step_name(layout, agent.name, repo),
                hook_optional=oob,
            )


def _run_agent_configure_for_repo(
    layout: Layout,
    agent_name: str,
    repo: str,
    *,
    hook_optional: bool,
    dry_run: bool,
) -> None:
    agent_repo = layout.git_repo_root(WorktreeName(agent_name), repo)
    if hook_optional:
        _ensure_agent_repo_configure(agent_repo, run_hook=layout.metadata.runs_hook(repo), dry_run=dry_run)
    else:
        _ensure_agent_push_config(agent_repo, dry_run=dry_run)


def _classify_wire_canon_for_repo(resume: ResumePlan, layout: Layout, repo: str, step: str) -> None:
    canon_repo = layout.git_repo_root(WorktreeName.canon, repo)
    bare_path = layout.bare_repo(repo)
    bare_url = git_remote_url(canon_repo, BARE_REMOTE)
    if bare_url is not None and Path(bare_url).resolve() != bare_path.resolve():
        msg = f"{canon_repo} '{BARE_REMOTE}' remote points at {bare_url!r}, expected {bare_path}; fix or remove it."
        raise WorkflowError(msg)
    if bare_url is not None:
        resume.satisfied(step, f"{BARE_REMOTE} already points at bare")
    else:
        resume.will_run(step)


def _classify_wire_canon_steps(resume: ResumePlan, layout: Layout) -> None:
    for repo in layout.repos():
        _classify_wire_canon_for_repo(resume, layout, repo, _wire_canon_step_name(layout, repo))


def _run_wire_canon_for_repo(layout: Layout, repo: str, *, dry_run: bool) -> None:
    canon_repo = layout.git_repo_root(WorktreeName.canon, repo)
    _wire_canon_to_bare(canon_repo, layout.bare_repo(repo), track=repo == DOCS_REPO, dry_run=dry_run)


def _classify_marker_step(resume: ResumePlan, project_dir: Path) -> None:
    # A malformed file never gets here: _resolve_init_metadata has already failed on it.
    if (project_dir / METADATA_FILENAME).exists():
        resume.satisfied(_STEP_MARKER, "metadata file exists")
    else:
        resume.will_run(_STEP_MARKER)


def _classify_venv_steps(resume: ResumePlan, config: WorkflowConfig, layout: Layout) -> None:
    worktree_names = [agent.name for agent in config.agents] + [WorktreeName.canon]
    for worktree in worktree_names:
        venv_dir = layout.venv_root(WorktreeName(worktree)) / ".venv"
        step = f"venv {worktree}"
        if venv_dir.is_dir():
            resume.needs_continue(step, "exists; pass --continue to recreate (destructive)")
        else:
            resume.will_run(step)


def _classify_init_steps(resume: ResumePlan, config: WorkflowConfig, layout: Layout) -> None:
    """Populate *resume* with a classification for every init step (read-only)."""
    _classify_agents_md_step(resume, config, layout)
    _classify_bare_clone_steps(resume, layout)
    _classify_agent_clone_steps(resume, config, layout)
    _classify_agent_configure_steps(resume, config, layout)
    _classify_wire_canon_steps(resume, layout)
    _classify_marker_step(resume, layout.project_dir)
    _classify_venv_steps(resume, config, layout)


def _detect_code_repos(canon_root: Path, *, skip_hooks: set[str]) -> list[CodeRepoMeta]:
    """Scan *canon_root* for code repos: any subdirectory with ``.git``, excluding ``docs``.

    Decision 1 ("store, not discover"): this scan only ever runs once, when no
    ``.workflow.json`` exists yet; every subsequent run trusts the persisted list.
    """
    if not canon_root.is_dir():
        return []
    repos = []
    for child in sorted(canon_root.iterdir()):
        if not child.is_dir() or child.name == DOCS_REPO:
            continue
        if (child / ".git").exists():
            repos.append(_code_repo_meta(child.name, run_hook=child.name not in skip_hooks))
    return repos


def _code_repo_meta(name: str, *, run_hook: bool) -> CodeRepoMeta:
    try:
        return CodeRepoMeta(name=name, run_hook=run_hook)
    except ValidationError as exc:
        raise WorkflowError(exc.errors()[0]["msg"]) from exc


def _resolve_init_metadata(
    project_dir: Path,
    requested_mode: ProjectMode,
    canon_root: Path,
    *,
    skip_hooks: set[str],
) -> ProjectMetadata:
    """Return the metadata to use for this ``init`` run.

    Enforces mode immutability against any already-persisted ``.workflow.json``. When
    none exists yet and the requested mode is oob, detects code repos once; a later
    ``init --continue`` rerun reuses the already-persisted list instead of rescanning
    (rescanning would silently drop a previously passed ``--skip-hook``).
    """
    if (project_dir / METADATA_FILENAME).exists():
        existing = read_metadata(project_dir)
        if existing.mode != requested_mode:
            msg = f"project was initialised in {existing.mode.value} mode; --oob cannot be changed after init."
            raise WorkflowError(msg)
        persisted_skips = {repo.name for repo in existing.code_repos if not repo.run_hook}
        require(
            not skip_hooks or skip_hooks == persisted_skips,
            f"--skip-hook cannot be changed after init (recorded: {sorted(persisted_skips)}); "
            "rerun without it to reuse the recorded values.",
        )
        return existing
    if requested_mode == ProjectMode.oob:
        code_repos = _detect_code_repos(canon_root, skip_hooks=skip_hooks)
        require(
            bool(code_repos),
            f"no code repos found under {canon_root}; docs-only oob projects are out of scope.",
        )
        unknown = skip_hooks - {repo.name for repo in code_repos}
        require(not unknown, f"--skip-hook names no detected code repo: {sorted(unknown)}.")
        console.print(f"Detected code repos: {', '.join(repo.name for repo in code_repos)}")
        return ProjectMetadata(mode=ProjectMode.oob, code_repos=code_repos)
    return ProjectMetadata()


def _check_executable_bootstrap(repo: Path, rel_path: str, issues: list[str]) -> None:
    """Append an issue unless *rel_path* in *repo* exists, is executable, and is committed as executable."""
    path = repo / rel_path
    if not path.is_file():
        issues.append(f"{path} must exist.")
        return
    if not os.access(path, os.X_OK):
        issues.append(f"{path} must be executable.")
    # Agents clone the repo, so they only get the committed file and its committed mode.
    if git_file_mode_at_ref(repo, "HEAD", rel_path) != _GIT_EXECUTABLE_MODE:
        issues.append(f"{path} must be committed as executable (git add --chmod=+x {rel_path}).")


def _check_repo_with_commit(repo: Path, issues: list[str]) -> bool:
    """Append issues unless *repo* is a git repo with a commit and no lock; return whether HEAD-based checks can run."""
    if not (repo / ".git").is_dir():
        issues.append(f"{repo} is not a git repository (no .git/ found).")
        return False
    if git_index_lock_present(repo):
        issues.append(f"leftover git lock in {repo}; remove it first.")
    if git_rev_parse(repo, "HEAD") is None:
        issues.append(f"{repo} must have at least one commit.")
        return False
    return True


def _check_bare_valid(bare_path: Path, issues: list[str]) -> None:
    if bare_path.exists() and not git_is_valid_repo(bare_path, bare=True):
        issues.append(f"{bare_path} exists but is not a valid bare clone; remove it or fix it.")


def _raise_if_issues(title: str, issues: list[str]) -> None:
    if issues:
        raise WorkflowError("\n".join([f"{title}:", *(f"- {issue}" for issue in issues)]))


def _check_hook_committed(repo: Path, issues: list[str]) -> None:
    """Append an issue unless *repo*'s HEAD has ``.git-pre-commit-hook`` (agents clone HEAD, not the working tree)."""
    if not git_path_exists_at_ref(repo, "HEAD", HOOK_FILENAME):
        issues.append(f"{repo / HOOK_FILENAME} must be committed.")


def _check_code_repo_init(
    layout: Layout,
    repo: str,
    issues: list[str],
    *,
    run_hook: bool,
    skip_hint: str,
) -> None:
    """Pre-flight one code repo: it has a commit and, if *run_hook*, an executable committed hook (else *skip_hint*)."""
    repo_path = layout.git_repo_root(WorktreeName.canon, repo)
    # Executable, since check/accept refuse a hook the docker script cannot run (pre_commit_hook_present).
    if (
        _check_repo_with_commit(repo_path, issues)
        and run_hook
        and git_file_mode_at_ref(repo_path, "HEAD", HOOK_FILENAME) != _GIT_EXECUTABLE_MODE
    ):
        issues.append(
            f"{repo_path / HOOK_FILENAME} must be committed as executable "
            f"(git add --chmod=+x {HOOK_FILENAME}), or {skip_hint}.",
        )


def _run_hook_off_hint(repo: str) -> str:
    """Recovery for a ``run_hook`` repo without a hook, once ``.workflow.json`` is persisted."""
    return f"set run_hook: false for {repo} in {METADATA_FILENAME} and run workflow agents_md"


def _check_in_band_init_preconditions(layout: Layout, issues: list[str]) -> None:
    canon_repo = layout.git_repo_root(WorktreeName.canon)
    if not _check_repo_with_commit(canon_repo, issues):
        return
    _check_hook_committed(canon_repo, issues)
    if git_path_exists_at_ref(canon_repo, "HEAD", IN_BAND_BOOTSTRAP_PATH):
        _check_executable_bootstrap(canon_repo, IN_BAND_BOOTSTRAP_PATH, issues)
    elif not git_path_exists_at_ref(canon_repo, "HEAD", "pyproject.toml"):
        issues.append(
            f"{canon_repo} must commit a pyproject.toml, or a {IN_BAND_BOOTSTRAP_PATH} script that creates the venv "
            "(see docs/example_bootstrap_venv.sh).",
        )


def _check_oob_init_preconditions(layout: Layout, issues: list[str]) -> None:
    docs_repo = layout.git_repo_root(WorktreeName.canon, DOCS_REPO)
    if _check_repo_with_commit(docs_repo, issues):
        if git_current_branch(docs_repo) != "main":
            issues.append(f"{docs_repo} must have 'main' checked out.")
        body_path = docs_repo / BODY_OF_AGENTS_FILENAME
        if not body_path.is_file():
            issues.append(f"{body_path} must exist.")
        _check_executable_bootstrap(docs_repo, BOOTSTRAP_VENV_FILENAME, issues)
    # --skip-hook is fixed at the first init; afterwards only the .workflow.json edit can switch the hook off.
    persisted = (layout.project_dir / METADATA_FILENAME).exists()
    for repo in layout.code_repos():
        skip_hint = _run_hook_off_hint(repo) if persisted else f"pass --skip-hook {repo}"
        _check_code_repo_init(layout, repo, issues, run_hook=layout.metadata.runs_hook(repo), skip_hint=skip_hint)


def _check_init_preconditions(layout: Layout) -> None:
    """Fail on cheaply-detectable causes of a broken ``init`` run, listing all of them at once."""
    issues: list[str] = []
    env_list = layout.project_dir / "env.list"
    if not env_list.is_file():
        issues.append(f"{env_list} must exist.")
    if _is_oob(layout):
        _check_oob_init_preconditions(layout, issues)
    else:
        _check_in_band_init_preconditions(layout, issues)
    for repo in layout.repos():
        _check_bare_valid(layout.bare_repo(repo), issues)
    _raise_if_issues("init pre-flight checks failed", issues)


def _create_and_commit_gitkeep_files(layout: Layout, canon_repo: Path, *, dry_run: bool) -> None:
    for state in PlanState:
        state_path = layout.plans_root(WorktreeName.canon) / state.value
        gitkeep = state_path / ".gitkeep"
        if not dry_run:
            state_path.mkdir(parents=True, exist_ok=True)
            gitkeep.touch()
        git_add(canon_repo, gitkeep, dry_run=dry_run)
    git_commit_if_changed(canon_repo, "chore: .gitkeep files for state dirs", dry_run=dry_run, no_verify=True)


def _run_clone_and_configure_steps(
    resume: ResumePlan,
    config: WorkflowConfig,
    layout: Layout,
    *,
    dry_run: bool,
) -> None:
    """Run the bare-clone, agent-clone, configure, and wire-canon steps for every repo."""
    oob = _is_oob(layout)
    for repo in layout.repos():
        if resume.should_run(_bare_clone_step_name(layout, repo)):
            _run_bare_clone_for_repo(layout, repo, dry_run=dry_run)

    # Clone/configure steps below keep their own _dir_needs_clone-style guards (shared
    # verbatim with _classify_*) rather than gating on should_run: a `needs_continue` clone
    # step means "leave the existing clone as-is" (would fail if re-cloned), not "run it".
    for agent in config.agents:
        for repo in layout.repos():
            _run_agent_clone_for_repo(layout, agent.name, repo, dry_run=dry_run)

    for agent in config.agents:
        for repo in layout.repos():
            _run_agent_configure_for_repo(layout, agent.name, repo, hook_optional=oob, dry_run=dry_run)

    for repo in layout.repos():
        if resume.should_run(_wire_canon_step_name(layout, repo)):
            _run_wire_canon_for_repo(layout, repo, dry_run=dry_run)


def _run_init_steps(resume: ResumePlan, config: WorkflowConfig, layout: Layout, *, dry_run: bool) -> None:
    """Perform the (already-classified) init steps, gated on resume.should_run."""
    project_dir = layout.project_dir
    oob = _is_oob(layout)
    # canon_repo is the docs repo's root in canon: identical to canon itself in-band,
    # canon/docs in oob (git_repo_root defaults to the docs repo in both modes).
    canon_repo = layout.git_repo_root(WorktreeName.canon)

    # In-band: generate AGENTS.md into canon before the bare clone so all agent clones
    # inherit it via git. Oob: AGENTS.md is not committed/propagated by git at all, so it
    # is generated after every worktree exists (see the oob call further down).
    if not oob:
        _run_agents_md_step(resume, config, layout, canon_repo, dry_run=dry_run)

    _create_and_commit_gitkeep_files(layout, canon_repo=canon_repo, dry_run=dry_run)

    _run_clone_and_configure_steps(resume, config, layout, dry_run=dry_run)

    if resume.should_run(_STEP_MARKER) and not dry_run:
        write_metadata(project_dir, layout.metadata)

    if oob:
        _run_agents_md_step(resume, config, layout, canon_repo, dry_run=dry_run)

    # Create venvs (always via --reset: cheap on first creation, destructive-but-
    # confirmed via --continue when a venv already exists; see _classify_init_steps).
    script = _find_docker_script(project_dir)
    project_name = config.project_name or project_dir.name
    for agent in config.agents:
        run_command(
            _create_venv_command(script, project_name, project_dir, agent.name),
            cwd=project_dir,
            dry_run=dry_run,
        )
    run_command(
        _create_venv_command(script, project_name, project_dir, WorktreeName.canon),
        cwd=project_dir,
        dry_run=dry_run,
    )


@register_script("init")
@click.option("--oob", is_flag=True, help="Initialise an out-of-band project (separate docs and code repos).")
@click.option(
    "--skip-hook",
    "skip_hooks",
    multiple=True,
    metavar="NAME",
    help="Code repo not to install the pre-commit hook symlink for. Only valid with --oob; repeatable.",
)
@click.option("--continue", "cont", is_flag=True, help="Proceed past rough (unverifiable) partial state.")
@click.pass_context
def init(ctx: click.Context, *, oob: bool, skip_hooks: tuple[str, ...], cont: bool) -> None:
    """Initialise the project directory: create bare and agent git clones."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]
    project_dir = (config.project_dir or Path.cwd()).resolve()
    # No .workflow.json yet: the canon worktree root is mode-independent, so default metadata suffices.
    canon_root = Layout(project_dir=project_dir, metadata=ProjectMetadata()).worktree_root(WorktreeName.canon)

    try:
        require(oob or not skip_hooks, "--skip-hook is only valid with --oob.")
        requested_mode = ProjectMode.oob if oob else ProjectMode.in_band
        metadata = _resolve_init_metadata(project_dir, requested_mode, canon_root, skip_hooks=set(skip_hooks))
        layout = Layout(project_dir=project_dir, metadata=metadata)
        _check_init_preconditions(layout)

        resume = ResumePlan(title="init")
        _classify_init_steps(resume, config, layout)
        resume.render()
        if resume.abort_unless_continue(cont=cont):
            ctx.exit(1)
            return

        _run_init_steps(resume, config, layout, dry_run=dry_run)
        _write_script_audit_log(config.model_copy(update={"project_dir": project_dir}), "init", dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


def _add_repo_metadata(layout: Layout, name: str, *, run_hook: bool) -> ProjectMetadata:
    """Check the add-repo preconditions and return the metadata with *name* registered."""
    metadata = layout.metadata
    require(metadata.mode == ProjectMode.oob, "add-repo is only valid for out-of-band projects.")
    # The docs/duplicate checks repeat the model's validators, for friendlier messages.
    require(name != DOCS_REPO, f"{DOCS_REPO!r} is reserved for the docs repo.")
    require(
        name not in {repo.name for repo in metadata.code_repos},
        f"{name!r} is already registered as a code repo.",
    )
    # Validate the name before it is used as a path below.
    new_metadata = ProjectMetadata(
        mode=metadata.mode,
        code_repos=[*metadata.code_repos, _code_repo_meta(name, run_hook=run_hook)],
    )
    issues: list[str] = []
    _check_code_repo_init(layout, name, issues, run_hook=run_hook, skip_hint="pass --skip-hook")
    _check_bare_valid(layout.bare_repo(name), issues)
    _raise_if_issues("add-repo pre-flight checks failed", issues)
    return new_metadata


def _classify_add_repo_steps(resume: ResumePlan, config: WorkflowConfig, layout: Layout, name: str) -> None:
    _classify_bare_clone_for_repo(resume, layout, name, f"bare clone {name}")
    for agent in config.agents:
        _classify_agent_clone_for_repo(resume, layout, agent.name, name, f"clone {agent.name}/{name}")
    for agent in config.agents:
        _classify_agent_configure_for_repo(
            resume,
            layout,
            agent.name,
            name,
            f"configure {agent.name}/{name}",
            hook_optional=True,
        )
    _classify_wire_canon_for_repo(resume, layout, name, f"wire canon/{name} to bare")


def _run_add_repo_steps(
    resume: ResumePlan,
    config: WorkflowConfig,
    layout: Layout,
    name: str,
    *,
    dry_run: bool,
) -> None:
    if resume.should_run(f"bare clone {name}"):
        _run_bare_clone_for_repo(layout, name, dry_run=dry_run)
    for agent in config.agents:
        _run_agent_clone_for_repo(layout, agent.name, name, dry_run=dry_run)
    for agent in config.agents:
        _run_agent_configure_for_repo(layout, agent.name, name, hook_optional=True, dry_run=dry_run)
    if resume.should_run(f"wire canon/{name} to bare"):
        _run_wire_canon_for_repo(layout, name, dry_run=dry_run)


@register_script("add-repo")
@click.argument("name")
@click.option("--skip-hook", is_flag=True, help="Do not install the pre-commit hook symlink for this repo.")
@click.option("--continue", "cont", is_flag=True, help="Proceed past rough (unverifiable) partial state.")
@click.pass_context
def add_repo(ctx: click.Context, name: str, *, skip_hook: bool, cont: bool) -> None:
    """Register an additional code repo in an out-of-band project."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        layout = config.layout()
        new_metadata = _add_repo_metadata(layout, name, run_hook=not skip_hook)
        new_layout = layout.model_copy(update={"metadata": new_metadata})

        resume = ResumePlan(title="add-repo")
        _classify_add_repo_steps(resume, config, new_layout, name)
        # Every AGENTS.md copy lists the code repos agents must cover in `branches` frontmatter.
        _classify_agents_md_step(resume, config, new_layout)
        resume.render()
        if resume.abort_unless_continue(cont=cont):
            ctx.exit(1)
            return

        _run_add_repo_steps(resume, config, new_layout, name, dry_run=dry_run)
        if not dry_run:
            write_metadata(layout.project_dir, new_metadata)
        # After write_metadata: the trusted hash covers the new .workflow.json.
        _run_agents_md_step(resume, config, new_layout, layout.git_repo_root(WorktreeName.canon), dry_run=dry_run)

        _write_script_audit_log(config, "add-repo", dry_run=dry_run)
        console.print(f"Registered code repo [bold]{name}[/bold].")
        console.print(
            f"The venvs do not include it yet: add it to {DOCS_REPO}/{BOOTSTRAP_VENV_FILENAME} and recreate them.",
        )
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


def _sync_one_branch(  # noqa: PLR0913
    canon_repo: Path,
    repo: str,
    branch: str,
    origin_ref: str,
    *,
    force: bool,
    dry_run: bool,
) -> bool:
    """Sync one branch of *repo* from *origin_ref* to ``bare``; returns False if refused.

    Refuses (rather than force-pushing) when ``bare``'s current tip is not an ancestor of
    *origin_ref* -- i.e. bare holds commits origin does not -- unless *force* is passed,
    since that push would otherwise silently discard them (Risk 2).
    """
    bare_ref = f"refs/remotes/{BARE_REMOTE}/{branch}"
    old_sha = git_rev_parse(canon_repo, bare_ref)
    new_sha = git_rev_parse(canon_repo, origin_ref)
    if old_sha == new_sha:
        console.print(f"{repo} {branch}: up to date ({new_sha}).")
        return True
    fast_forward = old_sha is None or git_is_ancestor(canon_repo, bare_ref, origin_ref)
    if not fast_forward and not force:
        console.print(
            f"[yellow]\\[!] {repo} {branch}: bare ({old_sha}) is not an ancestor of origin ({new_sha}); "
            "rerun with --force to overwrite.[/yellow]",
        )
        return False
    refspec = f"{'' if fast_forward else '+'}{origin_ref}:refs/heads/{branch}"
    git_push_refspec(canon_repo, refspec, dry_run=dry_run)
    console.print(f"{repo} {branch}: {old_sha or '(none)'} -> {new_sha}")
    return True


def _sync_one_repo(layout: Layout, repo: str, extra_branches: tuple[str, ...], *, force: bool, dry_run: bool) -> bool:
    """Sync one repo's ``bare`` from canon's ``origin``; returns False if any branch was refused."""
    canon_repo = layout.git_repo_root(WorktreeName.canon, repo)
    if git_remote_url(canon_repo, "origin") is None:  # upstream remote, not bare (023.5)
        console.print(f"[dim]{repo}: no upstream remote configured; skipping.[/dim]")
        return True

    # Both fetches always run for real, even in --dry-run: they only touch remote-tracking
    # refs (never the working tree), and the fast-forward check below needs them to be
    # current for the preview to mean anything (same classification-time-fetch exception
    # documented in this module's docstring).
    git_fetch(canon_repo)
    git_fetch_remote(canon_repo, "origin", prune=True)  # upstream remote, not bare (023.5)
    default_branch = resolve_default_branch(canon_repo, "origin")  # upstream remote, not bare (023.5)

    ok = True
    for branch in (default_branch, *extra_branches):
        origin_ref = f"refs/remotes/origin/{branch}"
        if git_rev_parse(canon_repo, origin_ref) is None:
            console.print(f"[yellow]\\[!] {repo}: {branch!r} not found on origin; skipping.[/yellow]")
            continue
        ok &= _sync_one_branch(canon_repo, repo, branch, origin_ref, force=force, dry_run=dry_run)
    return ok


@register_script("sync")
@click.option("--repo", "repos", multiple=True, metavar="NAME", help="Restrict sync to this repo. Repeatable.")
@click.option(
    "--branch",
    "branches",
    multiple=True,
    metavar="NAME",
    help="Extra branch to sync besides the upstream default branch. Repeatable.",
)
@click.option("--force", is_flag=True, help="Force-push even when bare is not a fast-forward ancestor of origin.")
@click.pass_context
def sync(ctx: click.Context, repos: tuple[str, ...], branches: tuple[str, ...], *, force: bool) -> None:
    """Refresh bare/<repo> from canon's upstream (origin)."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        layout = config.layout()
        require(_is_oob(layout), "sync is only valid for out-of-band projects.")
        # docs is excluded by default: bare is its source of truth and is normally ahead of origin.
        selected = list(repos) if repos else layout.code_repos()
        unknown = sorted(set(selected) - set(layout.repos()))
        require(not unknown, f"unknown repo(s): {unknown}")

        ok = True
        for repo in selected:
            ok &= _sync_one_repo(layout, repo, branches, force=force, dry_run=dry_run)

        _write_script_audit_log(config, "sync", dry_run=dry_run)
        if not ok:
            ctx.exit(1)
            return
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


def _find_docker_script(project_dir: Path) -> str:
    """Return the path to run_agent_in_docker.sh, or just its name for PATH lookup."""
    candidate = project_dir / "run_agent_in_docker.sh"
    if candidate.is_file():
        return str(candidate)
    return "run_agent_in_docker.sh"


def _agent_docker_command(
    script: str,
    project_name: str,
    project_dir: Path,
    agent: AgentConfig,
    *,
    reset: bool,
) -> str:
    """Build the tmux window command string for an agent."""
    parts = [script, "agent", project_name, agent.name, "--project-dir", str(project_dir)]
    if agent.model:
        parts.extend(["--model", agent.model])
    if reset:
        parts.append("--reset")
    return shlex.join(parts)


def _builder_shell_command(script: str, project_name: str, project_dir: Path) -> str:
    """Build the tmux window command string for the builder-shell window."""
    return shlex.join([script, "shell", project_name, "builder", "--project-dir", str(project_dir)])


def _create_venv_command(script: str, project_name: str, project_dir: Path, worktree: str) -> list[str]:
    """Build the command for creating *worktree*'s virtual env (agent name or 'canon')."""
    return [script, "venv", project_name, worktree, "--project-dir", str(project_dir), "--reset"]


def _hook_command(
    script: str,
    project_name: str,
    project_dir: Path,
    worktree: str,
    *,
    repo: str | None = None,
) -> list[str]:
    """Build the command for running the pre-commit hook in a throwaway container.

    *repo* is the code repo to run the hook inside, for an oob wrapper (``--repo`` on
    the docker script); omitted for in-band or when the hook targets the wrapper root.
    """
    command = [script, "hook", project_name, worktree, "--project-dir", str(project_dir)]
    if repo is not None:
        command.extend(["--repo", repo])
    return command


def _build_start_windows(
    config: WorkflowConfig,
    project_dir: Path,
    *,
    reset: bool,
) -> list[tuple[str, str]]:
    project_name = config.project_name or project_dir.name
    script = _find_docker_script(project_dir)
    windows = [
        (agent.name, _agent_docker_command(script, project_name, project_dir, agent, reset=reset))
        for agent in config.agents
    ]
    windows.append(("builder-shell", _builder_shell_command(script, project_name, project_dir)))
    return windows


@register_script("start")
@click.option("--reset", is_flag=True, default=False, help="Kill the existing session and containers, then recreate.")
@click.pass_context
def start(ctx: click.Context, *, reset: bool) -> None:
    """Create the workflow tmux session, or attach to it if it already exists.

    ``start`` is the control pane's entry point, not a command run from inside the
    session, so unlike other script commands it must not require an existing session.
    """
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = config.require_project_dir()
        if not config.agents:
            msg = "No agents configured."
            raise WorkflowError(msg)
        session_exists = workflow_session_exists(config)
        if session_exists and reset:
            require(
                not is_inside_workflow_session(config),
                "refusing to kill the session you are attached to; detach first, or run without --reset.",
            )

        resume = ResumePlan(title="start")
        if session_exists and not reset:
            resume.satisfied("create session", "session already exists; attaching")
        elif session_exists:
            resume.will_run("kill existing session")
            resume.will_run("create session")
        else:
            resume.will_run("create session")
        resume.render()

        if session_exists and reset:
            kill_workflow_session(config, dry_run=dry_run)
        if not session_exists or reset:
            first_agent = config.agents[0]
            windows = _build_start_windows(config, project_dir, reset=reset)
            create_workflow_session(config, windows, first_agent.name, dry_run=dry_run)

        _write_script_audit_log(config, "start", dry_run=dry_run)
        attach_workflow_session(config, dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


_SLUG_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def _intent_frontmatter(layout: Layout, stem: str) -> PlanFrontmatter | None:
    """Default ``branches`` block for a new oob intent (every code repo on ``feat/<stem>``); ``None`` in-band."""
    if layout.metadata.mode == ProjectMode.in_band:
        return None
    branches: dict[str, RepoBranch | None] = {}
    for repo in layout.code_repos():
        planner_repo = layout.git_repo_root(WorktreeName.planner, repo)
        require(planner_repo.is_dir(), f"{planner_repo} is missing; run workflow init (or add-repo) first.")
        base = resolve_default_branch(planner_repo, BARE_REMOTE)
        branches[repo] = RepoBranch(branch=f"{BRANCH_PREFIX}{stem}", base=base)
    return PlanFrontmatter(branches=branches)


@register_script("new")
@click.argument("slug")
@click.pass_context
def new(ctx: click.Context, slug: str) -> None:
    """Create a new intent file and open it in $EDITOR."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        layout = config.layout()
        if not _SLUG_RE.fullmatch(slug):
            msg = f"Invalid slug {slug!r}: use lowercase letters, digits, and underscores."
            raise WorkflowError(msg)
        number = next_intent_number(layout)
        intent_path = layout.plans_root(WorktreeName.canon) / "intent" / f"{number:03d}_{slug}.md"
        # Auto-increment should make this unreachable; a cheap safety net against clobbering.
        require(not intent_path.exists(), f"{intent_path} already exists; refusing to overwrite it.")
        template_text = (
            importlib.resources.files("workflow.templates").joinpath("intent.md.j2").read_text(encoding="utf-8")
        )
        content = jinja2.Template(template_text).render(number=number, slug=slug)
        frontmatter = _intent_frontmatter(layout, intent_path.stem)
        if frontmatter is not None:
            content = render_frontmatter(frontmatter) + content
        if dry_run:
            console.print(f"Would create: {intent_path}")
            console.print(f"Would open in $EDITOR: {intent_path}")
        else:
            intent_path.parent.mkdir(parents=True, exist_ok=True)
            intent_path.write_text(content, encoding="utf-8")
            _open_in_editor(intent_path)
            console.print(f"Created: {intent_path}", markup=False, soft_wrap=True)
        canon_repo = layout.git_repo_root(WorktreeName.canon)
        git_add(
            canon_repo,
            intent_path.relative_to(canon_repo),
            dry_run=dry_run,
        )
        next_step = _command_next_step(config, PlanState.INTENT, "new", intent_path.name)
        _write_script_audit_log(config, "new", dry_run=dry_run, output_plans=[intent_path.stem], next_step=next_step)
        print_next_step(next_step)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


# Step-name constants: the join key between _classify_todo_steps and todo()'s run phase.
_STEP_TODO_TRANSFER = "transfer planner branch to main"
_STEP_TODO_REBASE = "rebase planner branch onto main"
_STEP_TODO_TRANSITION = "transition to todo"


def _classify_todo_steps(resume: ResumePlan, plan: Plan, layout: Layout) -> None:
    planner_repo = layout.git_repo_root(WorktreeName.planner)
    transfer_will_run = plan.state == PlanState.PLANNING
    if transfer_will_run:
        resume.will_run(_STEP_TODO_TRANSFER)
    else:
        resume.satisfied(_STEP_TODO_TRANSFER, "plan already past planning")
    _classify_rebase_step(resume, _STEP_TODO_REBASE, planner_repo, transfer_will_run=transfer_will_run)

    todo_path = layout.plans_root(WorktreeName.canon) / "todo" / plan.path.name
    if todo_path.exists():
        resume.satisfied(_STEP_TODO_TRANSITION, "plan file already in todo/")
    else:
        resume.will_run(_STEP_TODO_TRANSITION)

    resume.will_run("push canon")


@register_script("todo")
@click.argument("plan_path")
@click.option("--force-validation", is_flag=True, help="Continue even when validation fails")
@click.option(
    "--force-push",
    is_flag=True,
    help="Allow force-pushing a branch an earlier run rebased but failed to publish",
)
@click.pass_context
def todo(ctx: click.Context, plan_path: str, *, force_validation: bool, force_push: bool) -> None:
    """Approve a ticket: push from planner, pull to canon, transition to todo."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        layout = config.layout()
        plan = _resolve_planner_plan(plan_path, config, "todo")
        _enforce_expected_state(plan, "todo")
        planner_repo = layout.git_repo_root(WorktreeName.planner)
        canon_repo = layout.git_repo_root(WorktreeName.canon)
        require(not git_index_lock_present(planner_repo), f"leftover git lock in {planner_repo}; remove it first.")
        require(not git_index_lock_present(canon_repo), f"leftover git lock in {canon_repo}; remove it first.")
        require(git_current_branch(canon_repo) == "main", "todo: canon must be on 'main'.")

        results = run_validate(plan, config, target_worktree_override="planner", dry_run=dry_run)
        if not dry_run:
            _print_results(results)
            if not force_validation and any(result.status == "fail" for result in results):
                ctx.exit(1)
                return

        resume = ResumePlan(title="todo")
        _classify_todo_steps(resume, plan, layout)
        resume.render()

        planner_branch = None
        if resume.should_run(_STEP_TODO_TRANSFER):
            planner_branch = git_current_branch(planner_repo)
            result = transfer_branch_to_main(
                planner_repo,
                canon_repo,
                branch=planner_branch,
                allow_rebase=resume.should_run(_STEP_TODO_REBASE),
                force_push=force_push,
                already_fetched=True,  # the rebase classification step just fetched
                other_worktrees=_other_worktrees(layout, WorktreeName.planner),
                post_rebase_verify=lambda: _run_docs_hook(config, layout, WorktreeName.planner, dry_run=dry_run),
                dry_run=dry_run,
            )
            _warn_if_rebased(result, planner_branch)
        # Value-returning transition: always call (idempotent) so new_plan reflects the
        # target dir even when the git mv itself is a no-op (Fix 2 "value-returning steps").
        new_plan = _transition_if_needed(
            plan,
            PlanState.TODO,
            layout,
            worktree_override=WorktreeName.canon,
            dry_run=dry_run,
        )
        git_push(canon_repo, dry_run=dry_run)
        _sync_repos_best_effort(layout, park=WorktreeName.planner, dry_run=dry_run)
        _delete_merged_planner_branch(layout, planner_branch, dry_run=dry_run)
        next_step = _command_next_step(config, PlanState.PLANNING, "todo", new_plan.name)
        _write_script_audit_log(config, "todo", dry_run=dry_run, input_plan=plan.name, next_step=next_step)
        print_next_step(next_step)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


def _resolve_planner_plan(plan_path: str, config: WorkflowConfig, command_name: str) -> Plan:
    """Resolve *plan_path*; when it is not found, name the planner branch whose plans are the only ones seen."""
    try:
        return resolve_plan(plan_path, config=config, command_name=command_name)
    except (PlanNotFoundError, SourcesDisagreeError) as exc:
        planner_repo = config.layout().git_repo_root(WorktreeName.planner)
        branch = None
        with suppress(subprocess.CalledProcessError, OSError):
            branch = git_current_branch(planner_repo)
        if branch is None:
            raise
        print_workflow_error(exc)
        hint = (
            f"The planner clone is on branch '{branch}'; {command_name} only sees planning-state plans on that "
            f"branch. Check out the branch holding the plan in {planner_repo} and retry."
        )
        raise WorkflowError(hint) from exc


def _delete_merged_planner_branch(layout: Layout, branch: str | None, *, dry_run: bool) -> None:
    """Delete the planner branch a todo/implement transfer just merged; never ``main`` or a skipped transfer."""
    if branch is not None and branch != "main":
        canon_repo = layout.git_repo_root(WorktreeName.canon)
        delete_merged_branch(canon_repo, (layout.git_repo_root(WorktreeName.planner),), branch, dry_run=dry_run)


def _other_worktrees(layout: Layout, source_worktree: WorktreeName) -> list[Path]:
    """Return every worktree's git repo root except *source_worktree* itself."""
    return [layout.git_repo_root(worktree) for worktree in WorktreeName if worktree != source_worktree]


def _sync_repos_best_effort(layout: Layout, *, park: WorktreeName | None = None, dry_run: bool) -> None:
    """Bring every repo's ``main`` up to date, parking *park* on it; failures only warn.

    Runs after a handoff has already landed, so a sync problem must not fail the command
    (same rationale as :func:`_rotate_best_effort`). All four clones are refreshed, not
    just the two the handoff touched: a clone left behind cuts its next branch from a
    stale ``main``, and ``doctor``'s stale-main check treats any difference from bare as
    an anomaly. Only *park* is checked out to ``main`` -- the others may be mid-work.

    Code repos (oob mode) never have their branch touched -- the tool does not manage
    code-repo branch lifecycle -- so they only get a plain ``git fetch bare``.
    """
    for worktree in WorktreeName:
        docs_repo_path = layout.git_repo_root(worktree)
        if docs_repo_path.is_dir():
            try:
                if worktree == park:
                    park_on_main(docs_repo_path, dry_run=dry_run)
                else:
                    refresh_main(docs_repo_path, dry_run=dry_run)
            except (subprocess.CalledProcessError, WorkflowError) as exc:
                error_console.print(f"[yellow]main sync skipped in {docs_repo_path}: {exc}[/yellow]")
        fetch_code_repos_best_effort(layout, worktree, dry_run=dry_run)


class RepoPushInfo(BaseModel):
    """One repo's ahead-of-bare state in the acting worktree, for the ``check``/``accept`` push set."""

    model_config = ConfigDict(frozen=True)

    repo: str
    branch: str
    commits: int


def _compute_push_set(layout: Layout, worktree: WorktreeName) -> list[RepoPushInfo]:
    """Return every repo in *worktree* that has commits bare does not, fetching bare first.

    The fetch always runs for real (never gated on ``--dry-run``), matching the module
    docstring's classification-time-fetch exception in ``git_helpers``: a stale fetch
    would silently under-count and drop a repo that genuinely has something to push.
    """
    push_set = []
    for repo in layout.repos():
        repo_path = layout.git_repo_root(worktree, repo)
        require(repo_path.is_dir(), f"{repo_path} is missing; run `workflow doctor`.")
        git_fetch(repo_path)
        commits = git_commits_not_in_bare(repo_path)
        if commits > 0:
            push_set.append(RepoPushInfo(repo=repo, branch=git_current_branch(repo_path), commits=commits))
    return push_set


def _print_push_set_table(push_set: list[RepoPushInfo]) -> None:
    """Print a (repo, branch, commits) table of every repo about to be pushed."""
    table = Table(title="Repos ahead of bare")
    table.add_column("repo")
    table.add_column("branch")
    table.add_column("commits")
    for info in push_set:
        table.add_row(info.repo, info.branch, str(info.commits))
    console.print(table)


def _require_clean_repos(layout: Layout, worktree: WorktreeName) -> None:
    """Fail on tracked modifications in any repo of *worktree*; warn only on untracked files.

    Untracked files are common noise in third-party code repos (build output, caches)
    and are not actionable here, so they only warn; tracked modifications mean the agent
    has uncommitted work that would not be reviewed, so they are a hard failure.
    """
    for repo in layout.repos():
        repo_path = layout.git_repo_root(worktree, repo)
        if not repo_path.is_dir():
            continue
        tracked = git_status_porcelain(repo_path, untracked=False)
        require(
            not tracked,
            f"{repo_path} has uncommitted tracked changes; commit or discard them first:\n{tracked}",
        )
        if git_status_porcelain(repo_path):
            error_console.print(f"[yellow]\\[!] {repo_path} has untracked files (not blocking).[/yellow]")


def _require_pushable(
    push_set: list[RepoPushInfo],
    layout: Layout,
    worktree: WorktreeName,
    *,
    protect_default_branch: bool,
    force_push: bool,
) -> None:
    """Fail before anything is transitioned if a code repo in *push_set* cannot be pushed.

    A detached HEAD has no branch to push, and a rewritten branch would be rejected by
    bare without *force_push*; both would otherwise fail mid-handoff, after the docs repo
    already landed. With *protect_default_branch*, commits on bare's default branch are
    refused: they would make ``workflow sync`` need a ``--force`` that discards them.
    """
    for info in _code_repo_entries(push_set):
        repo_path = layout.git_repo_root(worktree, info.repo)
        require(info.branch != "HEAD", f"{repo_path} has a detached HEAD; commit on a named branch first.")
        require(
            not layout.metadata.runs_hook(info.repo) or pre_commit_hook_present(repo_path),
            f"{repo_path} has no pre-commit hook but run_hook is true; commit {HOOK_FILENAME}, or "
            f"{_run_hook_off_hint(info.repo)}.",
        )
        if protect_default_branch:
            default_branch = resolve_default_branch(repo_path, BARE_REMOTE)
            require(
                info.branch != default_branch,
                f"{repo_path} has commits on {default_branch!r}; move them to a feature branch first.",
            )
        bare_ref = f"refs/remotes/{BARE_REMOTE}/{info.branch}"
        require(
            force_push or git_rev_parse(repo_path, bare_ref) is None or git_is_ancestor(repo_path, bare_ref, "HEAD"),
            f"{repo_path}: {info.branch!r} diverged from bare (rebased?); `accept --force-push` "
            "pushes it with --force-with-lease, otherwise push it by hand.",
        )


def _code_repo_entries(push_set: list[RepoPushInfo]) -> list[RepoPushInfo]:
    """The non-docs entries of *push_set*; the docs repo keeps its own dedicated push steps."""
    return [info for info in push_set if info.repo != DOCS_REPO]


def _run_repo_push_set(
    push_set: list[RepoPushInfo],
    layout: Layout,
    worktree: WorktreeName,
    *,
    force_push: bool = False,
    dry_run: bool,
) -> None:
    """Push every code repo in *push_set* (``--force-with-lease`` with *force_push*)."""
    for info in _code_repo_entries(push_set):
        git_push_branch(layout.git_repo_root(worktree, info.repo), info.branch, force=force_push, dry_run=dry_run)


def _run_repo_push_set_hooks(
    config: WorkflowConfig,
    push_set: list[RepoPushInfo],
    layout: Layout,
    worktree: str,
    *,
    dry_run: bool,
) -> None:
    """Run the pre-commit hook for every repo in *push_set* that has one enabled.

    In-band mode has a single repo (docs) and runs the hook exactly as before *repo*
    plumbing existed (``repo=None``, no ``--repo`` flag). Oob mode runs it once per
    repo in the push set, passing ``--repo NAME`` (docs included -- the wrapper root is
    not a git repo, so the hook must still be told which directory to run in).
    """
    if layout.metadata.mode != ProjectMode.oob:
        _run_pre_commit_hook(config, worktree, dry_run=dry_run)
        return
    for info in push_set:
        if _hook_enabled(layout, info.repo):
            _run_pre_commit_hook(config, worktree, dry_run=dry_run, repo=info.repo)
        else:
            console.print(_skip_hook_message(info.repo))


def _classify_rebase_step(
    resume: ResumePlan,
    step_name: str,
    source_repo: Path,
    *,
    branch: str | None = None,
    transfer_will_run: bool,
) -> None:
    """Classify a transfer's rebase step, probing only when the transfer step will run.

    Nothing touches *source_repo* otherwise -- not even to read its current branch, which
    is what *branch* defaults to. The probe's ``git fetch`` is why the run phase can pass
    ``already_fetched=True`` to :func:`transfer_branch_to_main`.
    """
    if not transfer_will_run:
        resume.satisfied(step_name, "transfer already done or not needed")
        return
    if branch_needs_rebase(source_repo, branch or git_current_branch(source_repo)):
        resume.will_run(step_name)
    else:
        resume.satisfied(step_name, "branch already based on current main")


def _warn_if_rebased(result: TransferResult, branch: str) -> None:
    if result is TransferResult.REBASED:
        error_console.print(
            f"[yellow]\\[!] {branch} did not fast-forward and was rebased onto main; "
            "history was rewritten. Review the change.[/yellow]",
        )


def _ensure_branch_checked_out(repo_path: Path, branch: str, *, dry_run: bool) -> None:
    """Check out *branch* in *repo_path* unless it is already the current branch."""
    if git_current_branch(repo_path) != branch:
        git_checkout(repo_path, branch, dry_run=dry_run)


def _fetch_code_repos(layout: Layout, worktree: WorktreeName, *, dry_run: bool) -> None:
    """Fetch bare for every code repo in *worktree*; never checks out (decision 13)."""
    for repo in layout.code_repos():
        repo_path = layout.git_repo_root(worktree, repo)
        if repo_path.is_dir():
            git_fetch(repo_path, dry_run=dry_run)


def _transition_if_needed(  # noqa: PLR0913
    plan: Plan,
    target_state: PlanState,
    layout: Layout,
    *,
    worktree_override: WorktreeName | None = None,
    commit_worktree: WorktreeName | None = None,
    dry_run: bool,
) -> Plan:
    """Move *plan* to *target_state*, skipping the ``git mv`` when already done.

    Always (re)commits via :func:`git_commit_if_changed`, which safely no-ops when
    there is nothing staged. This also finishes a prior run that moved the file but
    was interrupted before committing.

    Assumes the ``git mv`` (when it runs) is staged before this is called:
    :func:`~workflow.git_helpers.git_commit_if_changed` does not ``git add -u``, so an
    unstaged rename would silently no-op the commit.
    """
    worktree = worktree_override or plan.worktree
    target_rel = _plan_path_in_state(layout, worktree, target_state, plan)
    if (layout.project_dir / target_rel).exists():
        new_plan = plan.model_copy(update={"state": target_state, "path": target_rel})
    else:
        new_plan = transition_plan(
            plan,
            target_state,
            layout,
            worktree_override=worktree_override,
            dry_run=dry_run,
        )
    git_commit_if_changed(
        layout.git_repo_root(commit_worktree or worktree),
        _transition_commit_message(new_plan, target_state),
        dry_run=dry_run,
        no_verify=True,
    )
    return new_plan


# Step-name constants: the join key between _classify_implement_steps and _run_implement_steps.
_STEP_IMPLEMENT_TRANSFER = "transfer planner branch to main"
_STEP_IMPLEMENT_REBASE = "rebase planner branch onto main"
_STEP_IMPLEMENT_TRANSITION_TODO = "transition to todo"
_STEP_IMPLEMENT_TRANSITION_IMPLEMENTING = "transition to implementing"
_STEP_IMPLEMENT_FETCH_BUILDER = "fetch builder"
_STEP_IMPLEMENT_CHECKOUT_MAIN_BUILDER = "checkout main in builder"
_STEP_IMPLEMENT_ONTO_CHECKOUT = "checkout stack branch in builder"
_STEP_IMPLEMENT_ONTO_FRONTMATTER = "write onto frontmatter"
_STEP_IMPLEMENT_ONTO_MERGE_MAIN = "merge bare/main into stack branch"
_STEP_IMPLEMENT_MERGE_MAIN = "merge bare/main into existing feature branch"


def _merge_bare_main_into(builder_repo: Path, *, dry_run: bool) -> None:
    """Fetch and merge bare/main into builder's current branch; a no-op when already merged.

    Fetches even though the callers just ran :func:`_sync_repos_best_effort`: that sync only
    warns on failure, and merging a stale bare/main would silently drop the very commit
    this merge exists to deliver.
    """
    git_fetch(builder_repo, dry_run=dry_run)
    git_merge(builder_repo, f"{BARE_REMOTE}/main", ff_only=False, dry_run=dry_run)


def _plan_file_on_branch(
    builder_repo: Path,
    branch: str,
    state: PlanState,
    filename: str,
    plans_prefix: PurePosixPath,
) -> bool:
    """Whether *filename* sits under *state*'s directory on *branch*, per builder's ref or bare's.

    Both refs are consulted because which one carries the latest transition depends on the
    flags of the previous command: the default ``check`` commits in builder, while
    ``check --target-repo reviewer`` commits in reviewer and only pushes, leaving builder's
    local ref behind.
    """
    rel_path = f"{plans_prefix}/{state.value}/{filename}"
    return any(git_path_exists_at_ref(builder_repo, ref, rel_path) for ref in (branch, f"{BARE_REMOTE}/{branch}"))


def _manual_merge_hint(builder_repo: Path, feature_branch: str) -> str:
    """The commands that land bare/main on *feature_branch* by hand, for when this run cannot."""
    return f"git -C {builder_repo} checkout {feature_branch} && git -C {builder_repo} merge {BARE_REMOTE}/main"


def _classify_transfer_and_todo_steps(resume: ResumePlan, plan: Plan, layout: Layout) -> None:
    """Classify the planning->todo transfer steps shared by both implement paths."""
    transfer_will_run = plan.state == PlanState.PLANNING
    if transfer_will_run:
        resume.will_run(_STEP_IMPLEMENT_TRANSFER)
        resume.will_run(_STEP_IMPLEMENT_TRANSITION_TODO)
    else:
        resume.satisfied(_STEP_IMPLEMENT_TRANSFER, "plan already past planning")
        resume.satisfied(_STEP_IMPLEMENT_TRANSITION_TODO, "plan already past planning")
    _classify_rebase_step(
        resume,
        _STEP_IMPLEMENT_REBASE,
        layout.git_repo_root(WorktreeName.planner),
        transfer_will_run=transfer_will_run,
    )


def _classify_transition_implementing_step(resume: ResumePlan, plan: Plan, layout: Layout) -> None:
    implementing_path = layout.plans_root(WorktreeName.canon) / "implementing" / plan.path.name
    if implementing_path.exists():
        resume.satisfied(_STEP_IMPLEMENT_TRANSITION_IMPLEMENTING, "plan file already in implementing/")
    else:
        resume.will_run(_STEP_IMPLEMENT_TRANSITION_IMPLEMENTING)


def _run_transfer_and_todo_steps(  # noqa: PLR0913
    resume: ResumePlan,
    plan: Plan,
    layout: Layout,
    config: WorkflowConfig,
    *,
    force_push: bool,
    dry_run: bool,
) -> tuple[Plan, str | None]:
    """Run the shared planning->todo transfer steps; returns (possibly-updated plan, transferred planner branch).

    The branch is ``None`` when the transfer did not run.
    """
    planner_branch = None
    if resume.should_run(_STEP_IMPLEMENT_TRANSFER):
        planner_repo = layout.git_repo_root(WorktreeName.planner)
        canon_repo = layout.git_repo_root(WorktreeName.canon)
        planner_branch = git_current_branch(planner_repo)
        result = transfer_branch_to_main(
            planner_repo,
            canon_repo,
            branch=planner_branch,
            allow_rebase=resume.should_run(_STEP_IMPLEMENT_REBASE),
            force_push=force_push,
            already_fetched=True,  # the rebase classification step just fetched
            other_worktrees=_other_worktrees(layout, WorktreeName.planner),
            post_rebase_verify=lambda: _run_docs_hook(config, layout, WorktreeName.planner, dry_run=dry_run),
            dry_run=dry_run,
        )
        _warn_if_rebased(result, planner_branch)
        plan = _transition_if_needed(
            plan,
            PlanState.TODO,
            layout,
            worktree_override=WorktreeName.canon,
            dry_run=dry_run,
        )
    return plan, planner_branch


def _classify_implement_steps(
    resume: ResumePlan,
    plan: Plan,
    layout: Layout,
    builder_repo: Path,
    feature_branch: str,
) -> None:
    _classify_transfer_and_todo_steps(resume, plan, layout)
    _classify_transition_implementing_step(resume, plan, layout)
    resume.will_run("push canon")

    # If the feature branch already exists, builder is presumably already checked out to
    # it (and possibly has work in progress) -- fetch/checkout-main/pull must not run, or
    # a rerun on an already-fully-implemented plan would force builder off its own branch.
    branch_exists = git_branch_exists(builder_repo, feature_branch)
    current_branch = git_current_branch(builder_repo)
    if branch_exists:
        resume.satisfied(_STEP_IMPLEMENT_FETCH_BUILDER, "feature branch already exists")
        resume.satisfied(_STEP_IMPLEMENT_CHECKOUT_MAIN_BUILDER, "feature branch already exists")
        # The pre-existing-branch path never touches builder before the push below (unlike
        # the fresh-branch path), so builder's current branch is a sound predicate here: the
        # same push-moves-main gap as _STEP_IMPLEMENT_ONTO_MERGE_MAIN, but only reachable when
        # builder is already sitting on the branch that push is about to leave behind.
        if current_branch == feature_branch:
            resume.will_run(_STEP_IMPLEMENT_MERGE_MAIN)
        else:
            # Not satisfied, just impossible here: forcing builder off its current branch is
            # worse than leaving the transition commit unmerged, so demand --continue and hand
            # the merge over (the run step warns again with the same commands).
            resume.needs_continue(
                _STEP_IMPLEMENT_MERGE_MAIN,
                f"builder is on {current_branch!r}, not {feature_branch!r}; the plan file will not "
                f"reach the branch until you run: {_manual_merge_hint(builder_repo, feature_branch)}",
            )
    else:
        resume.will_run(_STEP_IMPLEMENT_FETCH_BUILDER)
        if current_branch == "main":
            resume.satisfied(_STEP_IMPLEMENT_CHECKOUT_MAIN_BUILDER, "builder already on main")
        else:
            resume.will_run(_STEP_IMPLEMENT_CHECKOUT_MAIN_BUILDER)
        resume.satisfied(_STEP_IMPLEMENT_MERGE_MAIN, "feature branch is being created fresh in this run")

    # Classified for the --continue prompt only (not separately gated at run time: creating
    # the branch is coupled 1:1 with _STEP_IMPLEMENT_FETCH_BUILDER, see _run_implement_steps).
    # needs_continue here means "leave the existing branch as-is", not "recreate it" -- unlike
    # a plain resume step, so it must not drive its own should_run-gated git checkout -b.
    step = f"create {feature_branch}"
    if not branch_exists:
        resume.will_run(step)
    elif plan.state == PlanState.IMPLEMENTING:
        resume.satisfied(step, "already created by a prior run")
    else:
        resume.needs_continue(step, "branch exists but plan not yet implementing; pass --continue to accept it as-is")


def _run_implement_steps(  # noqa: PLR0913
    resume: ResumePlan,
    plan: Plan,
    layout: Layout,
    builder_repo: Path,
    feature_branch: str,
    config: WorkflowConfig,
    *,
    force_push: bool,
    dry_run: bool,
) -> Plan:
    """Run the (already-classified) implement steps, gated on resume.should_run."""
    plan, planner_branch = _run_transfer_and_todo_steps(
        resume,
        plan,
        layout,
        config,
        force_push=force_push,
        dry_run=dry_run,
    )

    # Value-returning transition: always call (idempotent), regardless of should_run, so
    # new_plan reflects the target dir even when the git mv itself is a no-op (see Fix 2
    # "value-returning steps" nuance).
    new_plan = _transition_if_needed(plan, PlanState.IMPLEMENTING, layout, dry_run=dry_run)

    canon_repo = layout.git_repo_root(WorktreeName.canon)
    git_push(canon_repo, dry_run=dry_run)
    # Only planner is parked: builder keeps the new feat/* branch created below, so
    # parking it would check it straight back out onto 'main'. With no transfer there is
    # no planner branch involved at all, so nothing to park either.
    _sync_repos_best_effort(layout, park=WorktreeName.planner if planner_branch else None, dry_run=dry_run)
    _delete_merged_planner_branch(layout, planner_branch, dry_run=dry_run)
    # Leave an existing feature branch (and any work in progress on it) untouched: the branch
    # creation is coupled to this same gate, not classified/gated separately (see above).
    if resume.should_run(_STEP_IMPLEMENT_FETCH_BUILDER):
        git_fetch(builder_repo, dry_run=dry_run)
        _ensure_branch_checked_out(builder_repo, "main", dry_run=dry_run)
        git_pull(builder_repo, dry_run=dry_run)
        git_checkout(builder_repo, feature_branch, create=True, dry_run=dry_run)
    if resume.should_run(_STEP_IMPLEMENT_MERGE_MAIN):
        if git_current_branch(builder_repo) == feature_branch:
            _merge_bare_main_into(builder_repo, dry_run=dry_run)
        else:
            error_console.print(
                f"[yellow]\\[!] builder is not on {feature_branch}; {BARE_REMOTE}/main was not merged into it. "
                f"Run: {_manual_merge_hint(builder_repo, feature_branch)}[/yellow]",
            )

    return new_plan


def _require_builder_not_on_unfinished_stack(
    layout: Layout,
    builder_repo: Path,
    target_branch: str,
) -> None:
    """Refuse when builder sits on a different feat/* branch with an unfinished stack.

    Applies to plain ``implement`` (*target_branch* is the brand-new branch about to be
    created, so this fires for any other feat/* branch builder happens to be on) and to
    ``implement --onto`` (*target_branch* is the base's own branch, so builder already
    being there is fine).
    """
    current = git_current_branch(builder_repo)
    if current in ("main", target_branch) or not current.startswith(BRANCH_PREFIX):
        return
    tip = tip_plan_for_branch(layout, current)
    if tip is None:
        return
    stack = resolve_stack(layout, tip)
    if any(member.state in (PlanState.IMPLEMENTING, PlanState.CHECKING) for member in stack):
        msg = (
            f"builder is on {current!r}, which still has unfinished work in progress "
            f"(pass --onto {tip.name} to continue there, or check/accept/reject it first)."
        )
        raise WorkflowError(msg)


def _resolve_onto_base_plan(layout: Layout, builder_repo: Path, onto: str) -> Plan:
    """Resolve the ``--onto`` base plan: explicit name, or inferred from builder's current branch."""
    if onto != "":
        base_plan = find_stack_member(layout, onto)
        if base_plan is None:
            msg = f"--onto {onto!r} does not resolve to a plan."
            raise WorkflowError(msg)
        return base_plan
    current = git_current_branch(builder_repo)
    require(current.startswith(BRANCH_PREFIX), f"builder is on {current!r}, not a feat/* branch; pass --onto <plan>.")
    base_plan = tip_plan_for_branch(layout, current)
    if base_plan is None:
        msg = f"no plan resolves to branch {current!r} in builder."
        raise WorkflowError(msg)
    return base_plan


def _check_onto_preconditions(
    layout: Layout,
    plan: Plan,
    base_plan: Plan,
    builder_repo: Path,
) -> list[Plan]:
    """Validate every ``implement --onto`` precondition; return the base's resolved stack.

    Runs before any mutation, so the caller must not resolve the stack again afterwards:
    a cycle or an unresolvable ``onto`` has to fail while the plan file is still untouched.
    """
    reviewer_repo = layout.git_repo_root(WorktreeName.reviewer)
    require(
        plan.name != base_plan.name,
        f"{plan.name} cannot be stacked onto itself; pass --onto <other plan> or drop --onto.",
    )
    base_branch = branch_for_plan(layout, base_plan)
    child = stack_child(layout, base_plan)
    if child is not None:
        msg = f"{base_plan.name} already has {child.name} stacked on it; use --onto {child.name} instead."
        raise WorkflowError(msg)
    # Canon never shows 'checking' (only `check`'s target_repo does; canon jumps straight
    # from 'implementing' to 'done' at `accept`), so this one state covers both. It still
    # has to be checked: a rejected base keeps its branch, and that branch's tree keeps the
    # plan file, so the ref reads below would happily accept it.
    require(
        base_plan.state == PlanState.IMPLEMENTING,
        f"--onto base {base_plan.name} must be in 'implementing' or 'checking', "
        f"got {base_plan.state.value!r} (canon shows a plan in checking as 'implementing').",
    )
    require(
        git_branch_exists(builder_repo, base_branch),
        f"branch {base_branch!r} (for {base_plan.name}) does not exist in builder.",
    )
    # The implementing/checking distinction is invisible in canon (see above), so read it
    # from the branch's own tree: `check` commits that transition on the branch itself.
    git_fetch(builder_repo)
    filename = base_plan.path.name
    if _plan_file_on_branch(builder_repo, base_branch, PlanState.CHECKING, filename, layout.plans_prefix):
        git_fetch(reviewer_repo)
        ahead = git_commits_ahead_of_remote(reviewer_repo, base_branch)
        require(
            ahead == 0,
            f"{base_plan.name} is in checking with {ahead} commit(s) in reviewer not yet pushed "
            f"(the pr document / fix commits would be silently excluded); push or land them first.",
        )
    else:
        require(
            _plan_file_on_branch(builder_repo, base_branch, PlanState.IMPLEMENTING, filename, layout.plans_prefix),
            f"{base_plan.name}'s plan file is on branch {base_branch!r} under neither "
            f"docs/plans/implementing/ nor docs/plans/checking/; merge {BARE_REMOTE}/main into that branch first.",
        )
    fm = read_plan_frontmatter(layout, plan)
    if fm is not None and fm.onto not in (None, base_plan.name):
        msg = f"{plan.name} is already stacked onto {fm.onto!r}; cannot restack onto {base_plan.name!r}."
        raise WorkflowError(msg)
    return resolve_stack(layout, base_plan)


def _classify_implement_onto_steps(
    resume: ResumePlan,
    plan: Plan,
    layout: Layout,
    builder_repo: Path,
    feature_branch: str,
) -> None:
    _classify_transfer_and_todo_steps(resume, plan, layout)

    # No fetch: _check_onto_preconditions just fetched builder, so bare/* is current.
    on_branch = git_current_branch(builder_repo) == feature_branch
    # A base still in 'implementing' may never have been pushed (only `check` publishes
    # a branch); with no upstream there is nothing to be behind on.
    has_upstream = git_ref_exists_remote(builder_repo, feature_branch)
    up_to_date = not has_upstream or git_is_ancestor(builder_repo, f"{BARE_REMOTE}/{feature_branch}", feature_branch)
    if on_branch and up_to_date:
        resume.satisfied(_STEP_IMPLEMENT_ONTO_CHECKOUT, "builder already on the branch and up to date")
    else:
        resume.will_run(_STEP_IMPLEMENT_ONTO_CHECKOUT)

    fm = read_plan_frontmatter(layout, plan)
    if fm is not None and fm.onto is not None:
        resume.satisfied(_STEP_IMPLEMENT_ONTO_FRONTMATTER, "frontmatter already recorded")
    else:
        resume.will_run(_STEP_IMPLEMENT_ONTO_FRONTMATTER)

    _classify_transition_implementing_step(resume, plan, layout)
    resume.will_run("push canon")

    # Unconditional: the push above (same run) is what advances bare/main with the
    # very commit this merge exists to deliver, so no pre-push predicate on bare/main
    # can be trusted here. git_merge is already a no-op when there is nothing pending.
    resume.will_run(_STEP_IMPLEMENT_ONTO_MERGE_MAIN)


def _run_implement_onto_steps(  # noqa: PLR0913
    resume: ResumePlan,
    plan: Plan,
    layout: Layout,
    builder_repo: Path,
    feature_branch: str,
    base_plan: Plan,
    config: WorkflowConfig,
    *,
    force_push: bool,
    dry_run: bool,
) -> Plan:
    """Run the (already-classified) ``implement --onto`` steps, gated on resume.should_run."""
    plan, planner_branch = _run_transfer_and_todo_steps(
        resume,
        plan,
        layout,
        config,
        force_push=force_push,
        dry_run=dry_run,
    )

    if resume.should_run(_STEP_IMPLEMENT_ONTO_CHECKOUT):
        # No fetch: classification already fetched, and the pull below fetches again anyway.
        _ensure_branch_checked_out(builder_repo, feature_branch, dry_run=dry_run)
        git_pull(builder_repo, dry_run=dry_run, skip_if_no_upstream=True)

    canon_repo = layout.git_repo_root(WorktreeName.canon)
    if resume.should_run(_STEP_IMPLEMENT_ONTO_FRONTMATTER):
        # Read after the checkout (and its pull) but before the merge below: the anchor is
        # the branch tip as base_plan's own work left it, and must not include the merge
        # commit that is about to carry this very frontmatter write (see plan 025 §3).
        base_commit = git_commit_hash(builder_repo)
        existing = read_plan_frontmatter(layout, plan)
        branches = dict(existing.branches) if existing is not None else {}
        branches[DOCS_REPO] = RepoBranch(branch=feature_branch, base=base_commit)
        # model_copy, not a fresh model: keeps any extra keys a later plan format added.
        fm = (
            existing.model_copy(update={"onto": base_plan.name, "branches": branches})
            if existing is not None
            else PlanFrontmatter(onto=base_plan.name, branches=branches)
        )
        target_path = locate_plan_file(layout, WorktreeName.canon, plan.path.name) or (layout.project_dir / plan.path)
        if dry_run:
            console.print(f"[dim]would write frontmatter (onto={base_plan.name}) to {target_path}[/dim]")
        else:
            write_frontmatter(target_path, fm)
            git_add(canon_repo, target_path, dry_run=False)

    new_plan = _transition_if_needed(
        plan,
        PlanState.IMPLEMENTING,
        layout,
        worktree_override=WorktreeName.canon,
        dry_run=dry_run,
    )
    git_push(canon_repo, dry_run=dry_run)
    _sync_repos_best_effort(layout, park=WorktreeName.planner if planner_branch else None, dry_run=dry_run)
    _delete_merged_planner_branch(layout, planner_branch, dry_run=dry_run)

    # Unconditional (see _classify_implement_onto_steps): not gated on resume.should_run.
    _merge_bare_main_into(builder_repo, dry_run=dry_run)

    return new_plan


@register_script("implement")
@click.option("--continue", "cont", is_flag=True, help="Proceed past rough (unverifiable) partial state.")
@click.option(
    "--force-push",
    is_flag=True,
    help="Allow force-pushing a branch an earlier run rebased but failed to publish",
)
@click.option(
    "--onto",
    "onto",
    is_flag=False,
    flag_value="",
    # No explicit default=: click only treats a bare `--onto` (no value) as flag_value
    # when its own default is still its UNSET sentinel; passing default=None here would
    # disable that (see click.Option.__init__'s _flag_needs_value derivation).
    help="Continue on the feature branch of PLAN (default: the branch builder is on).",
)
@click.argument("plan_path")
@click.pass_context
def implement(ctx: click.Context, plan_path: str, *, cont: bool, force_push: bool, onto: str | None) -> None:
    """Schedule a ticket for implementation: transition to implementing and push."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        config.require_project_dir()
        layout = config.layout()
        plan = _resolve_planner_plan(plan_path, config, "implement")
        _enforce_expected_state(plan, "implement")
        builder_repo = layout.git_repo_root(WorktreeName.builder)
        canon_repo = layout.git_repo_root(WorktreeName.canon)
        require(not git_index_lock_present(builder_repo), f"leftover git lock in {builder_repo}; remove it first.")
        require(not git_index_lock_present(canon_repo), f"leftover git lock in {canon_repo}; remove it first.")
        if plan.state == PlanState.PLANNING:
            planner_repo = layout.git_repo_root(WorktreeName.planner)
            require(not git_index_lock_present(planner_repo), f"leftover git lock in {planner_repo}; remove it first.")
        require(
            not git_status_porcelain(builder_repo),
            "builder working tree is dirty; commit or discard changes before implement",
        )

        if onto is not None:
            base_plan = _resolve_onto_base_plan(layout, builder_repo, onto)
            prior_stack = _check_onto_preconditions(layout, plan, base_plan, builder_repo)
            feature_branch = branch_for_plan(layout, base_plan)
            _require_builder_not_on_unfinished_stack(layout, builder_repo, feature_branch)

            resume = ResumePlan(title="implement --onto")
            _classify_implement_onto_steps(resume, plan, layout, builder_repo, feature_branch)
            resume.render()
            if resume.abort_unless_continue(cont=cont):
                ctx.exit(1)
                return

            new_plan = _run_implement_onto_steps(
                resume,
                plan,
                layout,
                builder_repo,
                feature_branch,
                base_plan,
                config,
                force_push=force_push,
                dry_run=dry_run,
            )
            next_step = _command_next_step(config, PlanState.TODO, "implement", new_plan.name)
            _write_script_audit_log(
                config,
                "implement",
                dry_run=dry_run,
                input_plan=new_plan.name,
                stacked_on=[member.name for member in prior_stack],
                next_step=next_step,
            )
            _print_stack_note(feature_branch, [*prior_stack, new_plan])
        else:
            feature_branch = branch_for_plan(layout, plan)
            _require_builder_not_on_unfinished_stack(layout, builder_repo, feature_branch)

            resume = ResumePlan(title="implement")
            _classify_implement_steps(resume, plan, layout, builder_repo, feature_branch)
            resume.render()
            if resume.abort_unless_continue(cont=cont):
                ctx.exit(1)
                return

            new_plan = _run_implement_steps(
                resume,
                plan,
                layout,
                builder_repo,
                feature_branch,
                config,
                force_push=force_push,
                dry_run=dry_run,
            )
            next_step = _command_next_step(config, PlanState.TODO, "implement", new_plan.name)
            _write_script_audit_log(config, "implement", dry_run=dry_run, input_plan=new_plan.name, next_step=next_step)

        print_next_step(next_step)
        if not dry_run:
            time.sleep(config.tmux.prompt_send_delay)
        _rotate_best_effort(config, AgentName.builder, dry_run=dry_run)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("validate")
@click.pass_context
@click.option(
    "--target-repo",
    type=click.Choice(WorktreeName),
    default=None,
    help="Override the target repo (default: inferred from plan state).",
)
@click.argument("plan_path")
def validate(
    ctx: click.Context,
    plan_path: str,
    target_repo: WorktreeName | None,
) -> None:
    """Run pre-merge verification checks on the plan's branch."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    if config.project_dir is None:
        error_console.print("[red]Error: not inside a workflow project directory.[/red]")
        ctx.exit(1)
        return

    try:
        plan = resolve_plan(plan_path, config=config)
    except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return

    target = infer_target_worktree(plan, config, target_repo)
    console.print(f"Target repo: [bold]{target}[/bold]")

    if dry_run:
        for name in check_names_for_mode(config.layout().metadata.mode):
            console.print(f"[dim][-] {name}: skipped in dry-run[/dim]")
        return

    try:
        results = run_validate(plan, config, target_worktree_override=target)
    except WorkflowError as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return

    _print_results(results)
    has_failures = any(result.status == "fail" for result in results)
    if not has_failures:
        next_step = _command_next_step(config, plan.state, "validate", plan.name)
        print_next_step(next_step)
        _write_script_audit_log(config, "validate", dry_run=dry_run, input_plan=plan.name, next_step=next_step)
    ctx.exit(1 if has_failures else 0)


def _print_results(results: list[CheckResult]) -> None:
    for result in results:
        label = f"{result.check_name} ({result.repo})" if result.repo is not None else result.check_name
        if result.status == "pass":
            console.print(f"[green][✓][/green] {label}: {result.message}")
        elif result.status == "warn":
            console.print(f"[yellow][!][/yellow] {label}: {result.message}")
            for detail in result.details:
                console.print(f"    {detail}")
        else:
            console.print(f"[red][✗][/red] {label}: {result.message}")
            for detail in result.details:
                console.print(f"    {detail}")


# Step-name constants: the join key between _classify_check_steps and check()'s run phase.
_STEP_CHECK_CHECKOUT_REVIEWER = "checkout branch in reviewer"
_STEP_CHECK_CHECKOUT_MAIN_BUILDER = "checkout main in builder"


def _classify_push_set_steps(resume: ResumePlan, push_set: list[RepoPushInfo]) -> None:
    """Add a ``push {repo}`` step for every non-docs repo in *push_set*.

    Shared between ``check`` and ``accept``: the docs repo keeps its own dedicated named
    steps (transition commit, transfer to main) in each command's own classify function.
    """
    for info in _code_repo_entries(push_set):
        resume.will_run(f"push {info.repo}")


def _run_check_handoff(  # noqa: PLR0913
    resume: ResumePlan,
    layout: Layout,
    target_repo: AgentName,
    target_repo_path: Path,
    reviewer_repo: Path,
    builder_repo: Path,
    branch: str,
    push_set: list[RepoPushInfo],
    *,
    dry_run: bool,
) -> None:
    """Push *target_repo* (and every push-set repo), then fetch/checkout reviewer, park builder."""
    git_push(target_repo_path, dry_run=dry_run)
    _run_repo_push_set(push_set, layout, WorktreeName(target_repo), dry_run=dry_run)
    git_fetch(reviewer_repo, dry_run=dry_run)
    _fetch_code_repos(layout, WorktreeName.reviewer, dry_run=dry_run)
    if resume.should_run(_STEP_CHECK_CHECKOUT_REVIEWER):
        _ensure_branch_checked_out(reviewer_repo, branch, dry_run=dry_run)
    git_pull(reviewer_repo, dry_run=dry_run)
    # checkout main in builder to make it ready for the next task
    if resume.should_run(_STEP_CHECK_CHECKOUT_MAIN_BUILDER):
        _ensure_branch_checked_out(builder_repo, "main", dry_run=dry_run)
    git_pull(builder_repo, dry_run=dry_run)


def _classify_check_steps(
    resume: ResumePlan,
    stack: list[Plan],
    layout: Layout,
    target_repo: AgentName,
    branch: str,
) -> None:
    for member in stack:
        step = f"transition {member.name} to checking"
        target_path = layout.plans_root(WorktreeName(target_repo)) / "checking" / member.path.name
        if target_path.exists():
            resume.satisfied(step, "plan file already in checking/")
        else:
            resume.will_run(step)

    resume.will_run(f"push {target_repo} branch")
    resume.will_run("fetch reviewer")

    reviewer_repo = layout.git_repo_root(WorktreeName.reviewer)
    if git_current_branch(reviewer_repo) == branch:
        resume.satisfied(_STEP_CHECK_CHECKOUT_REVIEWER, "reviewer already on branch")
    else:
        resume.will_run(_STEP_CHECK_CHECKOUT_REVIEWER)
    resume.will_run("pull reviewer")

    if git_current_branch(layout.git_repo_root(WorktreeName.builder)) == "main":
        resume.satisfied(_STEP_CHECK_CHECKOUT_MAIN_BUILDER, "builder already on main")
    else:
        resume.will_run(_STEP_CHECK_CHECKOUT_MAIN_BUILDER)


@register_script("check")
@click.option(
    "--target-repo",
    type=click.Choice(AgentName),
    default=AgentName.builder,
    show_default=True,
    help="Repo whose sandbox and branch should be pushed.",
)
@click.option("--force-validation", is_flag=True, help="Continue even when validation fails")
@click.argument("plan_path")
@click.pass_context
def check(ctx: click.Context, plan_path: str, target_repo: AgentName, *, force_validation: bool) -> None:
    """Hand off a ticket from builder to reviewer: push branch, fetch in reviewer."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        config.require_project_dir()
        layout = config.layout()
        plan = resolve_plan(plan_path, config=config, command_name="check")
        _enforce_expected_state(plan, "check")
        _require_is_stack_tip(layout, plan)
        target_repo_path = layout.git_repo_root(WorktreeName(target_repo))
        reviewer_repo = layout.git_repo_root(WorktreeName.reviewer)
        builder_repo = layout.git_repo_root(WorktreeName.builder)
        require(
            not git_index_lock_present(target_repo_path),
            f"leftover git lock in {target_repo_path}; remove it first.",
        )
        require(not git_index_lock_present(reviewer_repo), f"leftover git lock in {reviewer_repo}; remove it first.")
        _require_clean_repos(layout, WorktreeName(target_repo))
        stack = resolve_stack(layout, plan)
        branch = branch_for_plan(layout, plan)
        if plan.state == PlanState.IMPLEMENTING:
            # Equality, not just the feat/* shape: `check` pushes whatever *target_repo* is
            # on but hands reviewer the plan's recorded branch, so the two must be the same.
            current_branch = git_current_branch(target_repo_path)
            require(
                current_branch == branch,
                f"{target_repo} must be on {branch!r} (the branch of {plan.name}), "
                f"currently on {current_branch!r}.",
            )

        # Fetches bare for every repo in *target_repo*'s worktree, so the push set below is
        # accurate (a stale fetch would silently under-count and drop a repo to push).
        push_set = _compute_push_set(layout, WorktreeName(target_repo))
        _require_pushable(
            push_set,
            layout,
            WorktreeName(target_repo),
            protect_default_branch=_is_oob(layout),
            force_push=False,
        )

        # On a resume where a prior run already transitioned the plan to checking, these
        # checks either already passed or are moot (builder may already be on main); skip
        # them unconditionally rather than re-running pre-commit/validate against 'main'.
        # An operator who wants to re-run validation post-transition can use `workflow validate`.
        if plan.state == PlanState.IMPLEMENTING:
            _run_repo_push_set_hooks(config, push_set, layout, target_repo, dry_run=dry_run)
            results = run_validate(plan, config, target_worktree_override=target_repo, dry_run=dry_run)
            if not dry_run:
                _print_results(results)
                if not force_validation and any(result.status == "fail" for result in results):
                    ctx.exit(1)
                    return

        _print_push_set_table(push_set)
        resume = ResumePlan(title="check")
        _classify_check_steps(resume, stack, layout, target_repo, branch)
        _classify_push_set_steps(resume, push_set)
        resume.render()

        new_stack = _transition_stack_members(
            stack,
            PlanState.CHECKING,
            layout,
            worktree_override=WorktreeName(target_repo),
            commit_worktree=WorktreeName(target_repo),
            dry_run=dry_run,
        )
        new_plan = new_stack[-1]
        _run_check_handoff(
            resume,
            layout,
            target_repo,
            target_repo_path,
            reviewer_repo,
            builder_repo,
            branch,
            push_set,
            dry_run=dry_run,
        )
        next_step = _command_next_step(config, PlanState.IMPLEMENTING, "check", new_plan.name)
        _write_script_audit_log(
            config,
            "check",
            dry_run=dry_run,
            input_plan=new_plan.name,
            output_plans=[member.name for member in new_stack[:-1]],
            next_step=next_step,
        )
        _print_stack_note(branch, new_stack)
        print_next_step(next_step)
        if not dry_run:
            time.sleep(config.tmux.prompt_send_delay)
        _rotate_best_effort(config, AgentName.reviewer, dry_run=dry_run)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("push")
@click.argument("plan_path")
@click.pass_context
def push(ctx: click.Context, plan_path: str) -> None:
    """Push manual edits for a plan to bare. No state transition."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        config.require_project_dir()
        layout = config.layout()
        plan = resolve_plan(
            plan_path,
            config=config,
            command_name="push",
            git_provides_worktree=True,
        )
        repo = plan.worktree
        repo_path = layout.git_repo_root(repo)
        require(not git_index_lock_present(repo_path), f"leftover git lock in {repo_path}; remove it first.")
        if not dry_run and repo != WorktreeName.canon:
            branch = git_current_branch(repo_path)
            if branch == "main":
                msg = f"push: {repo} is on 'main'; expected a feature branch"
                raise WorkflowError(msg)
        git_push(repo_path, dry_run=dry_run)
        script_cmd = find_command(config, plan.state, "push")
        if (
            isinstance(script_cmd, _ScriptCommand)
            and script_cmd.pull_target is not None
            and script_cmd.pull_target != repo
        ):
            # A whole-project sync, not a plain pull of pull_target: pull_target is
            # typically on a feature branch (e.g. planner mid-plan), where 'git pull'
            # advances that branch rather than 'main'.
            _sync_repos_best_effort(layout, dry_run=dry_run)
        next_step = _command_next_step(config, plan.state, "push", plan.name)
        _write_script_audit_log(config, "push", dry_run=dry_run, input_plan=plan.name, next_step=next_step)
        print_next_step(next_step)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


# Step-name constant: the join key between _classify_accept_steps and accept()'s run phase.
_STEP_ACCEPT_TRANSFER = "transfer reviewer branch to main"
_STEP_ACCEPT_REBASE = "rebase reviewer branch onto main"


def _classify_accept_steps(
    resume: ResumePlan,
    stack: list[Plan],
    layout: Layout,
    branch: str,
    push_set: list[RepoPushInfo],
) -> None:
    canon_repo = layout.git_repo_root(WorktreeName.canon)

    # transfer_branch_to_main is one guarded+idempotent unit (Fix 3); classify it as a
    # single step so classify- and run-granularity stay aligned (see plan 020.1 Fix 3
    # granularity note). A branch already deleted from bare (by a prior accept's
    # cleanup) has nothing left to transfer, regardless of what a now-meaningless
    # ancestry check against a dangling bare/<branch> ref would say.
    transfer_will_run = git_ref_exists_remote(canon_repo, branch) and not git_is_ancestor(
        canon_repo,
        f"{BARE_REMOTE}/{branch}",
        "main",
    )
    if transfer_will_run:
        resume.will_run(_STEP_ACCEPT_TRANSFER)
    else:
        resume.satisfied(_STEP_ACCEPT_TRANSFER, "bare/<branch> already merged into main")
    _classify_rebase_step(
        resume,
        _STEP_ACCEPT_REBASE,
        layout.git_repo_root(WorktreeName.reviewer),
        branch=branch,
        transfer_will_run=transfer_will_run,
    )

    for member in stack:
        step = f"transition {member.name} to done"
        target_path = layout.plans_root(WorktreeName.canon) / "done" / member.path.name
        if target_path.exists():
            resume.satisfied(step, "plan file already in done/")
        else:
            resume.will_run(step)

    _classify_push_set_steps(resume, push_set)
    resume.will_run("push canon")


@register_script("accept")
@click.option(
    "--force-push",
    is_flag=True,
    help="Allow force-pushing a branch an earlier run rebased but failed to publish",
)
@click.argument("plan_path")
@click.pass_context
def accept(ctx: click.Context, plan_path: str, *, force_push: bool) -> None:
    """Accept a reviewed ticket: push from reviewer, merge to main, transition to done."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        config.require_project_dir()
        layout = config.layout()
        plan = resolve_plan(plan_path, config=config, command_name="accept")
        _enforce_expected_state(plan, "accept")
        _require_is_stack_tip(layout, plan)
        reviewer_repo = layout.git_repo_root(WorktreeName.reviewer)
        canon_repo = layout.git_repo_root(WorktreeName.canon)
        require(not git_index_lock_present(reviewer_repo), f"leftover git lock in {reviewer_repo}; remove it first.")
        require(not git_index_lock_present(canon_repo), f"leftover git lock in {canon_repo}; remove it first.")
        require(git_current_branch(canon_repo) == "main", "accept: canon must be on 'main'.")
        _require_clean_repos(layout, WorktreeName.reviewer)
        # The feature branch follows *plan*'s resolved branch (set by `implement`), not
        # whatever reviewer happens to be checked out to right now -- a prior run may have
        # already advanced reviewer to 'main' (decision 2).
        branch = branch_for_plan(layout, plan)
        if plan.state == PlanState.CHECKING:
            current_branch = git_current_branch(reviewer_repo)
            if current_branch == "main":
                msg = "accept: reviewer is on 'main'; expected a feature branch"
                raise WorkflowError(msg)

        stack = resolve_stack(layout, plan)
        # Fetches bare for every repo in the reviewer worktree, so the push set below is
        # accurate (a stale fetch would silently under-count and drop a repo to push).
        push_set = _compute_push_set(layout, WorktreeName.reviewer)
        _require_pushable(
            push_set,
            layout,
            WorktreeName.reviewer,
            protect_default_branch=False,
            force_push=force_push,
        )
        _print_push_set_table(push_set)
        if _is_oob(layout):
            # Reviewer commits in code repos get the same per-repo gate `check` gave the builder's.
            _run_repo_push_set_hooks(
                config,
                _code_repo_entries(push_set),
                layout,
                WorktreeName.reviewer,
                dry_run=dry_run,
            )

        resume = ResumePlan(title="accept")
        _classify_accept_steps(resume, stack, layout, branch, push_set)
        resume.render()

        # Gated on should_run: unlike a fresh run, a resume where this already landed must
        # not re-transfer a branch already merged into main.
        if resume.should_run(_STEP_ACCEPT_TRANSFER):
            result = transfer_branch_to_main(
                reviewer_repo,
                canon_repo,
                branch=branch,
                allow_rebase=resume.should_run(_STEP_ACCEPT_REBASE),
                force_push=force_push,
                already_fetched=True,  # the rebase classification step just fetched
                other_worktrees=_other_worktrees(layout, WorktreeName.reviewer),
                post_rebase_verify=lambda: _run_docs_hook(config, layout, WorktreeName.reviewer, dry_run=dry_run),
                dry_run=dry_run,
            )
            _warn_if_rebased(result, branch)
        _run_repo_push_set(push_set, layout, WorktreeName.reviewer, force_push=force_push, dry_run=dry_run)
        new_stack = _transition_stack_members(
            stack,
            PlanState.DONE,
            layout,
            worktree_override=WorktreeName.canon,
            commit_worktree=WorktreeName.canon,
            dry_run=dry_run,
        )
        new_plan = new_stack[-1]
        git_push(canon_repo, dry_run=dry_run)
        # Parks reviewer on 'main' (fixing issue 1's ordering hazard) only after the
        # done-transition commit has itself reached bare.
        _sync_repos_best_effort(layout, park=WorktreeName.reviewer, dry_run=dry_run)
        builder_repo = layout.git_repo_root(WorktreeName.builder)
        delete_merged_branch(canon_repo, (reviewer_repo, builder_repo), branch, dry_run=dry_run)
        next_step = _command_next_step(config, PlanState.CHECKING, "accept", new_plan.name)
        _write_script_audit_log(
            config,
            "accept",
            dry_run=dry_run,
            input_plan=new_plan.name,
            output_plans=[member.name for member in new_stack[:-1]],
            next_step=next_step,
        )
        _print_stack_note(branch, new_stack)
        print_next_step(next_step)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


# Branch-name prefix of the branch a rejected plan leaves behind, if any -- states with no
# open branch (intent/todo/done/rejected) map to nothing, so 'reject' prints no
# unmerged-branch note.
_BRANCH_PREFIX_FOR_STATE: dict[PlanState, str] = {
    PlanState.PLANNING: "plan",
    PlanState.IMPLEMENTING: "feat",
    PlanState.CHECKING: "feat",
}


@register_script("reject")
@click.option(
    "--target-repo",
    type=click.Choice(WorktreeName),
    default=WorktreeName.canon,
    show_default=True,
    help="Repo containing the plan to reject.",
)
@click.argument("plan_path")
@click.pass_context
def reject(ctx: click.Context, plan_path: str, target_repo: WorktreeName) -> None:
    """Transition a ticket to the rejected state."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        config.require_project_dir()
        layout = config.layout()
        plan = resolve_plan(plan_path, config=config)
        _enforce_expected_state(plan, "reject")
        target_repo_path = layout.git_repo_root(target_repo)
        require(
            not git_index_lock_present(target_repo_path),
            f"leftover git lock in {target_repo_path}; remove it first.",
        )
        # Read the rollback context before the transition (no-cascade: only this plan moves).
        fm = read_plan_frontmatter(layout, plan)
        child = stack_child(layout, plan)
        # _transition_if_needed (idempotent) rather than a raw transition_plan + git_commit,
        # and no early-exit shortcut on plan.state == REJECTED: a fully-qualified path can
        # resolve to REJECTED purely from the file's current directory even when the git mv
        # landed but the commit didn't (an interrupted prior run) -- _transition_if_needed's
        # own idempotent skip-the-mv-still-commit logic is the single source of truth for
        # both "nothing to do" and "finish the interrupted commit".
        _transition_if_needed(
            plan,
            PlanState.REJECTED,
            layout,
            worktree_override=target_repo,
            dry_run=dry_run,
        )
        git_push(target_repo_path, dry_run=dry_run)
        # Refresh only -- never park: the agent owning the abandoned branch may still be
        # mid-work on it.
        _sync_repos_best_effort(layout, dry_run=dry_run)
        repo_branch = fm.branches.get(DOCS_REPO) if fm is not None and fm.onto is not None else None
        if fm is not None and repo_branch is not None:
            console.print(f"[yellow]Note:[/yellow] {fm.onto} and this plan share the branch {repo_branch.branch}.")
            console.print("To drop this plan's commits from the branch:")
            # The checkout first: after `check`, builder sits on 'main', where an
            # unqualified reset would rewind main instead of the shared branch.
            builder_repo = layout.git_repo_root(WorktreeName.builder)
            console.print(f"    git -C {builder_repo} checkout {repo_branch.branch}")
            console.print(f"    git -C {builder_repo} reset --hard {repo_branch.base}")
        else:
            branch_prefix = _BRANCH_PREFIX_FOR_STATE.get(plan.state)
            if branch_prefix == "feat":
                branch = branch_for_plan(layout, plan)
                console.print(f"[yellow]Note:[/yellow] {branch} is left unmerged and can be deleted manually.")
            elif branch_prefix is not None:
                console.print(
                    f"[yellow]Note:[/yellow] {branch_prefix}/{plan.name} is left unmerged and can be deleted manually.",
                )
        if child is not None:
            console.print(
                f"[yellow]Warning:[/yellow] {child.name} is stacked on this plan; its commits stay under "
                f"{child.name}'s work.",
            )
        _write_script_audit_log(config, "reject", dry_run=dry_run, input_plan=plan.name)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("canon_pre_commit")
@click.pass_context
def canon_pre_commit(ctx: click.Context) -> None:
    """Run the pre-commit hook against canon in a throwaway container (oob: once per enabled repo)."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]
    try:
        layout = config.layout()
        if layout.metadata.mode == ProjectMode.oob:
            for repo in layout.repos():
                if _hook_enabled(layout, repo):
                    _run_pre_commit_hook(config, WorktreeName.canon, dry_run=dry_run, repo=repo)
                else:
                    console.print(_skip_hook_message(repo))
        else:
            _run_pre_commit_hook(config, WorktreeName.canon, dry_run=dry_run)
        _write_script_audit_log(config, "canon_pre_commit", dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


def complete_standard_agents(ctx: click.Context, param: str, incomplete: str) -> list[str]:
    """Shell completion for the go target param."""
    del ctx
    del param
    valid = {agent.name for agent in AgentName} | {"manual"}
    return [k for k in valid if k.startswith(incomplete)]


@register_script("go")
@click.argument("target", shell_complete=complete_standard_agents)
@click.pass_context
def go(ctx: click.Context, target: str) -> None:
    """Rotate the control pane to a window and print the target repo's git status."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        config.require_project_dir()
        layout = config.layout()
        if not dry_run:
            assert_in_workflow_session(config)
        valid = {agent.name for agent in config.agents} | {"manual"}
        if target not in valid:
            msg = f"invalid go target {target!r}; choose from {sorted(valid)}"
            raise WorkflowError(msg)
        if target == "manual":
            window = "builder-shell"
            repo = _manual_shell_repo(config)
        else:
            window = target
            repo = WorktreeName(target)
        rotate_control_pane(config, window, dry_run=dry_run)
        if repo is not None:
            git_status(layout.git_repo_root(repo), dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)
