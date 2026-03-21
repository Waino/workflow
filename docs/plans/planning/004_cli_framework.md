# 004: CLI Framework, Registry, and Audit Logging

Input: `intent/001_idea`

## Goal

Build the CLI skeleton that ties together configuration, command dispatch, dry-run mode,
and audit logging. This is the framework that all script commands plug into.

## Scope

- Click CLI with global options (`--dry-run`, `--config`)
- Script registry pattern linking config entries to Python functions
- Audit log writing and reading
- Config validation wired to registry (fail-fast)

## CLI structure

```python
@click.group()
@click.option("--dry-run", is_flag=True, help="Print commands without executing them.")
@click.option("--config", "config_path", type=click.Path(exists=True), help="Path to config file.")
@click.pass_context
def cli(ctx: click.Context, dry_run: bool, config_path: str | None) -> None:
    """Agentic workflow orchestration tool."""
    config = load_config(Path(config_path) if config_path else None)
    validate_registry(config)  # fail fast
    ctx.ensure_object(dict)
    ctx.obj["config"] = config
    ctx.obj["dry_run"] = dry_run
```

Each script command is a `@cli.command()`. The registry maps config `script` keys to functions.

## Script registry

```python
_SCRIPT_REGISTRY: dict[str, ScriptFunc] = {}

ScriptFunc = Callable[..., int]  # return code

def register_script(name: str) -> Callable[[ScriptFunc], ScriptFunc]:
    """Decorator to register a script function."""
    def decorator(func: ScriptFunc) -> ScriptFunc:
        _SCRIPT_REGISTRY[name] = func
        return func
    return decorator

def validate_registry(config: WorkflowConfig) -> None:
    """Check that all script references in config point to registered functions."""
    for state_config in config.states.values():
        for cmd in state_config.commands:
            if isinstance(cmd, ScriptCommand) and cmd.script not in _SCRIPT_REGISTRY:
                raise ConfigError(f"Config references unknown script: {cmd.script!r}")
```

## Shell command execution

A helper that respects dry-run mode:

```python
def run_command(args: list[str], *, dry_run: bool = False, **kwargs: Any) -> subprocess.CompletedProcess | None:
    """Run a shell command, or print it if dry_run is True."""
    if dry_run:
        console.print(f"[dim]would run:[/dim] {shlex.join(args)}")
        return None
    return subprocess.run(args, check=True, **kwargs)
```

## Prompt command dispatch

Prompt commands defined in the YAML config are exposed as Click commands dynamically at CLI startup.
After loading config, one Click command is registered for each distinct `PromptCommand` name
found across all states (duplicates with the same name are registered once):

```python
def register_prompt_commands(cli: click.Group, config: WorkflowConfig) -> None:
    """Dynamically register click commands for all prompt commands in config.

    Commands with the same name appearing in multiple states share a single CLI entry point;
    the correct prompt template is selected at dispatch time based on the resolved plan's state.
    Multi-word command names (e.g. 'full review') are exposed as kebab-case ('full-review').
    """
```

Each registered prompt command accepts:
- An optional `plan_path` positional argument (omitted for state-less commands like `full-review`)
- An `--edit` flag (no argument) to open the rendered prompt in `$EDITOR` before sending

Dispatch sequence for a prompt command:
1. Resolve `plan_path` to a canonical `Plan` via `resolve_plan` (plan 003)
2. Look up the plan's current state in config; retrieve the matching `PromptCommand`
3. Run mandatory pre-steps: `git pull` in the agent's host-side repo
4. Render the Jinja2 prompt template with `{path_to_plan}` (canonical path string)
5. If `--edit`: write the rendered prompt to a temporary file and open it with `$EDITOR`
   (falling back to `vi` if `$EDITOR` is unset); wait for the editor to exit;
   read back the file contents. If the file is empty, print a cancellation message and exit.
   Otherwise use the edited contents as the final prompt.
6. Call `send_prompt_to_agent` with the final prompt (see below)
7. Write an audit log entry

## Tmux dispatch

```python
def send_prompt_to_agent(
    agent_name: str,
    prompt: str,
    config: WorkflowConfig,
    *,
    dry_run: bool = False,
) -> None:
    """Send a rendered prompt to the named agent's tmux window.

    Uses config.tmux.session_name to locate the tmux session.
    Finds the window whose name matches agent_name.
    Waits config.tmux.prompt_send_delay seconds before calling send_keys,
    giving the human a moment to abort if the wrong window is focused.

    In dry-run mode, prints the session, window, and rendered prompt instead of sending.
    """
```

Raises a clear error (not an unhandled exception) if the tmux session or the agent window
cannot be found, so the human can start the session with `workflow start` first.

## Audit logging

### Writing

```python
def write_audit_log(log_dir: Path, entry: AuditLogEntry) -> None:
    """Append an entry to the JSONL audit log."""
    log_path = log_dir / "workflow.log.jsonl"
    with open(log_path, "a") as f:
        f.write(entry.model_dump_json() + "\n")
```

### Reading

```python
def read_audit_log(log_path: Path) -> list[AuditLogEntry]:
    """Read all entries from a JSONL audit log file."""

def read_all_audit_logs(project_dir: Path, agents: list[str]) -> list[AuditLogEntry]:
    """Read and merge audit logs from all agents and master, sorted by timestamp."""
```

## File organization

- `workflow/cli.py` — Click CLI group, global options, calls `register_prompt_commands` at startup (replaces current stub)
- `workflow/registry.py` — script registry, validate_registry
- `workflow/shell.py` — run_command helper
- `workflow/tmux_dispatch.py` — `send_prompt_to_agent`, `register_prompt_commands`
- `workflow/audit.py` — audit log read/write

## New dependency

- Add `libtmux` to project dependencies (used for tmux window discovery and send-keys).

## Dependencies

- Plan 002 (models): uses WorkflowConfig, ScriptCommand, PromptCommand, AuditLogEntry
- Plan 002 (config): uses load_config
- Plan 003 (path resolution): prompt dispatch calls resolve_plan
- Plan 005 (git helpers): prompt dispatch calls git_pull as a pre-step

## Risks

- The registry must be populated before validation runs. All script modules must be
  imported before `validate_registry` is called. Use a conventional import in `cli.py`
  that pulls in all command modules.
- `prompt dispatch → git_pull (plan 005) → run_command (plan 004)`: this is a layered
  dependency, not a circular one, as long as `tmux_dispatch.py` imports from `git_helpers.py`
  and not vice versa. Keep dispatch and git helpers in separate modules.
- libtmux raises exceptions if the tmux server is not running or the session/window does not exist.
  Catch these and surface a clear error message directing the human to run `workflow start`.

