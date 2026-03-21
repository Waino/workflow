"""Command line interface for workflow.

Import of command modules must happen before load_config is called so that
the script registry is fully populated when WorkflowConfig.validate_script_references
runs during model construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

import workflow.commands.info
import workflow.commands.workflow  # noqa: F401  — registers workflow commands
from workflow.config import load_config
from workflow.tmux_dispatch import register_prompt_commands


@click.group()
@click.option("--dry-run", is_flag=True, help="Print commands without executing them.")
@click.option("--config", "config_path", type=click.Path(exists=True), help="Path to config file.")
@click.pass_context
def main(ctx: click.Context, *, dry_run: bool, config_path: str | None) -> int:
    """Agentic workflow orchestration tool."""
    config = load_config(Path(config_path) if config_path else None)
    register_prompt_commands(main, config)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config
    ctx.obj["dry_run"] = dry_run
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
