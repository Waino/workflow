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
from workflow.commands.agents_md import generate_and_write_agents_md
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
    git_current_branch,
    git_fetch,
    git_merge,
    git_pull,
    git_push,
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
from workflow.tmux_dispatch import (
    assert_in_workflow_session,
    attach_workflow_session,
    create_workflow_session,
    rotate_control_pane,
)
from workflow.validate import CHECK_NAMES, CheckResult, infer_target_repo
from workflow.validate import validate as run_validate

# Hard state constraints for script commands
_EXPECTED_STATES_FOR: dict[str, set[PlanState]] = {
    "todo": {PlanState.PLANNING},
    "implement": {PlanState.PLANNING, PlanState.TODO},
    "check": {PlanState.IMPLEMENTING},
    "accept": {PlanState.CHECKING},
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
    if repo_name == RepoName.master:
        msg = "Target repo 'master' has no sandbox container."
        raise WorkflowError(msg)
    project_name = config.project_name or _require_project_dir(config).name
    container_name = f"pi-{project_name}-{repo_name}"
    repo_path = shlex.quote(f"/home/auggie/projects/{project_name}/{repo_name}")
    run_command(
        ["docker", "exec", container_name, "bash", "-lc", f"cd {repo_path} && ./.git-pre-commit-hook"],
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


@register_script("init")
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialise the project directory: create bare and agent git clones."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]
    project_dir = (config.project_dir or Path.cwd()).resolve()

    try:
        if not (project_dir / RepoName.master / ".git").is_dir():
            msg = f"{project_dir / RepoName.master} is not a git repository (no .git/ found)."
            raise WorkflowError(msg)
        if not (project_dir / RepoName.master / ".git-pre-commit-hook").is_file():
            msg = f"{project_dir / RepoName.master / '.git-pre-commit-hook'} must exist."
            raise WorkflowError(msg)
        # Generate AGENTS.md into master before the bare clone so all agent clones inherit it.
        if not dry_run:
            try:
                generate_and_write_agents_md(config.model_copy(update={"project_dir": project_dir}))
                master_repo_path = project_dir / RepoName.master
                git_add(master_repo_path, Path("AGENTS.md"), dry_run=False)
                # no_verify: at init time the pre-commit hook would run pytest before the
                # workflow package is even installed in master; bypassing is required here.
                git_commit_if_changed(
                    master_repo_path,
                    "chore: generate initial AGENTS.md",
                    dry_run=False,
                    no_verify=True,
                )
            except AgentsMdGenerationError as exc:
                raise WorkflowError(str(exc)) from exc
        run_command(["git", "clone", "--bare", RepoName.master, "bare"], cwd=project_dir, dry_run=dry_run)
        for agent in config.agents:
            run_command(["git", "clone", "bare", agent.name], cwd=project_dir, dry_run=dry_run)
        for agent in config.agents:
            # Configure push behaviour in each agent clone so feature branches push
            # to a same-named remote branch without requiring a pre-existing upstream.
            agent_repo = project_dir / agent.name
            run_command(["git", "config", "push.autoSetupRemote", "true"], cwd=agent_repo, dry_run=dry_run)
            run_command(["git", "config", "push.default", "current"], cwd=agent_repo, dry_run=dry_run)
            # Install pre-commit hook
            run_command(
                ["ln", "-s", "../../.git-pre-commit-hook", ".git/hooks/pre-commit"],
                cwd=agent_repo,
                dry_run=dry_run,
            )
        # Wire master to bare so git pull/push work in place.
        bare_path = project_dir / "bare"
        master_repo = project_dir / RepoName.master
        run_command(["git", "remote", "add", "origin", str(bare_path)], cwd=master_repo, dry_run=dry_run)
        run_command(["git", "config", "push.autoSetupRemote", "true"], cwd=master_repo, dry_run=dry_run)
        run_command(["git", "config", "push.default", "current"], cwd=master_repo, dry_run=dry_run)
        run_command(["git", "push", "-u", "origin", "HEAD"], cwd=master_repo, dry_run=dry_run)
        if not dry_run:
            (project_dir / WORKFLOW_ROOT_MARKER).touch()

        # Create venvs
        script = _find_docker_script(project_dir)
        project_name = config.project_name or project_dir.name
        for agent in config.agents:
            run_command(
                _create_venv_command(script, project_name, project_dir, agent),
                cwd=project_dir,
                dry_run=dry_run,
            )
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


@register_script("start")
@click.option("--reset", is_flag=True, default=False, help="Remove existing containers instead of restarting.")
@click.pass_context
def start(ctx: click.Context, *, reset: bool) -> None:
    """Start tmux windows for all configured agents."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        if not config.agents:
            msg = "No agents configured."
            raise WorkflowError(msg)
        project_name = config.project_name or project_dir.name
        script = _find_docker_script(project_dir)
        first_agent = config.agents[0]
        windows = [
            (agent.name, _agent_docker_command(script, project_name, project_dir, agent, reset=reset))
            for agent in config.agents
        ]
        windows.append(("builder-shell", _builder_shell_command(script, project_name, project_dir)))
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
        intent_path = project_dir / RepoName.master / "docs" / "plans" / "intent" / f"{number:03d}_{slug}.md"
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
            project_dir / RepoName.master,
            intent_path.relative_to(project_dir / RepoName.master),
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
    """Approve a ticket: push from planner, pull to master, transition to todo."""
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

        transfer_branch_to_main(project_dir / RepoName.planner, project_dir / RepoName.master, dry_run=dry_run)
        new_plan = transition_plan(plan, PlanState.TODO, project_dir, repo_override=RepoName.master, dry_run=dry_run)
        git_commit(
            project_dir / RepoName.master,
            _transition_commit_message(plan, PlanState.TODO),
            dry_run=dry_run,
            no_verify=True,
        )
        git_push(project_dir / RepoName.master, dry_run=dry_run)
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


@register_script("implement")
@click.argument("plan_path")
@click.pass_context
def implement(ctx: click.Context, plan_path: str) -> None:
    """Schedule a ticket for implementation: transition to implementing and push."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, config=config, command_name="implement")
        _enforce_expected_state(plan, "implement")
        # Preconditions (checked before any state change, so a failure leaves no state change).
        builder_repo = project_dir / RepoName.builder
        feature_branch = f"feat/{plan.name}"
        if git_status_porcelain(builder_repo, dry_run=dry_run):
            msg = "builder working tree is dirty; commit or discard changes before implement"
            raise WorkflowError(msg)
        if git_branch_exists(builder_repo, feature_branch, dry_run=dry_run):
            msg = f"branch {feature_branch} already exists in builder"
            raise WorkflowError(msg)
        if plan.state == PlanState.PLANNING:
            transfer_branch_to_main(
                project_dir / RepoName.planner,
                project_dir / RepoName.master,
                dry_run=dry_run,
            )
            plan = transition_plan(plan, PlanState.TODO, project_dir, repo_override=RepoName.master, dry_run=dry_run)
            git_commit(
                project_dir / RepoName.master,
                _transition_commit_message(plan, PlanState.TODO),
                dry_run=dry_run,
                no_verify=True,
            )
        new_plan = transition_plan(plan, PlanState.IMPLEMENTING, project_dir, dry_run=dry_run)
        git_commit(
            project_dir / RepoName.master,
            _transition_commit_message(new_plan, PlanState.IMPLEMENTING),
            dry_run=dry_run,
            no_verify=True,
        )
        git_push(project_dir / RepoName.master, dry_run=dry_run)
        # Leave the builder ready on its feature branch, freshly synced from main (D5).
        git_fetch(builder_repo, dry_run=dry_run)
        git_checkout(builder_repo, "main", dry_run=dry_run)
        git_pull(builder_repo, dry_run=dry_run)
        git_checkout(builder_repo, feature_branch, create=True, dry_run=dry_run)
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
        _run_pre_commit_in_sandbox(config, target_repo, dry_run=dry_run)
        results = run_validate(plan, project_dir, config, target_repo_override=target_repo, dry_run=dry_run)
        if not dry_run:
            _print_results(results)
            if not force and any(result.status == "fail" for result in results):
                ctx.exit(1)
                return
        new_plan = transition_plan(plan, PlanState.CHECKING, project_dir, dry_run=dry_run)
        git_commit(
            project_dir / target_repo,
            _transition_commit_message(new_plan, PlanState.CHECKING),
            dry_run=dry_run,
            no_verify=True,
        )
        git_push(project_dir / target_repo, dry_run=dry_run)
        branch = git_current_branch(project_dir / target_repo)
        git_fetch(project_dir / RepoName.reviewer, dry_run=dry_run)
        git_checkout(project_dir / RepoName.reviewer, branch, create=False, dry_run=dry_run)
        git_pull(project_dir / RepoName.reviewer, dry_run=dry_run)
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
        if not dry_run and repo != RepoName.master:
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
        branch = "<dry-run-branch>" if dry_run else git_current_branch(project_dir / RepoName.reviewer)
        if not dry_run and branch == "main":
            msg = "accept: reviewer is on 'main'; expected a feature branch"
            raise WorkflowError(msg)
        git_push(project_dir / RepoName.reviewer, dry_run=dry_run)
        git_pull(project_dir / RepoName.master, dry_run=dry_run)
        # Merge via remote-tracking ref: the branch is not checked out locally in master.
        git_merge(project_dir / RepoName.master, f"origin/{branch}", dry_run=dry_run)
        new_plan = transition_plan(plan, PlanState.DONE, project_dir, repo_override=RepoName.master, dry_run=dry_run)
        git_commit(
            project_dir / RepoName.master,
            _transition_commit_message(new_plan, PlanState.DONE),
            dry_run=dry_run,
            no_verify=True,
        )
        git_push(project_dir / RepoName.master, dry_run=dry_run)
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
    default=RepoName.master,
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
