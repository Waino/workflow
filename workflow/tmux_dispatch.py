"""Prompt command dispatch and tmux integration.

Implements dynamic Click command registration from the YAML config, Jinja2
prompt template rendering, optional $EDITOR editing, and delivery to agent
tmux windows via libtmux.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import click
import libtmux
from jinja2 import Environment, StrictUndefined, UndefinedError
from pydantic import BaseModel, ConfigDict

from workflow.audit import write_audit_log
from workflow.config import PromptCommand, WorkflowConfig
from workflow.exceptions import (
    AmbiguousPlanError,
    PlanCollisionError,
    PlanNotFoundError,
    WorkflowError,
)
from workflow.git_helpers import git_commit_hash, git_current_branch, git_pull
from workflow.models import AuditLogEntry, Plan, PlanState
from workflow.names import RepoName, command_cli_name
from workflow.plan_resolver import resolve_plan
from workflow.shell import console, error_console, print_workflow_error

# Reverse map: which workflow repo serves each prompt-eligible plan state.
_REPO_FOR_PROMPT_STATE: dict[PlanState, RepoName] = {
    PlanState.INTENT: RepoName.planner,
    PlanState.PLANNING: RepoName.planner,
    PlanState.TODO: RepoName.master,
    PlanState.IMPLEMENTING: RepoName.builder,
    PlanState.CHECKING: RepoName.reviewer,
    PlanState.REVIEW: RepoName.reviewer,
}


def _repo_for_prompt_state(state: PlanState) -> RepoName:
    """Return the workflow repo that owns prompt dispatch for *state*."""
    try:
        return _REPO_FOR_PROMPT_STATE[state]
    except KeyError as exc:
        msg = f"Prompt dispatch is not supported for state '{state.value}'."
        raise WorkflowError(msg) from exc


class _PromptTarget(BaseModel):
    """One concrete prompt command target in one workflow state."""

    model_config = ConfigDict(frozen=True)

    command: PromptCommand
    state: PlanState
    agent_name: str | None
    repo_name: RepoName
    has_plan: bool


class _PromptContext(BaseModel):
    """Resolved context shared between the setup helpers and _dispatch_prompt."""

    model_config = ConfigDict(frozen=True)

    agent_name: str
    repo_name: RepoName
    prompt: str
    audit_input_plan: str | None = None


def _collect_prompt_targets(config: WorkflowConfig) -> dict[str, list[_PromptTarget]]:
    """Collect prompt commands from the config, grouped by human-readable name."""
    targets: dict[str, list[_PromptTarget]] = {}
    for state, state_config in config.states.items():
        for cmd in state_config.commands:
            if not isinstance(cmd, PromptCommand):
                continue
            targets.setdefault(cmd.name, []).append(
                _PromptTarget(
                    command=cmd,
                    state=state,
                    agent_name=state_config.agent,
                    repo_name=_repo_for_prompt_state(state),
                    has_plan=state != PlanState.REVIEW,
                ),
            )
    return targets


def _render_prompt(template_str: str, **variables: object) -> str:
    """Render a Jinja2 prompt template; raise :class:`WorkflowError` on missing variables."""
    env = Environment(undefined=StrictUndefined, autoescape=False)  # noqa: S701 — text prompts, not HTML
    try:
        return env.from_string(template_str).render(**variables)
    except UndefinedError as exc:
        msg = f"Prompt template variable not found: {exc}"
        raise WorkflowError(msg) from exc


def _open_in_editor(content: str) -> str | None:
    """Write *content* to a temp file, open in ``$EDITOR``, return edited text.

    Returns ``None`` if the editor exits non-zero or the result is blank.
    Falls back to ``vi`` when ``$EDITOR`` is not set.
    """
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", encoding="utf-8", delete=False) as tf:
        tf.write(content)
        tmp_path = Path(tf.name)
    try:
        result = subprocess.run([editor, str(tmp_path)], check=False)  # noqa: S603
        if result.returncode != 0:
            return None
        edited = tmp_path.read_text(encoding="utf-8")
        return edited if edited.strip() else None
    finally:
        tmp_path.unlink(missing_ok=True)


def _resolve_plan_target(
    config: WorkflowConfig,
    cmd_name: str,
    plan_path_str: str,
) -> tuple[_PromptTarget, Plan]:
    """Resolve the plan and pick the prompt target for *cmd_name* in the plan's repo."""
    assert config.project_dir is not None
    targets = [target for target in _collect_prompt_targets(config).get(cmd_name, []) if target.has_plan]
    if not targets:
        msg = f"No '{cmd_name}' command found."
        raise WorkflowError(msg)

    plan = resolve_plan(plan_path_str, config.project_dir, config=config, command_name=cmd_name)
    plan_repo = plan.path.parts[0]
    for target in targets:
        if target.repo_name == plan_repo:
            return target, plan
    # Fall back to the first defined target — the resolver already restricted to valid repos.
    return targets[0], plan


