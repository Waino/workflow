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

Status: a working implementation already exists in `workflow/commands/workflow.py`
(numbering, audit-log scan, `$EDITOR` invocation). The remaining work is the
template extraction and the `git add` step described below.

### Finding the next free sequence number

- Scan all `docs/plans/` subdirectories across all states for existing sequence numbers.
- Also check the audit log for `output_plans` entries (to account for plans on branches
  not yet merged to main).
- Find the lowest unused integer ≥ 2 (001 is reserved for the global idea).
- Zero-pad to 3 digits.

### Opening the file

- Render `docs/plans/intent/{num}_{slug}.md` from a jinja2 template at
  `workflow/templates/intent.md.j2`, packaged as a resource and loaded via
  `importlib.resources`. Replace the existing inline f-string body.
- The starter template is intentionally bare-bones (title + Goal placeholder),
  matching what the inline string currently produces. It will be fleshed out
  in a future iteration as conventions for new intents settle.
- Open the created file using `$EDITOR` (fall back to `vi` if unset).
- Run `git add` on the created file in the `canon` repo so it shows up in
  `git status` for the human. Do not commit.

## workflow status

```python
@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show the current state of all active plans."""
```

### Output

- Use `rich.table.Table` for formatted output.
- Columns: sequence number, name, current state, last logged action, next step.
- Omit plans in `done` and `rejected` states (unless a `--all` flag is passed).
- Next step(s) should be derived from the audit log and the state config.
  First determine the last logged action for the plan, using the audit log.
  Then, use the same logic as in the existing _print_command_reminder to determine the next command.
- Refactor to unify the two uses, to keep it DRY.
- If no logged action for the plan exists, leave both last logged action and next step empty.
- Render the next step using `WorkflowStep.render()` to get marker and color consistent with the cheatsheet table.
- Render each step via `WorkflowStep.render()` (the same path the cheat
  sheet uses) so markers and colors stay consistent with `workflow cheat`.
- Footer: print the lowest free sequence number for plans by calling
  `_next_intent_number` (already extracted in `workflow/commands/workflow.py`); do not
  reimplement the scan.

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
- For `--plan`, route the value through `resolve_plan` (added in plan 012) so fuzzy
  paths and partial names match the same way they do for every other command. If
  `resolve_plan` cannot uniquely identify a plan, fall back to a case-insensitive
  substring match against `input_plan` and `output_plans` rather than erroring — the
  command is read-only and a wider match is more useful than a hard failure.

## workflow cheat

Status: **done**. Implemented in `workflow/commands/cheat.py`. The table is built
dynamically from `config.states` and each step renders itself via
`WorkflowStep.render()` (script/prompt/manual subclasses each contribute their own
marker and color). No further work is planned under this plan; future tweaks to
layout or coloring should be tracked separately.

## File organization

- `workflow/commands/workflow.py` — `new` (alongside the other state-transition commands)
- `workflow/commands/info.py` — `status`, `log`
- `workflow/commands/cheat.py` — `cheat` (already done)
- `workflow/templates/intent.md.j2` — jinja2 template for new intent files

## Dependencies

- Plan 002 (models): Plan, PlanState, WorkflowConfig, AuditLogEntry, ManualStep
- Plan 003 (path resolution): find_all_plans, parse_plan_filename
- Plan 004.1 (CLI and registry): cli group
- Plan 004.3 (audit logging): read_audit_log

## Risks

- The `new` command's free-number calculation needs to be robust against concurrent plan creation.
  Since only one human operates the tool, this is low risk, but the audit log check adds safety.

