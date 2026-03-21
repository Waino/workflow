"""Tmux prompt dispatch — stub only until plan 004.2 is implemented.

Plan 004.2 replaces this file with a full implementation that dynamically
registers Click commands from the YAML config, renders Jinja2 prompt templates,
and sends them to agent tmux windows via libtmux.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click

    from workflow.config import WorkflowConfig


def register_prompt_commands(cli: click.Group, config: WorkflowConfig) -> None:
    """Dynamically register prompt commands from *config* onto the CLI group.

    This is a no-op stub.  Prompt commands will not appear in ``--help`` until
    plan 004.2 is implemented.
    """
