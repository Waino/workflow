# 006: Informational Commands

Input: `intent/001_idea`

## Goal

Implement the commands that provide information and create new plans,
without performing git workflow transitions: `new`, `status`, `log`, and `cheat`.

## Scope

- `workflow new {slug}` — create a new intent file
- `workflow status` — show current state of all active plans
- `workflow log` — pretty-print audit logs with filtering
- `workflow cheat` — print the workflow cheat sheet

## workflow new

```python
@cli.command()
@click.argument("slug")
@click.pass_context
def new(ctx: click.Context, slug: str) -> None:
    """Create a new intent file with the next free sequence number."""
```

### Finding the next free sequence number

- Scan all `docs/plans/` subdirectories across all states for existing sequence numbers.
- Also check the audit log for `output_plans` entries (to account for plans on branches
  not yet merged to main).
- Find the lowest unused integer ≥ 2 (001 is reserved for the global idea).
- Zero-pad to 3 digits.

### Opening the file

- Render `docs/plans/intent/{num}_{slug}.md` from a jinja2 template (no string concatenation or long literals).
- The template lives in the package as a resource (e.g. `workflow/templates/intent.md.j2`).
- Open the created file using `$EDITOR` (fall back to `vi` if unset).
- Stage the created file. Don't commit.

## workflow status

```python
@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show the current state of all active plans."""
```

### Output

- Use `rich.table.Table` for formatted output.
- Columns: sequence number, name, current state, next step(s).
- Omit plans in `done` and `rejected` states (unless a `--all` flag is passed).
- Next step(s) are derived from the state config: list available commands for the plan's current state.
- Footer: print the lowest free sequence number for plans.

## workflow log

```python
@cli.command()
@click.option("--agent", help="Filter by agent.")
@click.option("--plan", help="Filter by plan name (loose match).")
@click.option("--command", "cmd_filter", help="Filter by command name.")
@click.option("--since", help="Show entries after this timestamp (ISO 8601).")
@click.option("--until", help="Show entries before this timestamp (ISO 8601).")
@click.pass_context
def log(ctx: click.Context, agent: str | None, plan: str | None,
        cmd_filter: str | None, since: str | None, until: str | None) -> None:
    """Pretty-print the audit log with optional filters."""
```

### Output

- Use `rich.table.Table` or `rich.console.Console` for formatted output.
- Columns: timestamp, actor, command, input plan, output plans.
- Read the single audit log file, sorted chronologically.
- Apply filters before display.

## workflow cheat

```python
@cli.command()
def cheat() -> None:
    """Print the workflow cheat sheet."""
```

### Output

- Construct the cheat sheet table dynamically from the command definitions in the config.
- Each state's commands are listed in the appropriate column, with markers based on `command_type`:
    - `-` for `script` commands
    - `@` for `prompt` commands
    - `( ... )` for `manual` steps
    - `>` for transitions (commands with a `transition` field)
- Use rich formatting with color to distinguish markers. Fade table delimiters to gray.
- The exact layout and construction logic will be refined in a later iteration of this plan.

## File organization

- `workflow/commands/info.py` — new, status, log, cheat commands
- `workflow/templates/intent.md.j2` — jinja2 template for new intent files

## New dependency

- Add `jinja2` to project dependencies (used for templating in `new` and potentially elsewhere).

## Dependencies

- Plan 002 (models): Plan, PlanState, WorkflowConfig, AuditLogEntry, ManualStep
- Plan 003 (path resolution): find_all_plans, parse_plan_filename
- Plan 004.1 (CLI and registry): cli group
- Plan 004.3 (audit logging): read_audit_log

## Risks

- The `new` command's free-number calculation needs to be robust against concurrent plan creation.
  Since only one human operates the tool, this is low risk, but the audit log check adds safety.

