# 002: Pydantic Models and Configuration

Input: `intent/001_idea`

## Goal

Define the foundational data models and configuration schema that all other components depend on.
This is the first plan to implement, as it has no dependencies on other plans.

## Scope

- Pydantic v2 models for plans, commands, audit log entries
- YAML configuration schema with pydantic validation
- Config loading from file with path resolution (project root → `~/.config/workflow/config.yaml` → CLI override)
- Fail-fast validation: error at startup if config references a non-existent script name

## Plan model

Represents a plan file in the state machine.

```python
class Plan(BaseModel):
    """A plan in the workflow state machine."""
    sequence_number: list[int]    # e.g. [2], [12, 1]
    slug: str                     # e.g. "pydantic_models"
    state: PlanState              # enum: intent, planning, todo, implementing, checking, done, rejected
    path: Path                    # absolute path to the .md file

    @property
    def sequence_number_str(self) -> str:
        """Dot-separated string representation, e.g. '002' or '012.1'."""
        parts = [str(n) for n in self.sequence_number]
        parts[0] = parts[0].zfill(3)  # zero-pad the first component
        return ".".join(parts)

    @property
    def name(self) -> str:
        """e.g. '002_pydantic_models'"""
        return f"{self.sequence_number_str}_{self.slug}"
```

`PlanState` is a `StrEnum` with values matching the subdirectory names.

Using `list[int]` allows calculations such as `max(p.sequence_number[0] for p in plans)`
to find the next free top-level sequence number.

## Command models

Discriminated union of prompt, script, and manual step commands, as specified in the config.

```python
class PromptCommand(BaseModel):
    """A command that is executed by typing it into an agent chat session."""
    command_type: Literal["prompt"]
    name: str
    prompt: str                   # the prompt text

class ScriptCommand(BaseModel):
    """A command that is executed by the human operator via the CLI."""
    command_type: Literal["script"]
    name: str
    script: str                   # registry key for the python function
    transition: PlanState | None = None  # target state, if this command causes a transition

class ManualStep(BaseModel):
    """An informative step in the workflow, not an actual command.

    Used in the cheat sheet to show steps like '( answer )', '( adjust )', '( pull )'.
    """
    command_type: Literal["manual"]
    name: str

Command = Annotated[PromptCommand | ScriptCommand | ManualStep, Field(discriminator="command_type")]
```

## State config model

Per-state configuration linking states to their available commands.

```python
class StateConfig(BaseModel):
    """Configuration for a single state in the workflow."""
    agent: str | None = None      # which agent works in this state (None = human-only)
    commands: list[Command] = []
```

## Audit log entry model

```python
class AuditLogEntry(BaseModel):
    """A single entry in the JSONL audit log."""
    timestamp: datetime
    actor: str                    # agent identity: "planner", "builder", "reviewer", "human"
    command: str                  # the command executed
    input_plan: str | None = None
    output_plans: list[str] = []
```

## Top-level config model

```python
class AgentConfig(BaseModel):
    """Configuration for a single agent."""
    name: str
    model: str                    # the LLM model to use

class WorkflowConfig(BaseModel):
    """Top-level configuration."""
    project_name: str
    project_dir: Path
    agents: list[AgentConfig]
    states: dict[str, StateConfig]

    @model_validator(mode="after")
    def validate_script_references(self) -> "WorkflowConfig":
        """Fail fast if config references a non-existent script name."""
        ...
```

## Config loading

```python
def load_config(config_path: Path | None = None) -> WorkflowConfig:
    """Load configuration from YAML file.

    Resolution order:
    1. Explicit path (CLI --config flag)
    2. ./config.yaml (project root, for development)
    3. ~/.config/workflow/config.yaml (installed location)
    """
```

## File organization

- `workflow/models.py` — Plan, PlanState, AuditLogEntry
- `workflow/config.py` — command models, StateConfig, AgentConfig, WorkflowConfig, load_config

## Dependencies

None — this is the foundational plan.

## Risks

- The config schema will evolve as later plans are implemented. Design for extensibility
  (optional fields with defaults) to minimize breaking changes.
- The `validate_script_references` validator depends on the registry (plan 004).
  Initially implement as a no-op or skip; wire up once the registry exists.

