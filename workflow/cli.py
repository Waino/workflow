"""Command line interface for workflow.

Import of command modules must happen before load_config is called so that
the script registry is fully populated when WorkflowConfig.validate_script_references
runs during model construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from pydantic import BaseModel, ConfigDict

import workflow.commands.cheat
import workflow.commands.info
import workflow.commands.workflow  # noqa: F401  — registers workflow commands
from workflow.config import ScriptCommand, WorkflowConfig, load_config
from workflow.names import command_cli_name
from workflow.registry import _SCRIPT_REGISTRY, ConfigError
from workflow.tmux_dispatch import build_prompt_commands

_SCRIPT_COMMAND_CACHE: dict[tuple[str, str, int], click.Command] = {}


def _build_script_command(script_name: str, cli_name: str) -> click.Command:
    """Build a Click command from a registered script function.

    Click decorators applied to the registered function (e.g. ``@click.argument``,
    ``@click.option``, ``@click.pass_context``) are preserved because
    ``click.command()`` reads ``__click_params__`` from the function directly.
    Plain no-argument stub functions also work: Click treats them as argument-free commands.
    """
    script_func = _SCRIPT_REGISTRY[script_name]
    cache_key = (script_name, cli_name, id(script_func))
    cached = _SCRIPT_COMMAND_CACHE.get(cache_key)
    if cached is not None:
        return cached
    command = click.command(cli_name)(script_func)
    _SCRIPT_COMMAND_CACHE[cache_key] = command
    return command


def _build_script_commands(config: WorkflowConfig) -> dict[str, click.Command]:
    """Build Click commands for configured and standalone registered script commands."""
    script_targets: dict[str, str] = {}
    for state_config in config.states.values():
        for cmd in state_config.commands:
            if not isinstance(cmd, ScriptCommand):
                continue
            cli_name = command_cli_name(cmd.name)
            existing_script = script_targets.get(cli_name)
            if existing_script is not None and existing_script != cmd.script:
                msg = f"CLI command {cli_name!r} is mapped to multiple scripts: {existing_script!r}, {cmd.script!r}"
                raise ConfigError(msg)
            script_targets[cli_name] = cmd.script
    for script_name in _SCRIPT_REGISTRY:
        cli_name = command_cli_name(script_name)
        script_targets.setdefault(cli_name, script_name)
    return {cli_name: _build_script_command(script_name, cli_name) for cli_name, script_name in script_targets.items()}


def _build_dynamic_commands(config: WorkflowConfig) -> dict[str, click.Command]:
    """Build the full dynamic command map for the configured workflow."""
    commands = _build_script_commands(config)
    for cli_name, prompt_command in build_prompt_commands(config).items():
        if cli_name in commands:
            msg = f"CLI command name {cli_name!r} is defined by both a script and a prompt command"
            raise ConfigError(msg)
        commands[cli_name] = prompt_command
    return commands


class _CliRuntime(BaseModel):
    """Cached runtime data for one top-level CLI invocation."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    config: WorkflowConfig
    dry_run: bool
    commands: dict[str, click.Command]


class WorkflowGroup(click.Group):
    """Click group that loads config before dynamic subcommand lookup."""

    _runtime_cache_key = "workflow_runtime"

    def get_runtime(self, ctx: click.Context) -> _CliRuntime:
        """Return the cached CLI runtime for the current top-level invocation."""
        cached = ctx.meta.get(self._runtime_cache_key)
        if cached is not None:
            return cached

        config_path = ctx.params.get("config_path")
        dry_run = bool(ctx.params.get("dry_run", False))
        config = load_config(config_path)
        runtime = _CliRuntime(config=config, dry_run=dry_run, commands=_build_dynamic_commands(config))
        ctx.meta[self._runtime_cache_key] = runtime
        return runtime

    def list_commands(self, ctx: click.Context) -> list[str]:
        """List statically defined and config-driven subcommands."""
        dynamic_names = self.get_runtime(ctx).commands.keys()
        return sorted({*super().list_commands(ctx), *dynamic_names})

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """Return a statically defined or config-driven subcommand by name."""
        command = self.get_runtime(ctx).commands.get(cmd_name)
        if command is not None:
            return command
        return super().get_command(ctx, cmd_name)


@click.group(cls=WorkflowGroup)
@click.option("--dry-run", is_flag=True, help="Print commands without executing them.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to config file.",
)
@click.pass_context
def main(ctx: click.Context, *, dry_run: bool, config_path: Path | None) -> None:
    """Agentic workflow orchestration tool."""
    del dry_run, config_path
    assert isinstance(ctx.command, WorkflowGroup)
    runtime = ctx.command.get_runtime(ctx)
    ctx.obj = {"config": runtime.config, "dry_run": runtime.dry_run}


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
