"""Workflow script commands: project setup, state transitions, and git operations."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from workflow.audit import read_audit_log, write_audit_log
from workflow.config import WORKFLOW_ROOT_MARKER, ScriptCommand, WorkflowConfig
from workflow.doublecheck import CHECK_NAMES, CheckResult, infer_target_repo
from workflow.doublecheck import doublecheck as run_doublecheck
from workflow.exceptions import AmbiguousPlanError, PlanCollisionError, PlanNotFoundError, WorkflowError
from workflow.git_helpers import (
    git_commit,
    git_current_branch,
    git_fetch,
    git_merge,
    git_pull,
    git_push,
    transfer_branch_to_main,
)
from workflow.models import AuditLogEntry, PlanState
from workflow.names import AgentName, RepoName, command_cli_name
from workflow.plan_resolver import find_all_plans, parse_plan_filename, resolve_plan
from workflow.registry import register_script
from workflow.shell import console, error_console, run_command
from workflow.state import transition_plan

if TYPE_CHECKING:
    from collections.abc import Sequence

    from workflow.config import Command
    from workflow.models import Plan


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
    )


def _find_command(config: WorkflowConfig, state: PlanState, command_name: str) -> Command | None:
    state_config = config.states.get(state)
    if state_config is None:
        return None
    for command in state_config.commands:
        if command.name == command_name:
            return command
    return None


def _format_reminder(config: WorkflowConfig, reminder: str, plan_name: str) -> str:
    for state_config in config.states.values():
        for command in state_config.commands:
            if command.name != reminder:
                continue
            if command.command_type == "manual":
                return reminder
            return f"workflow {command_cli_name(reminder)} {plan_name}"
    return reminder


def _find_reminder(config: WorkflowConfig, state: PlanState, command_name: str) -> str | None:
    command = _find_command(config, state, command_name)
    if command is None:
        return None
    reminder = getattr(command, "reminder", None)
    if reminder:
        return reminder
    transition = getattr(command, "transition", None)
    if transition is None:
        return None
    target_state = config.states.get(transition)
    if target_state is None or not target_state.commands:
        return None
    return target_state.commands[0].name


def _print_command_reminder(config: WorkflowConfig, state: PlanState, command_name: str, plan_name: str) -> None:
    reminder = _find_reminder(config, state, command_name)
    if reminder is None:
        return
    console.print(f"Next step: [bold]{_format_reminder(config, reminder, plan_name)}[/bold]")


def _top_level_number(plan_name: str) -> int | None:
    candidate = Path(plan_name).stem.split("/")[-1]
    if "_" not in candidate:
        return None
    try:
        sequence_number, _slug = parse_plan_filename(candidate)
    except ValueError:
        return None
    return sequence_number[0]


def _next_intent_number(project_dir: Path) -> int:
    reserved = {plan.sequence_number[0] for plan in find_all_plans(project_dir)}
    for entry in read_audit_log(project_dir):
        for plan_name in [entry.input_plan, *entry.output_plans]:
            if plan_name is None:
                continue
            top_level = _top_level_number(plan_name)
            if top_level is not None:
                reserved.add(top_level)
    number = 1
    while number in reserved:
        number += 1
    return number


def _open_in_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)], check=True)  # noqa: S603


def _run_pre_commit_in_sandbox(config: WorkflowConfig, repo_name: str, *, dry_run: bool) -> None:
    if repo_name == RepoName.master:
        msg = "Target repo 'master' has no sandbox container."
        raise WorkflowError(msg)
    project_name = config.project_name or _require_project_dir(config).name
    container_name = f"auggie-{project_name}-{repo_name}"
    repo_path = f"/home/auggie/projects/{project_name}"
    run_command(
        ["docker", "exec", container_name, "bash", "-lc", f"cd {repo_path} && ./.git-pre-commit-hook"],
        dry_run=dry_run,
    )


def _transition_commit_message(plan: Plan, target_state: PlanState) -> str:
    return f"Transition {plan.name} to {target_state.value}"


@register_script("init")
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialise the project directory: create bare and agent git clones."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]
    project_dir = (config.project_dir or Path.cwd()).resolve()

    try:
        run_command(["git", "clone", "--bare", RepoName.master, "bare"], cwd=project_dir, dry_run=dry_run)
        for agent in config.agents:
            run_command(["git", "clone", "bare", agent.name], cwd=project_dir, dry_run=dry_run)
        if not dry_run:
            (project_dir / WORKFLOW_ROOT_MARKER).touch()
        _write_script_audit_log(config.model_copy(update={"project_dir": project_dir}), "init", dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("start")
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start tmux windows for all configured agents."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        _require_project_dir(config)
        if not config.agents:
            msg = "No agents configured."
            raise WorkflowError(msg)
        project_name = config.project_name or _require_project_dir(config).name
        first_agent = config.agents[0]
        first_command = f"echo {project_name} {first_agent.name} {first_agent.model}"
        run_command(
            ["tmux", "new-session", "-d", "-s", config.tmux.session_name, "-n", first_agent.name, first_command],
            dry_run=dry_run,
        )
        for agent in config.agents[1:]:
            run_command(
                [
                    "tmux",
                    "new-window",
                    "-t",
                    config.tmux.session_name,
                    "-n",
                    agent.name,
                    f"echo {project_name} {agent.name} {agent.model}",  # TODO: placeholder
                ],
                dry_run=dry_run,
            )
        run_command(
            [
                "tmux",
                "new-window",
                "-t",
                config.tmux.session_name,
                "-n",
                "builder-shell",
                f"echo {project_name} builder-shell bash",  # TODO: placeholder
            ],
            dry_run=dry_run,
        )
        _write_script_audit_log(config, "start", dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("new")
@click.argument("slug")
@click.pass_context
def new(ctx: click.Context, slug: str) -> None:
    """Create a new intent file and open it in $EDITOR."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        number = _next_intent_number(project_dir)
        intent_path = project_dir / RepoName.master / "docs" / "plans" / "intent" / f"{number:03d}_{slug}.md"
        content = f"# {number:03d}: {slug.replace('_', ' ').title()}\n\n## Goal\n\nTODO\n"
        if dry_run:
            console.print(f"Would create: {intent_path}")
            console.print(f"Would open in $EDITOR: {intent_path}")
        else:
            intent_path.parent.mkdir(parents=True, exist_ok=True)
            intent_path.write_text(content, encoding="utf-8")
            _open_in_editor(intent_path)
        _write_script_audit_log(config, "new", dry_run=dry_run, output_plans=[intent_path.stem])
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("todo")
@click.argument("plan_path")
@click.pass_context
def todo(ctx: click.Context, plan_path: str) -> None:
    """Approve a plan: push from planner, pull to master, transition to todo."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, repo=RepoName.planner, expected_states={PlanState.PLANNING})
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
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("implement")
@click.argument("plan_path")
@click.pass_context
def implement(ctx: click.Context, plan_path: str) -> None:
    """Schedule a plan for implementation: transition to implementing and push."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(
            plan_path,
            project_dir,
            repo=RepoName.master,
            expected_states={PlanState.PLANNING, PlanState.TODO},
        )
        if plan.state == PlanState.PLANNING:
            plan = transition_plan(plan, PlanState.TODO, project_dir, dry_run=dry_run)
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
        _write_script_audit_log(config, "implement", dry_run=dry_run, input_plan=plan.name)
        _print_command_reminder(config, PlanState.TODO, "implement", new_plan.name)
        if not dry_run:
            time.sleep(0.1)
        # TODO: switch to the builder tmux window.
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("doublecheck")
@click.pass_context
@click.option(
    "--target-repo",
    type=click.Choice(RepoName),
    default=None,
    help="Override the target repo (default: inferred from plan state).",
)
@click.argument("plan_path")
def doublecheck(
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
        plan = _resolve_doublecheck_plan(config, plan_path)
    except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError) as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return

    target = infer_target_repo(plan, config, target_repo)
    console.print(f"Target repo: [bold]{target}[/bold]")

    if dry_run:
        for name in CHECK_NAMES:
            console.print(f"[dim][-] {name}: skipped in dry-run[/dim]")
        return

    try:
        results = run_doublecheck(plan, config.project_dir, config, target_repo_override=target)
    except WorkflowError as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return

    _print_results(results)
    has_failures = any(result.status == "fail" for result in results)
    if not has_failures:
        _print_next_step(plan)
    ctx.exit(1 if has_failures else 0)


