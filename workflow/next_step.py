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


def find_step_by_name(config: WorkflowConfig, name: str) -> Command | None:
    """Return the first command named *name* across any state, or ``None``."""
    for state_config in config.states.values():
        for command in state_config.commands:
            if command.name == name:
                return command
    return None


def format_next_step(config: WorkflowConfig, command: Command, plan_name: str) -> str | None:
    """Return the display text for what to do after *command* ran on *plan_name*, or ``None``.

    A reminder naming a non-manual command becomes a runnable ``workflow ...`` line;
    anything else (manual steps, free text such as ``accept or reject``) is returned verbatim.
    """
    reminder = getattr(command, "reminder", None)
    if not reminder:
        return None
    step = find_step_by_name(config, reminder)
    if step is None or step.command_type == "manual":
        return reminder
    return f"workflow {command_cli_name(reminder)} {plan_name}"
