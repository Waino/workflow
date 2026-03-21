# 002: Pydantic Models and Configuration

Input: `intent/001_idea`

## Goal

Define the foundational data models and configuration schema that all other components depend on.
This is the first plan to implement, as it has no dependencies on other plans.

## Scope

- Pydantic v2 models for plans, commands, audit log entries
- YAML configuration schema with pydantic validation
- Config loading from file with path resolution (CLI override → project root → `~/.config/workflow/config.yaml`)
- Project root discovery by walking up from cwd to find `.workflow_root` marker
- Default config shipped in `docs/default_config.yaml`
- Fail-fast validation: error at startup if config references a non-existent script name

## Plan model

Represents a plan file in the state machine.

```python
class PlanState(StrEnum):
    """States in the plan state machine. Values match subdirectory names under docs/plans/."""
    INTENT = "intent"
    PLANNING = "planning"
    TODO = "todo"
    IMPLEMENTING = "implementing"
    CHECKING = "checking"
    DONE = "done"
    REJECTED = "rejected"
    REVIEW = "review"

class ReviewSubState(StrEnum):
    """Sub-states within the reviews directory."""
    CRITIQUE = "critique"
    PR = "pr"
    FULL = "full"

class Plan(BaseModel):
    """A plan in the workflow state machine."""
    sequence_number: list[int]    # e.g. [2], [12, 1]
    slug: str                     # e.g. "pydantic_models"
    state: PlanState              # enum: intent, planning, todo, implementing, checking, done, rejected
    review_sub_state: ReviewSubState | None = None  # set when state context is reviews/
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

Review documents live under `docs/plans/reviews/{sub_state}/` and use `state = PlanState.REVIEW`
with `review_sub_state` set to distinguish critique, pr, and full reviews.
For most purposes the sub-states behave the same; the distinction is primarily for display (status, cheat sheet).

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
    transition: PlanState | None = None  # target state; transition performed by agent, displayed in cheat sheet

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
    """A single entry in the JSONL audit log.

    The CLI always writes log entries; no agent writes log entries directly.
    """
    timestamp: datetime
    actor: str                    # participant this action was performed for:
                                  # "human" for script commands, agent name for prompt dispatch
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

class TmuxConfig(BaseModel):
    """Configuration for tmux integration (prompt dispatch via libtmux)."""
    session_name: str = "workflow"  # name of the tmux session created by `workflow start`
    prompt_send_delay: float = 1.0  # seconds to wait after window switch before send-keys

class WorkflowConfig(BaseModel):
    """Top-level configuration.

    project_dir and project_name are not in the YAML config file.
    They are discovered at runtime and populated by load_config.
    """
    project_dir: Path             # discovered by walking up from cwd to find .workflow_root
    project_name: str             # derived: project_dir.name
    agents: list[AgentConfig]
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    states: dict[PlanState, StateConfig]

    @model_validator(mode="after")
    def validate_script_references(self) -> "WorkflowConfig":
        """Fail fast if config references a non-existent script name.

        No-op until the registry (plan 004.1) is implemented.
        """
        return self

    @model_validator(mode="after")
    def validate_agent_references(self) -> "WorkflowConfig":
        """Check that agent names in StateConfig reference defined agents."""
        agent_names = {a.name for a in self.agents}
        for state, state_config in self.states.items():
            if state_config.agent and state_config.agent not in agent_names:
                raise ValueError(
                    f"State {state!r} references unknown agent {state_config.agent!r}"
                )
        return self
```

## Project root discovery

```python
def find_project_root(start: Path | None = None) -> Path | None:
    """Walk from start (default: cwd) towards filesystem root, looking for .workflow_root.

    Returns the directory containing the marker, or None if not found.
    """
```

When no project root is found, only `workflow init` and `workflow cheat` are supported.
All other commands should fail fast with a clear error message.

## Config loading

```python
def load_config(config_path: Path | None = None, project_dir: Path | None = None) -> WorkflowConfig:
    """Load configuration from YAML file and populate runtime fields.

    YAML resolution order:
    1. Explicit path (CLI --config flag)
    2. {project_dir}/config.yaml (project root, for development)
    3. ~/.config/workflow/config.yaml (installed location)

    project_dir and project_name are populated from find_project_root(),
    or from the explicit project_dir argument (used by `init`).
    """
```

## Default config

A default config file is shipped at `docs/default_config.yaml`.
It defines the standard three agents (planner, builder, reviewer), the default tmux settings,
and all states with their commands (including full prompt texts) as described in `intent/001_idea`.
`workflow init` copies this file into the project root.

## File organization

- `workflow/models.py` — Plan, PlanState, ReviewSubState, AuditLogEntry
- `workflow/config.py` — command models, StateConfig, AgentConfig, WorkflowConfig, load_config, find_project_root

## Dependencies

None — this is the foundational plan.

## Risks

- The config schema will evolve as later plans are implemented. Design for extensibility
  (optional fields with defaults) to minimize breaking changes.
- The `validate_script_references` validator depends on the registry (plan 004.1).
  Initially implement as a no-op; wire up once the registry exists.
- `dict[PlanState, StateConfig]` as a Pydantic field: Pydantic v2 handles StrEnum keys
  well, but the YAML will have plain string keys. A validator or custom type may be needed
  to coerce string keys to PlanState on load. Test this early.

