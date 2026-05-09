"""Resolve the next step to suggest after running a command on a plan.

Shared by ``commands/workflow.py`` (post-command reminders) and
``commands/info.py`` (the ``status`` table).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workflow.names import command_cli_name

if TYPE_CHECKING:
    from workflow.config import Command, WorkflowConfig
    from workflow.models import PlanState


def find_command(config: WorkflowConfig, state: PlanState, command_name: str) -> Command | None:
    """Return the command named *command_name* in *state*, or ``None``."""
    state_config = config.states.get(state)
    if state_config is None:
        return None
    for command in state_config.commands:
        if command.name == command_name:
            return command
    return None


def find_source_state_for_command(
    config: WorkflowConfig,
    plan_state: PlanState,
    command_name: str,
) -> PlanState | None:
    """Locate the source state for *command_name* given the plan's current state.

    The audit log only records the command name, so this picks the state whose
    command matches *plan_state* at the time the command ran:

    * Prefer a step whose ``transition`` equals *plan_state* (the command moved
      the plan into its current state).
    * Otherwise fall back to *plan_state* itself if it contains a step with that
      name and no transition.
    """
    transition_match: PlanState | None = None
    same_state_match: PlanState | None = None
    for state, state_config in config.states.items():
        for command in state_config.commands:
            if command.name != command_name:
                continue
            transition = getattr(command, "transition", None)
            if transition == plan_state:
                transition_match = state
            elif transition is None and state == plan_state:
                same_state_match = state
    return transition_match or same_state_match


def find_reminder_name(config: WorkflowConfig, state: PlanState, command_name: str) -> str | None:
    """Return the reminder command name for *command_name* in *state*, or ``None``."""
    command = find_command(config, state, command_name)
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


def find_step_by_name(config: WorkflowConfig, name: str) -> Command | None:
    """Return the first command named *name* across any state, or ``None``."""
    for state_config in config.states.values():
        for command in state_config.commands:
            if command.name == name:
                return command
    return None


def find_next_step(config: WorkflowConfig, source_state: PlanState, command_name: str) -> Command | None:
    """Resolve the next step to suggest after running *command_name* in *source_state*.

    Returns the matching :class:`Command` (suitable for ``.render()``), or
    ``None`` when no reminder is configured.
    """
    reminder_name = find_reminder_name(config, source_state, command_name)
    if reminder_name is None:
        return None
    return find_step_by_name(config, reminder_name)


def format_next_step_invocation(reminder: str, plan_name: str, *, command: Command | None) -> str:
    """Format *reminder* as a runnable hint, e.g. ``workflow todo 001_foo``.

    Manual steps are returned as-is.
    """
    if command is None or command.command_type == "manual":
        return reminder
    return f"workflow {command_cli_name(reminder)} {plan_name}"
