"""Configuration models and loading for the workflow tool."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from rich.text import Text

from workflow.exceptions import WorkflowError
from workflow.layout import Layout
from workflow.metadata import LEGACY_ROOT_MARKER, METADATA_FILENAME, ProjectMetadata, read_metadata
from workflow.models import PlanState
from workflow.names import WorktreeName

CONFIG_FILENAME = "config.yaml"
NOT_IN_PROJECT_MSG = "Error: not inside a workflow project directory."


# --- Command models (discriminated union) ---


class WorkflowStep(BaseModel, ABC):
    """Base class for commands and manual steps."""

    name: str
    description: str

    @model_validator(mode="after")
    def validate_description_not_blank(self) -> WorkflowStep:
        """Reject whitespace-only descriptions."""
        if not self.description.strip():
            msg = f"description for command {self.name!r} must not be empty or whitespace-only"
            raise ValueError(msg)
        return self

    @abstractmethod
    def render(self) -> Text:
        """Render the cheatsheet representation of the workflow step."""


class PromptCommand(WorkflowStep):
    """A command that is executed by typing it into an agent chat session."""

    command_type: Literal["prompt"]
    prompt: str
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
    worktree_override: WorktreeName | None = None
    also_valid_in_states: list[PlanState] = []
    pull_target: WorktreeName | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_repo_override(cls, data: object) -> object:
        """Fail with a clear pointer to the migration script on a leftover repo_override key."""
        if isinstance(data, dict) and "repo_override" in data:
            msg = (
                "config.yaml uses the old key 'repo_override' (renamed to 'worktree_override'). "
                "Run `python scripts/migrate_023.py --project-dir <project>` to migrate."
            )
            raise ValueError(msg)
        return data

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

    description: str
    agent: str | None = None
    commands: list[Command] = []

    @model_validator(mode="after")
    def validate_description_not_blank(self) -> StateConfig:
        """Reject whitespace-only descriptions."""
        if not self.description.strip():
            msg = "StateConfig description must not be empty or whitespace-only"
            raise ValueError(msg)
        return self


class AgentConfig(BaseModel):
    """Configuration for a single agent."""

    name: str
    model: str | None = None
    manual_shell: bool = False
    """Whether this agent's repo backs the ``builder-shell`` window targeted by ``go manual``."""


class TmuxConfig(BaseModel):
    """Configuration for tmux integration (prompt dispatch via libtmux)."""

    session_name: str = "workflow"
    """Name of the tmux session created by `workflow start`."""

    prompt_send_delay: float = 1.0
    """Seconds to wait after window switch before send-keys."""


# --- Top-level config ---


class WorkflowConfig(BaseModel):
    """Top-level configuration.

    project_dir, project_name, and metadata are not in the YAML config file.
    They are discovered at runtime and populated by load_config.
    All three are None when no .workflow.json was found (i.e. outside a project directory).
    Only the 'init' and 'cheat' commands are supported in that case.
    """

    project_dir: Path | None = None
    project_name: str | None = None
    metadata: ProjectMetadata | None = None
    agents: list[AgentConfig]
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    states: dict[PlanState, StateConfig]
    managed_hooks: dict[str, str] = Field(default_factory=lambda: {"pre-commit": ".git-pre-commit-hook"})
    """Maps hook filename (in .git/hooks/) to version-controlled source path (relative to repo root)."""

    @model_validator(mode="after")
    def validate_script_references(self) -> WorkflowConfig:
        """Fail fast if config references a script name not in the registry.

        All script command modules must be imported before load_config is called
        so that the registry is fully populated when this validator runs.
        """
        # lazy import to avoid a circular dependency between config.py and
        # registry.py (registry.py imports ScriptCommand from config.py).

        from workflow.registry import _SCRIPT_REGISTRY, ConfigError  # noqa: PLC0415

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
    def validate_manual_shell_at_most_once(self) -> WorkflowConfig:
        """Check that at most one agent is marked as the manual-shell agent."""
        manual_agents = [a.name for a in self.agents if a.manual_shell]
        if len(manual_agents) > 1:
            msg = f"At most one agent may set manual_shell; got: {sorted(manual_agents)}"
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

    def require_project_dir(self) -> Path:
        """Return project_dir, raising WorkflowError when not inside a workflow project directory."""
        if self.project_dir is None:
            raise WorkflowError(NOT_IN_PROJECT_MSG)
        return self.project_dir

    def layout(self) -> Layout:
        """Build this project's Layout from project_dir and metadata.

        Raises WorkflowError when either is None, i.e. when not inside a workflow project directory.
        """
        if self.metadata is None:
            raise WorkflowError(NOT_IN_PROJECT_MSG)
        return Layout(project_dir=self.require_project_dir(), metadata=self.metadata)


# --- Project root discovery ---


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk from start (default: cwd) towards filesystem root, looking for .workflow.json.

    Returns the directory containing the marker, or None if not found.
    """
    return _walk_for_marker(start, METADATA_FILENAME)


def find_legacy_project_root(start: Path | None = None) -> Path | None:
    """Walk from start (default: cwd) towards filesystem root, looking for the legacy .workflow_root marker.

    Returns the directory containing the marker, or None if not found.
    """
    return _walk_for_marker(start, LEGACY_ROOT_MARKER)


def _walk_for_marker(start: Path | None, marker: str) -> Path | None:
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / marker).exists():
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

    If no project root is found and no project_dir is given, project_dir,
    project_name, and metadata are set to None in the returned config. Commands
    that require a project directory must check for this and fail fast with a
    clear error. Only 'init' and 'cheat' are supported without a project root.

    Raises WorkflowError if only a legacy .workflow_root marker is found (no
    .workflow.json), naming the migration script to run.
    """
    if project_dir is None:
        project_dir = find_project_root()
        if project_dir is None:
            legacy_dir = find_legacy_project_root()
            if legacy_dir is not None:
                msg = (
                    f"{legacy_dir} contains a legacy .workflow_root marker but no .workflow.json.\n"
                    f"Run `python scripts/migrate_023.py --project-dir {legacy_dir}` to migrate."
                )
                raise WorkflowError(msg)
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
    raw["metadata"] = read_metadata(project_dir) if project_dir is not None else None

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
