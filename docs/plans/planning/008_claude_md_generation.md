# 008: CLAUDE.md Generation

Input: `intent/001_idea`

## Goal

Generate the workflow shorthand section of CLAUDE.md from the configuration,
so that agents in the sandbox understand the prompt commands.

## Scope

- `workflow claude.md` command that generates a CLAUDE.md section
- Output contains the prompt command definitions from config
- The generated section is appended to or replaces a marked section in the existing CLAUDE.md

## How it works

The YAML config contains prompt definitions for each state. This command extracts them
and renders a human-readable section for CLAUDE.md using a jinja2 template.

### Section markers

Use markers to identify the generated section within CLAUDE.md:

```markdown
<!-- WORKFLOW SHORTHAND BEGIN -->
... generated content ...
<!-- WORKFLOW SHORTHAND END -->
```

If the markers exist, replace the content between them.
If they don't exist, append the section at the end.

### Hash file

After generating CLAUDE.md, compute a hash of the file and write it to `.claude_md_hash`
(in the repo root, alongside CLAUDE.md). This hash file is committed alongside CLAUDE.md
and used by the doublecheck integrity verification (plan 005) to detect tampering.

## Command

```python
@cli.command("claude.md")
@click.option("--output", type=click.Path(), help="Output file path. Default: master/CLAUDE.md")
@click.pass_context
def generate_claude_md(ctx: click.Context, output: str | None) -> None:
    """Generate the workflow shorthand section of CLAUDE.md."""
```

## File organization

- `workflow/commands/claude_md.py` — the generate command
- `workflow/templates/claude_md.md.j2` — jinja2 template for the shorthand section
- Generation logic is a pure function that takes config and returns a string,
  making it easy to test.

## Dependencies

- Plan 002 (models): WorkflowConfig, PromptCommand
- Plan 004 (CLI framework): cli group
- jinja2 (added as dependency in plan 006)

## Risks

- The generated CLAUDE.md content needs to be clear enough for the agent to understand.
  This will require iterative refinement based on how well agents follow the instructions.
- The marker-based replacement is simple but fragile if someone manually edits around the markers.
  Accept this as a known limitation.

