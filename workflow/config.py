"""Configuration models and loading for the workflow tool."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from rich.text import Text

from workflow.models import PlanState

WORKFLOW_ROOT_MARKER = ".workflow_root"
CONFIG_FILENAME = "config.yaml"


# --- Command models (discriminated union) ---


class WorkflowStep(BaseModel, ABC):
    """Base class for commands and manual steps."""

    name: str

    @abstractmethod
    def render(self) -> Text:
        """Render the cheatsheet representation of the workflow step."""


class PromptCommand(WorkflowStep):
    """A command that is executed by typing it into an agent chat session."""

    command_type: Literal["prompt"]
    prompt: str
    also_valid_in_states: list[PlanState] = Field(default_factory=list)
    """Allow the prompt to be used in other states, altering the typical workflow.
    Not shown in the cheatsheet."""
    transition: PlanState | None = None
    reminder: str | None = None

    def render(self) -> Text:
        """Render the cheatsheet representation of the workflow step."""
        result = Text.from_markup(f"[bold]@[/bold] [light_salmon3]{self.name}[/light_salmon3]")
        if self.transition:
            result.append_text(Text.from_markup(" [bold]>[/bold]"))
        return result


class ScriptCommand(WorkflowStep):
    """A command that is executed by the human operator via the CLI."""

    command_type: Literal["script"]
    script: str
    transition: PlanState | None = None
    reminder: str | None = None

    def render(self) -> Text:
        """Render the cheatsheet representation of the workflow step."""
        result = Text.from_markup(f"[bold]-[/bold] [turquoise4]{self.name}[/turquoise4]")
        if self.transition:
            result.append_text(Text.from_markup(" [bold]>[/bold]"))
        return result


class ManualStep(WorkflowStep):
    """An informative step in the workflow, not an actual command."""

    command_type: Literal["manual"]

    def render(self) -> Text:
        """Render the cheatsheet representation of the workflow step."""
        return Text.from_markup(f"[steel_blue3][bold]([/bold] {self.name} [bold])[/bold][/steel_blue3]")


Command = Annotated[PromptCommand | ScriptCommand | ManualStep, Field(discriminator="command_type")]


# --- State and agent config ---


class StateConfig(BaseModel):
    """Configuration for a single state in the workflow."""

    agent: str | None = None
    commands: list[Command] = []


class AgentConfig(BaseModel):
    """Configuration for a single agent."""

    name: str
    model: str


class TmuxConfig(BaseModel):
    """Configuration for tmux integration (prompt dispatch via libtmux)."""

    session_name: str = "workflow"
    """Name of the tmux session created by `workflow start`."""

    prompt_send_delay: float = 1.0
    """Seconds to wait after window switch before send-keys."""


# --- Top-level config ---


class WorkflowConfig(BaseModel):
    """Top-level configuration.

    project_dir and project_name are not in the YAML config file.
    They are discovered at runtime and populated by load_config.
    Both are None when no .workflow_root marker was found (i.e. outside a project directory).
    Only the 'init' and 'cheat' commands are supported in that case.
    """

    project_dir: Path | None = None
    project_name: str | None = None
    agents: list[AgentConfig]
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    states: dict[PlanState, StateConfig]
    managed_hooks: dict[str, str] = Field(default_factory=lambda: {"pre-commit": ".git-pre-commit-hook"})
    """Maps hook filename (in .git/hooks/) to version-controlled source path (relative to repo root)."""

    @model_validator(mode="after")
    def validate_script_references(self) -> WorkflowConfig:
        """Fail fast if config references a script name not in the registry.

        Uses a lazy import to avoid a circular dependency between config.py and
        registry.py (registry.py imports ScriptCommand from config.py).

        All script command modules must be imported before load_config is called
        so that the registry is fully populated when this validator runs.
        """
        from workflow.registry import _SCRIPT_REGISTRY, ConfigError

        for state_config in self.states.values():
            for cmd in state_config.commands:
                if isinstance(cmd, ScriptCommand) and cmd.script not in _SCRIPT_REGISTRY:
                    msg = f"Config references unknown script: {cmd.script!r}"
                    raise ConfigError(msg)
        return self

    @model_validator(mode="after")
    def validate_agent_names_unique(self) -> WorkflowConfig:
        """Check that agent names are unique."""
        names = [a.name for a in self.agents]
        if len(names) != len(set(names)):
            duplicates = {n for n in names if names.count(n) > 1}
            msg = f"Duplicate agent names: {sorted(duplicates)}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_agent_references(self) -> WorkflowConfig:
        """Check that agent names in StateConfig reference defined agents."""
        agent_names = {a.name for a in self.agents}
        for state, state_config in self.states.items():
            if state_config.agent and state_config.agent not in agent_names:
                msg = f"State {state!r} references unknown agent {state_config.agent!r}"
                raise ValueError(msg)
        return self


# --- Project root discovery ---


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk from start (default: cwd) towards filesystem root, looking for .workflow_root.

    Returns the directory containing the marker, or None if not found.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / WORKFLOW_ROOT_MARKER).exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


# --- Config loading ---


def load_config(config_path: Path | None = None, project_dir: Path | None = None) -> WorkflowConfig:
    """Load configuration from YAML file and populate runtime fields.

    YAML resolution order:
    1. Explicit path (CLI --config flag)
    2. {project_dir}/config.yaml (project root)
    3. ~/.config/workflow/config.yaml (user-level)

    project_dir is discovered via find_project_root() unless explicitly provided.
    project_name is derived from project_dir.

    If no project root is found and no project_dir is given, project_dir and
    project_name are set to None in the returned config. Commands that require
    a project directory must check for this and fail fast with a clear error.
    Only 'init' and 'cheat' are supported without a project root.
    """
    if project_dir is None:
        project_dir = find_project_root()
    if project_dir is not None:
        project_dir = project_dir.resolve()

    yaml_path = _resolve_config_path(config_path, project_dir)
    with yaml_path.open() as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            msg = f"Could not parse config file at {yaml_path}: {exc}"
            raise ValueError(msg) from exc

    raw["project_dir"] = str(project_dir) if project_dir is not None else None
    raw["project_name"] = project_dir.name if project_dir is not None else None

    return WorkflowConfig(**raw)


def _resolve_config_path(config_path: Path | None, project_dir: Path | None) -> Path:
    """Find the config file, raising FileNotFoundError if none exists."""
    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(config_path)
    if project_dir is not None:
        candidates.append(project_dir / CONFIG_FILENAME)
    candidates.append(Path.home() / ".config" / "workflow" / CONFIG_FILENAME)

    for path in candidates:
        if path.is_file():
            return path

    searched = ", ".join(str(p) for p in candidates)
    msg = f"No config file found. Searched: {searched}"
    raise FileNotFoundError(msg)
