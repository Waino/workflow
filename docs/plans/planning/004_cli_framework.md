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

- `workflow/cli.py` — Click CLI group, global options (replaces current stub)
- `workflow/registry.py` — script registry, validate_registry
- `workflow/shell.py` — run_command helper
- `workflow/audit.py` — audit log read/write

## Dependencies

- Plan 002 (models): uses WorkflowConfig, ScriptCommand, AuditLogEntry
- Plan 002 (config): uses load_config

## Risks

- The registry must be populated before validation runs. All script modules must be
  imported before `validate_registry` is called. Use a conventional import in `cli.py`
  that pulls in all command modules.

