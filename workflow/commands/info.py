"""Informational script commands: status, log."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING

import click
from rich.table import Table

from workflow.audit import read_audit_log
from workflow.config import WorkflowConfig
from workflow.exceptions import AmbiguousPlanError, PlanCollisionError, PlanNotFoundError, WorkflowError
from workflow.git_helpers import git_status
from workflow.models import PlanState
from workflow.names import WorktreeName
from workflow.next_step import find_next_step, find_source_state_for_command
from workflow.plan_resolver import find_all_plans, next_intent_number, resolve_plan
from workflow.registry import register_script
from workflow.shell import console, error_console
from workflow.stack import read_plan_frontmatter

if TYPE_CHECKING:
    from pathlib import Path

    from rich.text import Text

    from workflow.models import AuditLogEntry, Plan


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime or date-only string.

    Tries ``datetime.fromisoformat`` first, then falls back to ``date.fromisoformat``
    (treating the date as midnight UTC). Naive datetimes are assumed to be UTC.
    Raises :class:`ValueError` when neither parse succeeds.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        d = date.fromisoformat(value)  # raises ValueError if still invalid
        dt = datetime.combine(d, time.min)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _plan_matches_filter(entry: AuditLogEntry, plan_name: str, plan_seq_str: str) -> bool:
    """Return True when *entry* references the plan by name or sequence number string."""
    all_names = [entry.input_plan, *entry.output_plans]
    return any(n is not None and n in (plan_name, plan_seq_str) for n in all_names)


def _plan_matches_substring(entry: AuditLogEntry, needle: str) -> bool:
    """Return True when *entry* references a plan whose name contains *needle* (case-insensitive)."""
    all_names = [entry.input_plan, *entry.output_plans]
    return any(n is not None and needle in n.lower() for n in all_names)


def _resolve_plan_filter(
    plan_filter: str,
    config: WorkflowConfig,
) -> tuple[str | None, str | None, bool]:
    """Resolve *plan_filter* to (plan_name, plan_seq_str, use_substring).

    On successful resolution returns the plan's name and sequence-number string with
    ``use_substring=False``. On any resolution error returns ``(None, None, True)``
    to trigger a case-insensitive substring match instead.
    """
    try:
        resolved = resolve_plan(plan_filter, config=config, command_name=None)
    except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError, WorkflowError):
        error_console.print("[yellow]Plan filter not uniquely resolved; falling back to substring match.[/yellow]")
        return None, None, True
    else:
        return resolved.name, resolved.sequence_number_str, False


def _entry_matches(  # noqa: PLR0911, PLR0913
    entry: AuditLogEntry,
    *,
    agent: str | None,
    cmd_filter: str | None,
    since_dt: datetime | None,
    until_dt: datetime | None,
    plan_filter: str | None,
    plan_name: str | None,
    plan_seq_str: str | None,
    use_substring: bool,
) -> bool:
    """Return True when *entry* passes all active filters."""
    if agent is not None and entry.actor != agent:
        return False
    if cmd_filter is not None and entry.command != cmd_filter:
        return False
    if since_dt is not None and entry.timestamp < since_dt:
        return False
    if until_dt is not None and entry.timestamp > until_dt:
        return False
    if plan_filter is None:
        return True
    if use_substring:
        return _plan_matches_substring(entry, plan_filter.lower())
    if plan_name is not None and plan_seq_str is not None:
        return _plan_matches_filter(entry, plan_name, plan_seq_str)
    return True


def _last_audit_entry_for_plan(entries: list[AuditLogEntry], plan: Plan) -> AuditLogEntry | None:
    """Return the most recent audit log entry referencing *plan* by name, or ``None``.

    Assumes *entries* is sorted ascending by timestamp (as :func:`read_audit_log` returns).
    """
    for entry in reversed(entries):
        if entry.input_plan == plan.name or plan.name in entry.output_plans:
            return entry
    return None


@register_script("status")
@click.option("--all", "show_all", is_flag=True, default=False, help="Include done and rejected plans.")
@click.pass_context
def status(ctx: click.Context, show_all: bool) -> None:  # noqa: FBT001
    """Show the current state of all plans across the pipeline."""
    config: WorkflowConfig = ctx.obj["config"]

    if config.project_dir is None:
        error_console.print("[red]Error: not inside a workflow project directory.[/red]")
        ctx.exit(1)
        return
    project_dir: Path = config.project_dir
    layout = config.layout()

    terminal_states = {PlanState.DONE, PlanState.REJECTED}
    plans = find_all_plans(layout)
    visible = [p for p in plans if p.state != PlanState.REVIEW and (show_all or p.state not in terminal_states)]
    visible.sort(key=lambda p: p.sequence_number)
    entries = read_audit_log(project_dir)

    table = Table(title="Workflow Status")
    table.add_column("#", style="bold", no_wrap=True)
    table.add_column("name")
    table.add_column("state")
    table.add_column("stack")
    table.add_column("last action")
    table.add_column("next step")

    for plan in visible:
        last_entry = _last_audit_entry_for_plan(entries, plan)
        last_action = last_entry.command if last_entry is not None else ""
        next_step_renderable: str | Text = ""
        if last_entry is not None:
            source_state = find_source_state_for_command(config, plan.state, last_entry.command)
            if source_state is not None:
                next_step = find_next_step(config, source_state, last_entry.command)
                if next_step is not None:
                    next_step_renderable = next_step.render()
        fm = read_plan_frontmatter(layout, plan)
        stack_cell = fm.onto if fm is not None and fm.onto is not None else ""
        table.add_row(
            plan.sequence_number_str,
            plan.slug,
            plan.state.value,
            stack_cell,
            last_action,
            next_step_renderable,
        )

    console.print(table)
    next_number = next_intent_number(layout)
    console.print(f"Next free number: [bold]{next_number:03d}[/bold]")

    for worktree in WorktreeName:
        console.print(f"Git status for worktree {worktree}")
        git_status(layout.worktree_root(worktree))
    ctx.exit(0)


@register_script("log")
@click.option("--agent", default=None, help="Filter by actor.")
@click.option("--plan", "plan_filter", default=None, help="Filter by plan name or number.")
@click.option("--command", "cmd_filter", default=None, help="Filter by command name.")
@click.option("--since", default=None, help="Show entries at or after this date/time (ISO 8601 or YYYY-MM-DD).")
@click.option("--until", default=None, help="Show entries at or before this date/time (ISO 8601 or YYYY-MM-DD).")
@click.pass_context
def log(  # noqa: PLR0913
    ctx: click.Context,
    agent: str | None,
    plan_filter: str | None,
    cmd_filter: str | None,
    since: str | None,
    until: str | None,
) -> None:
    """Pretty-print the audit log."""
    config: WorkflowConfig = ctx.obj["config"]

    if config.project_dir is None:
        error_console.print("[red]Error: not inside a workflow project directory.[/red]")
        ctx.exit(1)
        return
    project_dir: Path = config.project_dir

    since_dt: datetime | None = None
    until_dt: datetime | None = None
    if since is not None:
        try:
            since_dt = _parse_datetime(since)
        except ValueError:
            error_console.print(f"[red]Invalid --since value: {since!r}. Expected ISO 8601 or YYYY-MM-DD.[/red]")
            ctx.exit(1)
            return
    if until is not None:
        try:
            until_dt = _parse_datetime(until)
        except ValueError:
            error_console.print(f"[red]Invalid --until value: {until!r}. Expected ISO 8601 or YYYY-MM-DD.[/red]")
            ctx.exit(1)
            return

    plan_name: str | None = None
    plan_seq_str: str | None = None
    use_substring = False
    if plan_filter is not None:
        plan_name, plan_seq_str, use_substring = _resolve_plan_filter(plan_filter, config)

    entries = read_audit_log(project_dir)
    filtered = [
        e
        for e in entries
        if _entry_matches(
            e,
            agent=agent,
            cmd_filter=cmd_filter,
            since_dt=since_dt,
            until_dt=until_dt,
            plan_filter=plan_filter,
            plan_name=plan_name,
            plan_seq_str=plan_seq_str,
            use_substring=use_substring,
        )
    ]

    table = Table(title="Audit Log")
    table.add_column("timestamp", no_wrap=True)
    table.add_column("actor")
    table.add_column("command")
    table.add_column("input plan")
    table.add_column("output plans")

    for entry in filtered:
        command_cell = entry.command
        if entry.stacked_on:
            command_cell = f"{entry.command} (onto {entry.stacked_on[-1]})"
        table.add_row(
            entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            entry.actor,
            command_cell,
            entry.input_plan or "",
            ", ".join(entry.output_plans),
        )

    console.print(table)
    ctx.exit(0)
