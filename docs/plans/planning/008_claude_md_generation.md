# 008: CLAUDE.md Generation

Input: `intent/001_idea`

## Goal

Generate the workflow orientation sections of CLAUDE.md from the configuration,
so that agents have accurate awareness of the plan state machine and available commands.
Full prompt texts are no longer in CLAUDE.md — they are rendered from config templates
and sent directly to agents via libtmux dispatch (plan 004).

## Scope

- `workflow claude.md` command that updates two auto-generated sections of CLAUDE.md:
    1. **Plan state machine**: states, responsible agents, and transitions — agent-friendly format
    2. **Command listing**: commands available per state, with type markers
- Both sections are constructed from config; no prompt text is included
- Static CLAUDE.md content (git branch strategy, coding standards, general orientation)
  lives outside the auto-generated markers and is never touched by this command
- Hash file for doublecheck integrity verification (unchanged from original design)

## Generated sections

### Section markers

Two independent marked sections within CLAUDE.md:

```markdown
<!-- WORKFLOW STATES BEGIN -->
... generated plan state machine content ...
<!-- WORKFLOW STATES END -->

<!-- WORKFLOW COMMANDS BEGIN -->
... generated command listing content ...
<!-- WORKFLOW COMMANDS END -->
```

Each section is replaced independently when the command runs.
If a marker pair is absent, that section is appended at the end of the file.

### Plan state machine section

Generated from `config.states`. For each state, rendered as agent-friendly prose:
- State name
- Responsible agent (or "human operator" if `agent: null`)
- Which commands trigger state transitions, and to which target state

Example output:

```
## Plan State Machine

**intent** — agent: planner
Plans being explored. Available transitions: subdivide → planning, plan → planning.

**planning** — agent: planner
Plans being drafted. Available transitions: todo → todo.

**todo** — agent: (human operator)
Plans approved for implementation. Available transitions: implement → implementing.

**implementing** — agent: builder
Plans under active implementation. Available transitions: build → checking (performed by agent).

**checking** — agent: reviewer
Implementation complete, review in progress. Available transitions: accept → done.
```

### Command listing section

Generated from `config.states`. For each state, list commands with type markers:
- `-` for `script` commands (run by human operator via `workflow <name>`)
- `@` for `prompt` commands (sent to agent by CLI via libtmux; human runs `workflow <name>`)
- `( ... )` for `manual` steps (informational; no CLI command): omitted from CLAUDE.md

Example output:

```
## Workflow Commands

**intent** (planner)
  @ ask          Read the plan; ask clarifying questions
  @ critique     Critique the plan
  @ subdivide    Break into planning documents  (→ planning)

**planning** (planner)
  @ ask          Read the plan; ask clarifying questions
  @ critique     Critique the plan
  @ plan         Write a detailed plan from an intent  (→ planning)
  - doublecheck  Run verification checks
  - todo         Approve for implementation  (→ todo)

**implementing** (builder)
  @ ask          Read the plan; ask clarifying questions
  @ build        Implement the plan  (→ checking, performed by agent)
  - push         Push branch after verification

**checking** (reviewer)
  @ pr           Code review of the feature branch
  @ fix          Fix issues from the most recent review
  - doublecheck  Run verification checks
  - accept       Accept and merge to main  (→ done)
```

## Command

```python
@cli.command("claude.md")
@click.option("--output", type=click.Path(), help="Output file path. Default: master/CLAUDE.md")
@click.pass_context
def generate_claude_md(ctx: click.Context, output: str | None) -> None:
    """Regenerate the auto-generated orientation sections of CLAUDE.md."""
```

## Hash file

After generating, compute a SHA-256 hash of the full CLAUDE.md and write it to `.claude_md_hash`
in the same directory. This file is committed alongside CLAUDE.md and used by doublecheck (plan 005)
to detect agent tampering.

## File organization

- `workflow/commands/claude_md.py` — the generate command
- `workflow/templates/claude_md_states.md.j2` — jinja2 template for the state machine section
- `workflow/templates/claude_md_commands.md.j2` — jinja2 template for the command listing section
- Generation logic is implemented as pure functions that take `WorkflowConfig` and return `str`,
  making them straightforward to unit-test without touching the filesystem.

## Dependencies

- Plan 002 (models): WorkflowConfig, PromptCommand, ScriptCommand, ManualStep
- Plan 004 (CLI framework): cli group
- jinja2 (added as dependency in plan 006)

## Risks

- The generated content must be clear enough for agents to understand the workflow without
  seeing full prompt text. Iterative refinement will be needed based on agent behavior.
- The marker-based replacement is simple but fragile if someone manually edits around the markers.
  Accept this as a known limitation.
- Two separate marker pairs add a small risk of one being deleted accidentally.
  The command should warn (but not fail) if a marker pair is missing and it falls back to appending.

