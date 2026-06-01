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
from workflow.commands.claude_md import generate_and_write_claude_md
from workflow.config import WORKFLOW_ROOT_MARKER, WorkflowConfig
from workflow.config import ScriptCommand as _ScriptCommand
from workflow.exceptions import (
    AmbiguousPlanError,
    ClaudeMdGenerationError,
    PlanCollisionError,
    PlanNotFoundError,
    WorkflowError,
)
from workflow.git_helpers import (
    git_add,
    git_commit,
    git_commit_if_changed,
    git_current_branch,
    git_fetch,
    git_merge,
    git_pull,
    git_push,
    transfer_branch_to_main,
)
from workflow.models import AuditLogEntry, Plan, PlanState
from workflow.names import AgentName, RepoName
from workflow.next_step import find_command, find_next_step, find_reminder_name, format_next_step_invocation
from workflow.plan_resolver import next_intent_number, resolve_plan
from workflow.registry import register_script
from workflow.shell import console, error_console, print_workflow_error, run_command
from workflow.state import transition_plan
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
    container_name = f"auggie-{project_name}-{repo_name}"
    repo_path = shlex.quote(f"/home/auggie/projects/{project_name}")
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
        if not (project_dir / RepoName.master / ".git").is_dir():
            msg = f"{project_dir / RepoName.master} is not a git repository (no .git/ found)."
            raise WorkflowError(msg)
        # Generate CLAUDE.md into master before the bare clone so all agent clones inherit it.
        if not dry_run:
            try:
                generate_and_write_claude_md(config.model_copy(update={"project_dir": project_dir}))
                master_repo_path = project_dir / RepoName.master
                git_add(master_repo_path, Path("CLAUDE.md"), dry_run=False)
                # no_verify: at init time the pre-commit hook would run pytest before the
                # workflow package is even installed in master; bypassing is required here.
                git_commit_if_changed(
                    master_repo_path,
                    "chore: generate initial CLAUDE.md",
                    dry_run=False,
                    no_verify=True,
                )
            except ClaudeMdGenerationError as exc:
                raise WorkflowError(str(exc)) from exc
        run_command(["git", "clone", "--bare", RepoName.master, "bare"], cwd=project_dir, dry_run=dry_run)
        for agent in config.agents:
            run_command(["git", "clone", "bare", agent.name], cwd=project_dir, dry_run=dry_run)
        # Configure push behaviour in each agent clone so feature branches push
        # to a same-named remote branch without requiring a pre-existing upstream.
        for agent in config.agents:
            agent_repo = project_dir / agent.name
            run_command(["git", "config", "push.autoSetupRemote", "true"], cwd=agent_repo, dry_run=dry_run)
            run_command(["git", "config", "push.default", "current"], cwd=agent_repo, dry_run=dry_run)
        # Wire master to bare so git pull/push work in place.
        bare_path = project_dir / "bare"
        master_repo = project_dir / RepoName.master
        run_command(["git", "remote", "add", "origin", str(bare_path)], cwd=master_repo, dry_run=dry_run)
        run_command(["git", "config", "push.autoSetupRemote", "true"], cwd=master_repo, dry_run=dry_run)
        run_command(["git", "config", "push.default", "current"], cwd=master_repo, dry_run=dry_run)
        run_command(["git", "push", "-u", "origin", "HEAD"], cwd=master_repo, dry_run=dry_run)
        if not dry_run:
            (project_dir / WORKFLOW_ROOT_MARKER).touch()
        _write_script_audit_log(config.model_copy(update={"project_dir": project_dir}), "init", dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
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
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc)
        ctx.exit(1)
        return
    ctx.exit(0)


@register_script("todo")
@click.argument("plan_path")
@click.pass_context
def todo(ctx: click.Context, plan_path: str) -> None:
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
            if any(result.status == "fail" for result in results):
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
        _write_script_audit_log(config, "implement", dry_run=dry_run, input_plan=plan.name)
        _print_command_reminder(config, PlanState.TODO, "implement", new_plan.name)
        if not dry_run:
            time.sleep(1.0)
        # TODO: switch to the builder tmux window.
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
        _print_next_step(plan)
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


def _print_next_step(plan: object) -> None:
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
@click.argument("plan_path")
@click.pass_context
def check(ctx: click.Context, plan_path: str, target_repo: AgentName) -> None:
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
            if any(result.status == "fail" for result in results):
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
        git_fetch(project_dir / RepoName.reviewer, dry_run=dry_run)
        _write_script_audit_log(config, "check", dry_run=dry_run, input_plan=new_plan.name)
        _print_command_reminder(config, PlanState.IMPLEMENTING, "check", new_plan.name)
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
