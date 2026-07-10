"""Workflow script commands: project setup, state transitions, and git operations."""

from __future__ import annotations

import importlib.resources
import os
import re
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import click
import jinja2

from workflow.audit import write_audit_log
from workflow.commands.agents_md import _prepare_agents_md_update, generate_and_write_agents_md
from workflow.config import WORKFLOW_ROOT_MARKER, AgentConfig, WorkflowConfig
from workflow.config import ScriptCommand as _ScriptCommand
from workflow.exceptions import (
    AgentsMdGenerationError,
    AmbiguousPlanError,
    PlanCollisionError,
    PlanNotFoundError,
    WorkflowError,
)
from workflow.git_helpers import (
    git_add,
    git_branch_exists,
    git_checkout,
    git_commit,
    git_commit_if_changed,
    git_config_get,
    git_current_branch,
    git_fetch,
    git_index_lock_present,
    git_is_ancestor,
    git_is_valid_repo,
    git_merge,
    git_pull,
    git_push,
    git_remote_url,
    git_status,
    git_status_porcelain,
    transfer_branch_to_main,
)
from workflow.models import AuditLogEntry, Plan, PlanState
from workflow.names import AgentName, RepoName
from workflow.next_step import find_command, find_next_step, find_reminder_name, format_next_step_invocation
from workflow.plan_resolver import next_intent_number, resolve_plan
from workflow.registry import register_script
from workflow.shell import console, error_console, print_workflow_error, run_command
from workflow.state import transition_plan
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
from workflow.validate import CHECK_NAMES, CheckResult, infer_target_repo
from workflow.validate import validate as run_validate

# Hard state constraints for script commands
# Each command also accepts its own transition's *target* state, so a plan already
# moved by a prior (partially-failed) run of the same command can be resumed.
_EXPECTED_STATES_FOR: dict[str, set[PlanState]] = {
    "todo": {PlanState.PLANNING},
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


def _require_project_dir(config: WorkflowConfig) -> Path:
    if config.project_dir is None:
        msg = "Error: not inside a workflow project directory."
        raise WorkflowError(msg)
    return config.project_dir


def _write_script_audit_log(
    config: WorkflowConfig,
    command: str,
    *,
    dry_run: bool,
    input_plan: str | None = None,
    output_plans: list[str] | None = None,
) -> None:
    if dry_run:
        return
    project_dir = _require_project_dir(config)
    write_audit_log(
        project_dir,
        AuditLogEntry(
            timestamp=datetime.now(UTC),
            actor="human",
            command=command,
            input_plan=input_plan,
            output_plans=output_plans or [],
        ),
        dry_run=dry_run,
    )


def _print_command_reminder(config: WorkflowConfig, state: PlanState, command_name: str, plan_name: str) -> None:
    reminder = find_reminder_name(config, state, command_name)
    if reminder is None:
        return
    next_step = find_next_step(config, state, command_name)
    formatted = format_next_step_invocation(reminder, plan_name, command=next_step)
    console.print(f"Next step: [bold]{formatted}[/bold]")


def _open_in_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)], check=True)  # noqa: S603


def _run_pre_commit_in_sandbox(config: WorkflowConfig, repo_name: str, *, dry_run: bool) -> None:
    if repo_name == RepoName.canon:
        msg = "Target repo 'canon' has no sandbox container."
        raise WorkflowError(msg)
    project_name = config.project_name or _require_project_dir(config).name
    container_name = f"pi-{project_name}-{repo_name}"
    repo_path = shlex.quote(f"/home/auggie/projects/{project_name}/{repo_name}")
    run_command(
        [
            "docker", "exec",
            "--user",
            "auggie",
            "--workdir",
            repo_path,
            container_name,
            "bash",
            "-lc",
            "./.git-pre-commit-hook"
        ],
        dry_run=dry_run,
    )


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


def _manual_shell_repo(config: WorkflowConfig) -> RepoName | None:
    """Return the repo backing the manual shell window, or ``None`` if no agent sets it."""
    for agent in config.agents:
        if agent.manual_shell and agent.name in RepoName.__members__:
            return RepoName(agent.name)
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


def _agent_push_config_ok(repo_path: Path) -> bool:
    """Return whether push config and the pre-commit hook are already set up in *repo_path*."""
    return (
        git_config_get(repo_path, "push.autoSetupRemote") == "true"
        and git_config_get(repo_path, "push.default") == "current"
        and _hook_symlink_ok(repo_path)
    )