def _git_metadata(target: _PromptTarget, config: WorkflowConfig, *, dry_run: bool) -> tuple[str, str]:
    """Return branch and commit metadata for the prompt target repo."""
    if dry_run:
        return "<dry-run-branch>", "<dry-run-commit>"

    assert config.project_dir is not None
    repo_path = config.project_dir / target.repo_name
    try:
        return git_current_branch(repo_path), git_commit_hash(repo_path)
    except subprocess.CalledProcessError as exc:
        msg = f"Git metadata lookup failed in {repo_path}: {exc}"
        raise WorkflowError(msg) from exc


def _setup_has_plan(
    config: WorkflowConfig,
    cmd_name: str,
    plan_path_str: str,
    *,
    dry_run: bool,
) -> _PromptContext | None:
    """Resolve plan and render prompt for commands that take a plan argument.

    Returns a :class:`_PromptContext` on success,
    or ``None`` after printing an error message.
    """
    try:
        target, plan = _resolve_plan_target(config, cmd_name, plan_path_str)
    except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError, WorkflowError) as exc:
        print_workflow_error(exc)
        return None
    if target.agent_name is None:
        error_console.print(f"Plan resolved to: {plan.path}")
        error_console.print(f"[red]No agent configured for state '{plan.state.value}'.[/red]")
        return None
    try:
        branch, commit = _git_metadata(target, config, dry_run=dry_run)
        rendered = _render_prompt(target.command.prompt, plan=plan, branch=branch, commit=commit)
    except WorkflowError as exc:
        error_console.print(f"Plan resolved to: {plan.path}")
        print_workflow_error(exc)
        return None
    return _PromptContext(
        agent_name=target.agent_name,
        repo_name=target.repo_name,
        prompt=rendered,
        audit_input_plan=plan.name,
    )


def _setup_stateless(
    config: WorkflowConfig,
    cmd_name: str,
) -> _PromptContext | None:
    """Resolve agent and render prompt for state-less commands (no plan argument).

    Returns a :class:`_PromptContext` on success,
    or ``None`` after printing an error message.
    """
    targets = [target for target in _collect_prompt_targets(config).get(cmd_name, []) if not target.has_plan]
    if not targets:
        error_console.print(f"[red]No '{cmd_name}' command found.[/red]")
        return None
    target = targets[0]
    if target.agent_name is None:
        error_console.print("[red]No agent configured for review state.[/red]")
        return None
    try:
        rendered = _render_prompt(target.command.prompt)
    except WorkflowError as exc:
        print_workflow_error(exc)
        return None
    return _PromptContext(agent_name=target.agent_name, repo_name=target.repo_name, prompt=rendered)


