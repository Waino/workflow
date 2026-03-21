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

from workflow.audit import write_audit_log
from workflow.config import PromptCommand, WorkflowConfig
from workflow.exceptions import (
    AmbiguousPlanError,
    PlanCollisionError,
    PlanNotFoundError,
    WorkflowError,
)
from workflow.git_helpers import git_pull
from workflow.models import AuditLogEntry, PlanState
from workflow.plan_resolver import resolve_plan
from workflow.shell import console


def _find_prompt_command(
    config: WorkflowConfig,
    cmd_name: str,
    state: PlanState,
) -> PromptCommand | None:
    """Return the PromptCommand named *cmd_name* in *state*, or ``None``."""
    sc = config.states.get(state)
    if sc is None:
        return None
    return next(
        (cmd for cmd in sc.commands if isinstance(cmd, PromptCommand) and cmd.name == cmd_name),
        None,
    )


def _render_prompt(template_str: str, **variables: str) -> str:
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


def _setup_has_plan(
    config: WorkflowConfig,
    cmd_name: str,
    plan_path_str: str,
) -> tuple[str, str, str | None] | None:
    """Resolve plan and render prompt for commands that take a plan argument.

    Returns ``(agent_name, rendered_prompt, plan_name)`` on success,
    or ``None`` after printing an error message.
    """
    assert config.project_dir is not None  # caller guarantees this
    try:
        plan = resolve_plan(plan_path_str, config.project_dir)
    except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError) as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    cmd_obj = _find_prompt_command(config, cmd_name, plan.state)
    if cmd_obj is None:
        console.print(f"[red]No '{cmd_name}' command available in state '{plan.state.value}'.[/red]")
        return None
    agent_name = config.states[plan.state].agent
    if agent_name is None:
        console.print(f"[red]No agent configured for state '{plan.state.value}'.[/red]")
        return None
    try:
        rendered = _render_prompt(cmd_obj.prompt, path_to_plan=str(plan.path))
    except WorkflowError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    return agent_name, rendered, plan.name


def _setup_stateless(
    config: WorkflowConfig,
    cmd_name: str,
) -> tuple[str, str, None] | None:
    """Resolve agent and render prompt for state-less commands (no plan argument).

    Returns ``(agent_name, rendered_prompt, None)`` on success,
    or ``None`` after printing an error message.
    """
    cmd_obj = _find_prompt_command(config, cmd_name, PlanState.REVIEW)
    if cmd_obj is None:
        console.print(f"[red]No '{cmd_name}' command found.[/red]")
        return None
    review_cfg = config.states.get(PlanState.REVIEW)
    agent_name = review_cfg.agent if review_cfg else None
    if agent_name is None:
        console.print("[red]No agent configured for review state.[/red]")
        return None
    try:
        rendered = _render_prompt(cmd_obj.prompt)
    except WorkflowError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    return agent_name, rendered, None


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
        console.print("[red]Error: not inside a workflow project directory.[/red]")
        return 1

    if has_plan:
        assert plan_path_str is not None  # Click enforces required=True when has_plan
        result = _setup_has_plan(config, cmd_name, plan_path_str)
    else:
        result = _setup_stateless(config, cmd_name)

    if result is None:
        return 1

    agent_name, prompt, audit_input_plan = result

    git_pull(config.project_dir / agent_name, dry_run=dry_run)

    if edit_mode:
        edited = _open_in_editor(prompt)
        if edited is None:
            console.print("Cancelled (empty prompt).")
            return 0
        prompt = edited

    try:
        send_prompt_to_agent(agent_name, prompt, config, dry_run=dry_run)
    except WorkflowError as exc:
        console.print(f"[red]{exc}[/red]")
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
    def _cmd(ctx: click.Context, plan_path: str | None = None, *, edit_mode: bool = False) -> int:
        return _dispatch_prompt(ctx, cmd_name, plan_path, has_plan=has_plan, edit_mode=edit_mode)

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


def register_prompt_commands(cli: click.Group, config: WorkflowConfig) -> None:
    """Dynamically register one Click command per distinct :class:`PromptCommand` name.

    Commands with the same name in multiple states share a single CLI entry point.
    Multi-word names (e.g. ``'full review'``) are exposed as kebab-case
    (``'full-review'``).  A command gets ``has_plan=False`` only when it appears
    exclusively in the ``review`` state; all others require a plan path argument.
    """
    cmd_states: dict[str, set[PlanState]] = {}
    for state, state_config in config.states.items():
        for cmd in state_config.commands:
            if isinstance(cmd, PromptCommand):
                cmd_states.setdefault(cmd.name, set()).add(state)

    for cmd_name, states in cmd_states.items():
        has_plan = any(s != PlanState.REVIEW for s in states)
        kebab_name = cmd_name.replace(" ", "-")
        cli.add_command(_build_prompt_command(cmd_name, kebab_name, has_plan=has_plan))


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