def _ensure_agent_push_config(repo_path: Path, *, dry_run: bool) -> None:
    """Configure push behaviour and install the pre-commit hook symlink in *repo_path*.

    Push config lets feature branches push to a same-named remote branch without
    requiring a pre-existing upstream. Each setting is only (re)applied when not
    already correct, so this is safe to call on every ``init`` run.
    """
    if git_config_get(repo_path, "push.autoSetupRemote") != "true":
        run_command(["git", "config", "push.autoSetupRemote", "true"], cwd=repo_path, dry_run=dry_run)
    if git_config_get(repo_path, "push.default") != "current":
        run_command(["git", "config", "push.default", "current"], cwd=repo_path, dry_run=dry_run)
    _ensure_pre_commit_hook(repo_path, dry_run=dry_run)


def _wire_canon_to_bare(canon_repo: Path, bare_path: Path, *, dry_run: bool) -> None:
    """Add/configure the ``origin`` remote in canon (pointing at bare) and push HEAD."""
    if git_remote_url(canon_repo, "origin") is None:
        run_command(["git", "remote", "add", "origin", str(bare_path)], cwd=canon_repo, dry_run=dry_run)
    if git_config_get(canon_repo, "push.autoSetupRemote") != "true":
        run_command(["git", "config", "push.autoSetupRemote", "true"], cwd=canon_repo, dry_run=dry_run)
    if git_config_get(canon_repo, "push.default") != "current":
        run_command(["git", "config", "push.default", "current"], cwd=canon_repo, dry_run=dry_run)
    run_command(["git", "push", "-u", "origin", "HEAD"], cwd=canon_repo, dry_run=dry_run)


def _classify_agents_md_step(resume: ResumePlan, config: WorkflowConfig, project_dir: Path) -> None:
    update = _prepare_agents_md_update(config.model_copy(update={"project_dir": project_dir}))
    if update.agents_md_unchanged:
        resume.satisfied("AGENTS.md committed", "already up to date")
    else:
        resume.will_run("AGENTS.md committed")


def _dir_needs_clone(path: Path) -> bool:
    """Return whether *path* still needs to be created via ``git clone`` (missing or empty).

    ``git clone`` tolerates cloning into an existing but empty directory, so an
    empty directory is equivalent to "not yet cloned", not a rough partial state.
    """
    return not path.exists() or not any(path.iterdir())


def _classify_bare_clone_step(resume: ResumePlan, bare_path: Path) -> None:
    if _dir_needs_clone(bare_path):
        resume.will_run("bare clone")
    else:
        resume.satisfied("bare clone", "bare is a valid clone of canon")


def _classify_agent_clone_steps(resume: ResumePlan, config: WorkflowConfig, project_dir: Path, bare_path: Path) -> None:
    for agent in config.agents:
        agent_repo = project_dir / agent.name
        step = f"clone {agent.name}"
        if _dir_needs_clone(agent_repo):
            resume.will_run(step)
        elif git_is_valid_repo(agent_repo) and git_remote_url(agent_repo, "origin") == str(bare_path):
            resume.needs_continue(step, "clone exists; pass --continue to keep it as-is")
        else:
            msg = f"{agent_repo} exists but is not a valid clone of bare; remove it or fix it."
            raise WorkflowError(msg)


def _classify_agent_configure_steps(resume: ResumePlan, config: WorkflowConfig, project_dir: Path) -> None:
    for agent in config.agents:
        agent_repo = project_dir / agent.name
        step = f"configure {agent.name}"
        if agent_repo.exists() and _agent_push_config_ok(agent_repo):
            resume.satisfied(step, "push config and pre-commit hook already set up")
        else:
            resume.will_run(step)


def _classify_wire_canon_step(resume: ResumePlan, canon_repo: Path, bare_path: Path) -> None:
    origin_url = git_remote_url(canon_repo, "origin")
    if origin_url is not None and Path(origin_url).resolve() != bare_path.resolve():
        msg = f"canon 'origin' remote points at {origin_url!r}, expected {bare_path}; fix or remove it."
        raise WorkflowError(msg)
    if origin_url is not None:
        resume.satisfied("wire canon to bare", "origin already points at bare")
    else:
        resume.will_run("wire canon to bare")