def _dispatch_prompt(
    ctx: click.Context,
    cmd_name: str,
    plan_path_str: str | None,
    *,
    has_plan: bool,
    edit_mode: bool,
) -> int:
    """Resolve plan, render prompt, optionally edit, send to agent, write audit log."""
    config: WorkflowConfig = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    if config.project_dir is None:
        error_console.print("[red]Error: not inside a workflow project directory.[/red]")
        return 1

    if has_plan:
        assert plan_path_str is not None  # Click enforces required=True when has_plan
        result = _setup_has_plan(config, cmd_name, plan_path_str, dry_run=dry_run)
    else:
        result = _setup_stateless(config, cmd_name)

    if result is None:
        return 1

    agent_name = result.agent_name
    repo_name = result.repo_name
    prompt = result.prompt
    audit_input_plan = result.audit_input_plan

    try:
        git_pull(config.project_dir / repo_name, dry_run=dry_run)
    except subprocess.CalledProcessError as exc:
        error_console.print(f"[red]git pull failed in {config.project_dir / repo_name}: {exc}[/red]")
        return 1

    if edit_mode:
        edited = _open_in_editor(prompt)
        if edited is None:
            console.print("Cancelled (empty prompt).")
            return 0
        prompt = edited

    try:
        send_prompt_to_agent(agent_name, prompt, config, dry_run=dry_run)
    except WorkflowError as exc:
        print_workflow_error(exc)
        return 1

    write_audit_log(
        config.project_dir,
        AuditLogEntry(
            timestamp=datetime.now(UTC),
            actor=agent_name,
            command=cmd_name,
            input_plan=audit_input_plan,
        ),
    )
    return 0


def _build_prompt_command(cmd_name: str, kebab_name: str, *, has_plan: bool) -> click.Command:
    """Return a Click command that dispatches the named prompt command."""

    @click.pass_context
    def _cmd(ctx: click.Context, plan_path: str | None = None, *, edit_mode: bool = False) -> None:
        rc = _dispatch_prompt(ctx, cmd_name, plan_path, has_plan=has_plan, edit_mode=edit_mode)
        ctx.exit(rc)

    _cmd.__name__ = kebab_name
    _cmd.__doc__ = f"Send the '{cmd_name}' prompt to the responsible agent."
    _cmd = click.option(
        "--edit",
        "edit_mode",
        is_flag=True,
        help="Open rendered prompt in $EDITOR before sending.",
    )(_cmd)
    if has_plan:
        _cmd = click.argument("plan_path")(_cmd)
    return click.command(kebab_name)(_cmd)


def build_prompt_commands(config: WorkflowConfig) -> dict[str, click.Command]:
    """Build Click commands for all distinct prompt commands in the config."""
    prompt_commands: dict[str, click.Command] = {}
    for cmd_name, targets in _collect_prompt_targets(config).items():
        kebab_name = command_cli_name(cmd_name)
        has_plan = any(target.has_plan for target in targets)
        prompt_commands[kebab_name] = _build_prompt_command(cmd_name, kebab_name, has_plan=has_plan)
    return prompt_commands


def register_prompt_commands(cli: click.Group, config: WorkflowConfig) -> None:
    """Register prompt commands onto an existing Click group."""
    for cli_name, prompt_command in build_prompt_commands(config).items():
        cli.add_command(prompt_command, name=cli_name)


def send_prompt_to_agent(
    agent_name: str,
    prompt: str,
    config: WorkflowConfig,
    *,
    dry_run: bool = False,
) -> None:
    """Send a rendered prompt to the named agent's tmux window.

    Locates the session by ``config.tmux.session_name``, finds the window whose
    name matches *agent_name*, selects it, waits ``config.tmux.prompt_send_delay``
    seconds, then sends the prompt text followed by Enter.

    In dry-run mode prints the target window and prompt instead of sending.

    Raises :class:`~workflow.exceptions.WorkflowError` if the tmux server is not
    running, the session does not exist, or no matching window is found.
    """
    if dry_run:
        console.print(f"[dim]would send to {agent_name!r}:[/dim]")
        console.print(prompt)
        return

    hint = "Run 'workflow start' to create the tmux session."
    server = libtmux.Server()
    if not server.is_alive():
        msg = f"tmux server is not running. {hint}"
        raise WorkflowError(msg)

    session = server.sessions.get(session_name=config.tmux.session_name, default=None)
    if session is None:
        msg = f"tmux session {config.tmux.session_name!r} not found. {hint}"
        raise WorkflowError(msg)

    window = session.windows.get(window_name=agent_name, default=None)
    if window is None:
        msg = f"No tmux window named {agent_name!r} in session {config.tmux.session_name!r}. {hint}"
        raise WorkflowError(msg)

    window.select()
    time.sleep(config.tmux.prompt_send_delay)

    pane = window.active_pane
    if pane is None:
        msg = f"No active pane in window {agent_name!r}."
        raise WorkflowError(msg)
    pane.send_keys(prompt, enter=True, suppress_history=False, literal=True)