def _resolve_doublecheck_plan(config: WorkflowConfig, plan_path: str) -> Plan:
    """Resolve *plan_path* across the repos/states where `doublecheck` is configured."""
    project_dir = _require_project_dir(config)
    targets_by_repo: dict[str, set[PlanState]] = {}

    for state, state_config in config.states.items():
        if not _state_has_doublecheck(state_config.commands):
            continue
        repo_name = state_config.agent or RepoName.master
        targets_by_repo.setdefault(repo_name, set()).add(state)

    if not targets_by_repo:
        msg = "No 'doublecheck' command found."
        raise WorkflowError(msg)

    resolved: list[Plan] = []
    repos_searched: list[str] = []
    for repo_name, expected_states in targets_by_repo.items():
        repos_searched.append(repo_name)
        try:
            plan = resolve_plan(plan_path, project_dir, expected_states=expected_states, repo=repo_name)
        except PlanNotFoundError:
            continue
        resolved.append(plan)

    if not resolved:
        raise PlanNotFoundError(plan_path, repos_searched)
    if len(resolved) > 1:
        raise AmbiguousPlanError(plan_path, resolved)
    return resolved[0]


def _state_has_doublecheck(commands: Sequence[object]) -> bool:
    return any(isinstance(command, ScriptCommand) and command.script == "doublecheck" for command in commands)


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