def _classify_marker_step(resume: ResumePlan, project_dir: Path) -> None:
    if (project_dir / WORKFLOW_ROOT_MARKER).exists():
        resume.satisfied("workflow marker", "marker file exists")
    else:
        resume.will_run("workflow marker")


def _classify_venv_steps(resume: ResumePlan, config: WorkflowConfig, project_dir: Path) -> None:
    for agent in config.agents:
        venv_dir = project_dir / agent.name / ".venv"
        step = f"venv {agent.name}"
        if venv_dir.is_dir():
            resume.needs_continue(step, "exists; pass --continue to recreate (destructive)")
        else:
            resume.will_run(step)


def _classify_init_steps(
    resume: ResumePlan,
    config: WorkflowConfig,
    project_dir: Path,
    canon_repo: Path,
    bare_path: Path,
) -> None:
    """Populate *resume* with a classification for every init step (read-only)."""
    _classify_agents_md_step(resume, config, project_dir)
    _classify_bare_clone_step(resume, bare_path)
    _classify_agent_clone_steps(resume, config, project_dir, bare_path)
    _classify_agent_configure_steps(resume, config, project_dir)
    _classify_wire_canon_step(resume, canon_repo, bare_path)
    _classify_marker_step(resume, project_dir)
    _classify_venv_steps(resume, config, project_dir)


def _check_init_preconditions(canon_repo: Path, bare_path: Path) -> None:
    """Fail fast on cheaply-detectable causes of a broken ``init`` run."""
    if not (canon_repo / ".git").is_dir():
        msg = f"{canon_repo} is not a git repository (no .git/ found)."
        raise WorkflowError(msg)
    if not (canon_repo / ".git-pre-commit-hook").is_file():
        msg = f"{canon_repo / '.git-pre-commit-hook'} must exist."
        raise WorkflowError(msg)
    require(not git_index_lock_present(canon_repo), f"leftover git lock in {canon_repo}; remove it first.")
    if bare_path.exists():
        require(
            git_is_valid_repo(bare_path, bare=True),
            f"{bare_path} exists but is not a valid bare clone; remove it or fix it.",
        )


def _run_init_steps(
    config: WorkflowConfig,
    project_dir: Path,
    canon_repo: Path,
    bare_path: Path,
    *,
    dry_run: bool,
) -> None:
    """Perform the (already-classified) init steps, each idempotently skipping its own no-op."""
    # Generate AGENTS.md into canon before the bare clone so all agent clones inherit it.
    if not dry_run:
        try:
            generate_and_write_agents_md(config.model_copy(update={"project_dir": project_dir}))
            git_add(canon_repo, Path("AGENTS.md"), dry_run=False)
            # no_verify: at init time the pre-commit hook would run pytest before the
            # workflow package is even installed in canon; bypassing is required here.
            git_commit_if_changed(canon_repo, "chore: generate initial AGENTS.md", dry_run=False, no_verify=True)
        except AgentsMdGenerationError as exc:
            raise WorkflowError(str(exc)) from exc

    if _dir_needs_clone(bare_path):
        run_command(["git", "clone", "--bare", RepoName.canon, "bare"], cwd=project_dir, dry_run=dry_run)

    for agent in config.agents:
        agent_repo = project_dir / agent.name
        if _dir_needs_clone(agent_repo):
            run_command(["git", "clone", "bare", agent.name], cwd=project_dir, dry_run=dry_run)

    for agent in config.agents:
        _ensure_agent_push_config(project_dir / agent.name, dry_run=dry_run)

    _wire_canon_to_bare(canon_repo, bare_path, dry_run=dry_run)

    if not dry_run and not (project_dir / WORKFLOW_ROOT_MARKER).exists():
        (project_dir / WORKFLOW_ROOT_MARKER).touch()

    # Create venvs (always via --reset: cheap on first creation, destructive-but-
    # confirmed via --continue when a venv already exists; see _classify_init_steps).
    script = _find_docker_script(project_dir)
    project_name = config.project_name or project_dir.name
    for agent in config.agents:
        run_command(
            _create_venv_command(script, project_name, project_dir, agent),
            cwd=project_dir,
            dry_run=dry_run,
        )


@register_script("init")
@click.option("--continue", "cont", is_flag=True, help="Proceed past rough (unverifiable) partial state.")
@click.pass_context
def init(ctx: click.Context, *, cont: bool) -> None:
    """Initialise the project directory: create bare and agent git clones."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]
    project_dir = (config.project_dir or Path.cwd()).resolve()
    canon_repo = project_dir / RepoName.canon
    bare_path = project_dir / "bare"

    try:
        _check_init_preconditions(canon_repo, bare_path)

        resume = ResumePlan(title="init")
        _classify_init_steps(resume, config, project_dir, canon_repo, bare_path)
        resume.render()
        if resume.abort_unless_continue(cont=cont):
            ctx.exit(1)
            return

        _run_init_steps(config, project_dir, canon_repo, bare_path, dry_run=dry_run)
        _write_script_audit_log(config.model_copy(update={"project_dir": project_dir}), "init", dry_run=dry_run)
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
    parts = [script, project_name, agent.name, "--project-dir", str(project_dir)]
    if agent.model:
        parts.extend(["--model", agent.model])
    if reset:
        parts.append("--reset")
    return shlex.join(parts)


def _builder_shell_command(script: str, project_name: str, project_dir: Path) -> str:
    """Build the tmux window command string for the builder-shell window."""
    return shlex.join([script, project_name, "builder", "--project-dir", str(project_dir), "--shell"])


def _create_venv_command(script: str, project_name: str, project_dir: Path, agent: AgentConfig) -> list[str]:
    """Build the command string for creating the agent-specific virtual env."""
    return [script, project_name, agent.name, "--project-dir", str(project_dir), "--venv", "--reset"]


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
        project_dir = _require_project_dir(config)
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


@register_script("new")
@click.argument("slug")
@click.pass_context
def new(ctx: click.Context, slug: str) -> None:
    """Create a new intent file and open it in $EDITOR."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        if not _SLUG_RE.fullmatch(slug):
            msg = f"Invalid slug {slug!r}: use lowercase letters, digits, and underscores."
            raise WorkflowError(msg)
        number = next_intent_number(project_dir)
        intent_path = project_dir / RepoName.canon / "docs" / "plans" / "intent" / f"{number:03d}_{slug}.md"
        template_text = (
            importlib.resources.files("workflow.templates").joinpath("intent.md.j2").read_text(encoding="utf-8")
        )
        content = jinja2.Template(template_text).render(number=number, slug=slug)
        if dry_run:
            console.print(f"Would create: {intent_path}")
            console.print(f"Would open in $EDITOR: {intent_path}")
        else:
            intent_path.parent.mkdir(parents=True, exist_ok=True)
            intent_path.write_text(content, encoding="utf-8")
            _open_in_editor(intent_path)
        git_add(
            project_dir / RepoName.canon,
            intent_path.relative_to(project_dir / RepoName.canon),
            dry_run=dry_run,
        )
        _write_script_audit_log(config, "new", dry_run=dry_run, output_plans=[intent_path.stem])
        _print_command_reminder(config, PlanState.INTENT, "new", intent_path.name)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("todo")