def _print_next_step(plan: object) -> None:
    from workflow.models import Plan  # noqa: PLC0415

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


@register_script("push")
@click.option(
    "--target-repo",
    type=click.Choice(AgentName),
    default=AgentName.builder,
    show_default=True,
    help="Repo whose sandbox and branch should be pushed.",
)
@click.argument("plan_path")
@click.pass_context
def push(ctx: click.Context, plan_path: str, target_repo: AgentName) -> None:
    """Push a branch after verification and fetch it in reviewer."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, repo=target_repo, expected_states={PlanState.CHECKING})
        _run_pre_commit_in_sandbox(config, target_repo, dry_run=dry_run)
        results = run_doublecheck(plan, project_dir, config, target_repo_override=target_repo, dry_run=dry_run)
        if not dry_run:
            _print_results(results)
            if any(result.status == "fail" for result in results):
                ctx.exit(1)
                return
        git_push(project_dir / target_repo, dry_run=dry_run)
        git_fetch(project_dir / RepoName.reviewer, dry_run=dry_run)
        _write_script_audit_log(config, "push", dry_run=dry_run, input_plan=plan.name)
        _print_command_reminder(config, PlanState.IMPLEMENTING, "push", plan.name)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("accept")
@click.argument("plan_path")
@click.pass_context
def accept(ctx: click.Context, plan_path: str) -> None:
    """Accept a reviewed branch: push from reviewer, merge to main, transition to done."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(plan_path, project_dir, repo=RepoName.reviewer, expected_states={PlanState.CHECKING})
        branch = "<dry-run-branch>" if dry_run else git_current_branch(project_dir / RepoName.reviewer)
        git_push(project_dir / RepoName.reviewer, dry_run=dry_run)
        git_pull(project_dir / RepoName.master, dry_run=dry_run)
        new_plan = transition_plan(plan, PlanState.DONE, project_dir, repo_override=RepoName.master, dry_run=dry_run)
        git_merge(project_dir / RepoName.master, branch, dry_run=dry_run)
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
        error_console.print(f"[red]{exc}[/red]")
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
    """Transition a plan to the rejected state."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    try:
        project_dir = _require_project_dir(config)
        plan = resolve_plan(
            plan_path,
            project_dir,
            repo=target_repo,
            expected_states=set(PlanState) - {PlanState.REVIEW},
        )
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
        _write_script_audit_log(config, "reject", dry_run=dry_run, input_plan=plan.name)
    except (
        subprocess.CalledProcessError,
        PlanNotFoundError,
        AmbiguousPlanError,
        PlanCollisionError,
        WorkflowError,
    ) as exc:
        error_console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return
    ctx.exit(0)