@click.argument("plan_path")
@click.option("--force", is_flag=True, help="Continue even when validation fails")
@click.pass_context
def todo(ctx: click.Context, plan_path: str, *, force: bool) -> None:
    """Approve a ticket: push from planner, pull to canon, transition to todo."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, config=config, command_name="todo")
        _enforce_expected_state(plan, "todo")
        results = run_validate(plan, project_dir, config, target_repo_override="planner", dry_run=dry_run)
        if not dry_run:
            _print_results(results)
            if not force and any(result.status == "fail" for result in results):
                ctx.exit(1)
                return

        transfer_branch_to_main(project_dir / RepoName.planner, project_dir / RepoName.canon, dry_run=dry_run)
        new_plan = transition_plan(plan, PlanState.TODO, project_dir, repo_override=RepoName.canon, dry_run=dry_run)
        git_commit(
            project_dir / RepoName.canon,
            _transition_commit_message(plan, PlanState.TODO),
            dry_run=dry_run,
            no_verify=True,
        )
        git_push(project_dir / RepoName.canon, dry_run=dry_run)
        _write_script_audit_log(config, "todo", dry_run=dry_run, input_plan=plan.name)
        _print_command_reminder(config, PlanState.PLANNING, "todo", new_plan.name)
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


def _ensure_branch_checked_out(repo_path: Path, branch: str, *, dry_run: bool) -> None:
    """Check out *branch* in *repo_path* unless it is already the current branch."""
    if git_current_branch(repo_path) != branch:
        git_checkout(repo_path, branch, dry_run=dry_run)


def _transition_if_needed(  # noqa: PLR0913
    plan: Plan,
    target_state: PlanState,
    project_dir: Path,
    *,
    repo_override: str | None = None,
    commit_repo: str | None = None,
    dry_run: bool,
) -> Plan:
    """Move *plan* to *target_state*, skipping the ``git mv`` when already done.

    Always (re)commits via :func:`git_commit_if_changed`, which safely no-ops when
    there is nothing staged. This also finishes a prior run that moved the file but
    was interrupted before committing.
    """
    repo_name = repo_override or plan.path.parts[0]
    target_rel = Path(repo_name) / "docs" / "plans" / target_state.value / plan.path.name
    if (project_dir / target_rel).exists():
        new_plan = plan.model_copy(update={"state": target_state, "path": target_rel})
    else:
        new_plan = transition_plan(plan, target_state, project_dir, repo_override=repo_override, dry_run=dry_run)
    git_commit_if_changed(
        project_dir / (commit_repo or repo_name),
        _transition_commit_message(new_plan, target_state),
        dry_run=dry_run,
        no_verify=True,
    )
    return new_plan


def _classify_implement_steps(
    resume: ResumePlan,
    plan: Plan,
    project_dir: Path,
    builder_repo: Path,
    feature_branch: str,
) -> None:
    if plan.state == PlanState.PLANNING:
        resume.will_run("transfer planner branch to main")
        resume.will_run("transition to todo")
    else:
        resume.satisfied("transfer planner branch to main", "plan already past planning")
        resume.satisfied("transition to todo", "plan already past planning")

    implementing_path = project_dir / RepoName.canon / "docs" / "plans" / "implementing" / plan.path.name
    if implementing_path.exists():
        resume.satisfied("transition to implementing", "plan file already in implementing/")
    else:
        resume.will_run("transition to implementing")

    resume.will_run("push canon")

    # If the feature branch already exists, builder is presumably already checked out to
    # it (and possibly has work in progress) -- fetch/checkout-main/pull must not run, or
    # a rerun on an already-fully-implemented plan would force builder off its own branch.
    branch_exists = git_branch_exists(builder_repo, feature_branch)
    if branch_exists:
        resume.satisfied("fetch builder", "feature branch already exists")
        resume.satisfied("checkout main in builder", "feature branch already exists")
    else:
        resume.will_run("fetch builder")
        if git_current_branch(builder_repo) == "main":
            resume.satisfied("checkout main in builder", "builder already on main")
        else:
            resume.will_run("checkout main in builder")

    step = f"create {feature_branch}"
    if not branch_exists:
        resume.will_run(step)
    elif plan.state == PlanState.IMPLEMENTING:
        resume.satisfied(step, "already created by a prior run")
    else:
        resume.needs_continue(step, "branch exists but plan not yet implementing; pass --continue to accept it as-is")


def _run_implement_steps(
    plan: Plan,
    project_dir: Path,
    builder_repo: Path,
    feature_branch: str,
    *,
    dry_run: bool,
) -> Plan:
    """Run the (already-classified) implement steps, each idempotently skipping its own no-op."""
    if plan.state == PlanState.PLANNING:
        # Inlined, guarded equivalent of transfer_branch_to_main: each git op below is
        # naturally idempotent (safe to always re-run), unlike the shared helper (used
        # unguarded by `todo`, out of scope for resume -- see plan 020 build sequence).
        planner_repo = project_dir / RepoName.planner
        canon_repo = project_dir / RepoName.canon
        planner_branch = git_current_branch(planner_repo)
        git_push(planner_repo, dry_run=dry_run)
        git_pull(canon_repo, dry_run=dry_run)
        git_merge(canon_repo, f"origin/{planner_branch}", dry_run=dry_run)
        git_push(canon_repo, dry_run=dry_run)
        _ensure_branch_checked_out(planner_repo, "main", dry_run=dry_run)
        git_pull(planner_repo, dry_run=dry_run)
        plan = _transition_if_needed(plan, PlanState.TODO, project_dir, repo_override=RepoName.canon, dry_run=dry_run)

    new_plan = _transition_if_needed(plan, PlanState.IMPLEMENTING, project_dir, dry_run=dry_run)

    canon_repo = project_dir / RepoName.canon
    git_push(canon_repo, dry_run=dry_run)
    # Leave an existing feature branch (and any work in progress on it) untouched.
    if not git_branch_exists(builder_repo, feature_branch):
        git_fetch(builder_repo, dry_run=dry_run)
        _ensure_branch_checked_out(builder_repo, "main", dry_run=dry_run)
        git_pull(builder_repo, dry_run=dry_run)
        git_checkout(builder_repo, feature_branch, create=True, dry_run=dry_run)

    return new_plan


@register_script("implement")
@click.option("--continue", "cont", is_flag=True, help="Proceed past rough (unverifiable) partial state.")
@click.argument("plan_path")
@click.pass_context
def implement(ctx: click.Context, plan_path: str, *, cont: bool) -> None:
    """Schedule a ticket for implementation: transition to implementing and push."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, config=config, command_name="implement")
        _enforce_expected_state(plan, "implement")
        builder_repo = project_dir / RepoName.builder
        canon_repo = project_dir / RepoName.canon
        feature_branch = f"feat/{plan.name}"
        require(not git_index_lock_present(builder_repo), f"leftover git lock in {builder_repo}; remove it first.")
        require(not git_index_lock_present(canon_repo), f"leftover git lock in {canon_repo}; remove it first.")
        if plan.state == PlanState.PLANNING:
            planner_repo = project_dir / RepoName.planner
            require(not git_index_lock_present(planner_repo), f"leftover git lock in {planner_repo}; remove it first.")
        require(
            not git_status_porcelain(builder_repo),
            "builder working tree is dirty; commit or discard changes before implement",
        )

        resume = ResumePlan(title="implement")
        _classify_implement_steps(resume, plan, project_dir, builder_repo, feature_branch)
        resume.render()
        if resume.abort_unless_continue(cont=cont):
            ctx.exit(1)
            return

        new_plan = _run_implement_steps(plan, project_dir, builder_repo, feature_branch, dry_run=dry_run)
        _write_script_audit_log(config, "implement", dry_run=dry_run, input_plan=plan.name)
        _print_command_reminder(config, PlanState.TODO, "implement", new_plan.name)
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
    type=click.Choice(RepoName),
    default=None,
    help="Override the target repo (default: inferred from plan state).",
)
@click.argument("plan_path")
def validate(
    ctx: click.Context,
    plan_path: str,
    target_repo: RepoName | None,
) -> None:
    """Run pre-merge verification checks on the plan's branch."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    if config.project_dir is None:
        error_console.print("[red]Error: not inside a workflow project directory.[/red]")
        ctx.exit(1)
        return

    try:
        plan = resolve_plan(plan_path, config.project_dir, config=config)
    except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return

    target = infer_target_repo(plan, config, target_repo)
    console.print(f"Target repo: [bold]{target}[/bold]")

    if dry_run:
        for name in CHECK_NAMES:
            console.print(f"[dim][-] {name}: skipped in dry-run[/dim]")
        return

    try:
        results = run_validate(plan, config.project_dir, config, target_repo_override=target)
    except WorkflowError as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return

    _print_results(results)
    has_failures = any(result.status == "fail" for result in results)
    if not has_failures:
        _print_validate_next_step(plan)
    ctx.exit(1 if has_failures else 0)


def _print_results(results: list[CheckResult]) -> None:
    for result in results:
        if result.status == "pass":
            console.print(f"[green][✓][/green] {result.check_name}: {result.message}")
        elif result.status == "warn":
            console.print(f"[yellow][!][/yellow] {result.check_name}: {result.message}")
            for detail in result.details:
                console.print(f"    {detail}")
        else:
            console.print(f"[red][✗][/red] {result.check_name}: {result.message}")
            for detail in result.details:
                console.print(f"    {detail}")


def _print_validate_next_step(plan: object) -> None:
    if not isinstance(plan, Plan):
        return
    if plan.state == PlanState.PLANNING:
        console.print(
            f"\nNext step: [bold]workflow todo {plan.name}[/bold] or [bold]workflow reject {plan.name}[/bold]",
        )
    elif plan.state == PlanState.CHECKING:
        console.print(
            f"\nNext step: [bold]workflow accept {plan.name}[/bold] or [bold]workflow reject {plan.name}[/bold]",
        )


def _classify_check_steps(resume: ResumePlan, plan: Plan, project_dir: Path, target_repo: AgentName) -> None:
    target_path = project_dir / target_repo / "docs" / "plans" / "checking" / plan.path.name
    if target_path.exists():
        resume.satisfied("transition to checking", "plan file already in checking/")
    else:
        resume.will_run("transition to checking")

    resume.will_run(f"push {target_repo} branch")
    resume.will_run("fetch reviewer")

    branch = f"feat/{plan.name}"
    reviewer_repo = project_dir / RepoName.reviewer
    if git_current_branch(reviewer_repo) == branch:
        resume.satisfied("checkout branch in reviewer", "reviewer already on branch")
    else:
        resume.will_run("checkout branch in reviewer")
    resume.will_run("pull reviewer")

    if git_current_branch(project_dir / RepoName.builder) == "main":
        resume.satisfied("checkout main in builder", "builder already on main")
    else:
        resume.will_run("checkout main in builder")


@register_script("check")
@click.option(
    "--target-repo",
    type=click.Choice(AgentName),
    default=AgentName.builder,
    show_default=True,
    help="Repo whose sandbox and branch should be pushed.",
)
@click.option("--force", is_flag=True, help="Continue even when validation fails")
@click.argument("plan_path")
@click.pass_context
def check(ctx: click.Context, plan_path: str, target_repo: AgentName, *, force: bool) -> None:
    """Hand off a ticket from builder to reviewer: push branch, fetch in reviewer."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, config=config, command_name="check")
        _enforce_expected_state(plan, "check")
        target_repo_path = project_dir / target_repo
        reviewer_repo = project_dir / RepoName.reviewer
        builder_repo = project_dir / RepoName.builder
        require(
            not git_index_lock_present(target_repo_path),
            f"leftover git lock in {target_repo_path}; remove it first.",
        )
        require(not git_index_lock_present(reviewer_repo), f"leftover git lock in {reviewer_repo}; remove it first.")
        if plan.state == PlanState.IMPLEMENTING:
            branch = git_current_branch(target_repo_path)
            require(branch.startswith("feat/"), f"{target_repo} must be on a feat/* branch, currently on {branch!r}.")

        _run_pre_commit_in_sandbox(config, target_repo, dry_run=dry_run)
        results = run_validate(plan, project_dir, config, target_repo_override=target_repo, dry_run=dry_run)
        if not dry_run:
            _print_results(results)
            if not force and any(result.status == "fail" for result in results):
                ctx.exit(1)
                return

        resume = ResumePlan(title="check")
        _classify_check_steps(resume, plan, project_dir, target_repo)
        resume.render()

        new_plan = _transition_if_needed(
            plan,
            PlanState.CHECKING,
            project_dir,
            commit_repo=target_repo,
            dry_run=dry_run,
        )
        git_push(target_repo_path, dry_run=dry_run)
        branch = f"feat/{new_plan.name}"
        git_fetch(reviewer_repo, dry_run=dry_run)
        _ensure_branch_checked_out(reviewer_repo, branch, dry_run=dry_run)
        git_pull(reviewer_repo, dry_run=dry_run)
        # checkout main in builder to make it ready for the next task
        _ensure_branch_checked_out(builder_repo, "main", dry_run=dry_run)
        git_pull(builder_repo, dry_run=dry_run)
        _write_script_audit_log(config, "check", dry_run=dry_run, input_plan=new_plan.name)
        _print_command_reminder(config, PlanState.IMPLEMENTING, "check", new_plan.name)
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
        project_dir = _require_project_dir(config)
        plan = resolve_plan(
            plan_path,
            project_dir,
            config=config,
            command_name="push",
            git_provides_repo=True,
        )
        repo = RepoName(plan.path.parts[0])
        if not dry_run and repo != RepoName.canon:
            branch = git_current_branch(project_dir / repo)
            if branch == "main":
                msg = f"push: {repo} is on 'main'; expected a feature branch"
                raise WorkflowError(msg)
        git_push(project_dir / repo, dry_run=dry_run)
        script_cmd = find_command(config, plan.state, "push")
        if (
            isinstance(script_cmd, _ScriptCommand)
            and script_cmd.pull_target is not None
            and script_cmd.pull_target != repo
        ):
            git_pull(project_dir / script_cmd.pull_target, dry_run=dry_run)
        _write_script_audit_log(config, "push", dry_run=dry_run, input_plan=plan.name)
        _print_command_reminder(config, plan.state, "push", plan.name)
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


def _classify_accept_steps(resume: ResumePlan, plan: Plan, project_dir: Path, branch: str) -> None:
    canon_repo = project_dir / RepoName.canon
    reviewer_repo = project_dir / RepoName.reviewer

    resume.will_run("push reviewer branch")
    resume.will_run("pull canon")
    if git_is_ancestor(canon_repo, f"origin/{branch}", "main"):
        resume.satisfied("merge into canon main", "origin/<branch> already merged into main")
    else:
        resume.will_run("merge into canon main")

    target_path = project_dir / RepoName.canon / "docs" / "plans" / "done" / plan.path.name
    if target_path.exists():
        resume.satisfied("transition to done", "plan file already in done/")
    else:
        resume.will_run("transition to done")

    resume.will_run("push canon")
    if git_current_branch(reviewer_repo) == "main":
        resume.satisfied("checkout main in reviewer", "reviewer already on main")
    else:
        resume.will_run("checkout main in reviewer")


@register_script("accept")
@click.argument("plan_path")
@click.pass_context
def accept(ctx: click.Context, plan_path: str) -> None:
    """Accept a reviewed ticket: push from reviewer, merge to main, transition to done."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, config=config, command_name="accept")
        _enforce_expected_state(plan, "accept")
        reviewer_repo = project_dir / RepoName.reviewer
        canon_repo = project_dir / RepoName.canon
        require(not git_index_lock_present(reviewer_repo), f"leftover git lock in {reviewer_repo}; remove it first.")
        require(not git_index_lock_present(canon_repo), f"leftover git lock in {canon_repo}; remove it first.")
        require(git_current_branch(canon_repo) == "main", "accept: canon must be on 'main'.")
        # The feature branch follows the fixed 'feat/{plan.name}' convention (set by
        # `implement`), not whatever reviewer happens to be checked out to right now --
        # a prior run may have already advanced reviewer to 'main' (decision 2).
        branch = f"feat/{plan.name}"
        if plan.state == PlanState.CHECKING:
            current_branch = git_current_branch(reviewer_repo)
            if current_branch == "main":
                msg = "accept: reviewer is on 'main'; expected a feature branch"
                raise WorkflowError(msg)

        resume = ResumePlan(title="accept")
        _classify_accept_steps(resume, plan, project_dir, branch)
        resume.render()

        git_push(reviewer_repo, dry_run=dry_run)
        git_pull(canon_repo, dry_run=dry_run)
        # Merge via remote-tracking ref: the branch is not checked out locally in canon.
        git_merge(canon_repo, f"origin/{branch}", dry_run=dry_run)
        new_plan = _transition_if_needed(
            plan,
            PlanState.DONE,
            project_dir,
            repo_override=RepoName.canon,
            dry_run=dry_run,
        )
        git_push(canon_repo, dry_run=dry_run)
        # checkout main in reviewer to make it ready for the next task
        _ensure_branch_checked_out(reviewer_repo, "main", dry_run=dry_run)
        git_pull(reviewer_repo, dry_run=dry_run)
        _write_script_audit_log(config, "accept", dry_run=dry_run, input_plan=plan.name)
        _print_command_reminder(config, PlanState.CHECKING, "accept", new_plan.name)
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


@register_script("reject")
@click.option(
    "--target-repo",
    type=click.Choice(RepoName),
    default=RepoName.canon,
    show_default=True,
    help="Repo containing the plan to reject.",
)
@click.argument("plan_path")
@click.pass_context
def reject(ctx: click.Context, plan_path: str, target_repo: RepoName) -> None:
    """Transition a ticket to the rejected state."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, config=config)
        _enforce_expected_state(plan, "reject")
        if plan.state == PlanState.REJECTED:
            ctx.exit(0)
            return
        new_plan = transition_plan(plan, PlanState.REJECTED, project_dir, repo_override=target_repo, dry_run=dry_run)
        git_commit(
            project_dir / target_repo,
            _transition_commit_message(new_plan, PlanState.REJECTED),
            dry_run=dry_run,
            no_verify=True,
        )
        git_push(project_dir / target_repo, dry_run=dry_run)
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


@register_script("go")
@click.argument("target")
@click.pass_context
def go(ctx: click.Context, target: str) -> None:
    """Rotate the control pane to a window and print the target repo's git status."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
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
            repo = RepoName(target)
        rotate_control_pane(config, window, dry_run=dry_run)
        if repo is not None:
            git_status(project_dir / repo, dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)
