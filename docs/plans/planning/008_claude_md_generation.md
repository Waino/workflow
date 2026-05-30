# 008: CLAUDE.md Generation

Input: `intent/001_idea`

## Goal

Generate the workflow orientation sections of CLAUDE.md from the configuration,
so that agents have accurate awareness of the plan state machine and available commands.
Full prompt texts are no longer in CLAUDE.md — they are rendered from config templates
and sent directly to agents via libtmux dispatch (plan 004.2).

## Scope

- `workflow claude.md` command that updates two auto-generated sections of CLAUDE.md:
    1. **Plan state machine**: states, responsible agents, and transitions — agent-friendly format
    2. **Command listing**: commands available per state, with type markers
- Both sections are constructed from config; no prompt text is included
- Static CLAUDE.md content (git branch strategy, coding standards, general orientation)
  lives outside the auto-generated markers and is never touched by this command
- Hash file for `validate` integrity verification (unchanged from original design)

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

Example output (regenerated against the current `docs/default_config.yaml` after the
plan 012 respec; regenerate again whenever the default config changes):

```
## Plan State Machine

**intent** — agent: planner
Plans being explored. Available transitions: plan → planning, subdivide → planning.

**planning** — agent: planner
Plans being drafted. Available transitions: todo → todo.

**todo** — agent: (human operator)
Plans approved for implementation. Available transitions: implement → implementing.

**implementing** — agent: builder
Plans under active implementation. Available transitions: check → checking.

**checking** — agent: reviewer
Implementation complete, review in progress. Available transitions: accept → done, reject → rejected.

**done** — agent: (human operator)
Merged to main. Implementation and review complete. No transitions.

**rejected** — agent: (human operator)
Will not be implemented. No transitions.
```

### Command listing section

Generated from `config.states`. For each state, list commands with type markers:
- `-` for `script` commands (run by human operator via `workflow <name>`)
- `@` for `prompt` commands (sent to agent by CLI via libtmux; human runs `workflow <name>`)
- `( ... )` for `manual` steps (informational, no CLI command): each is performed
  free-form by the human, with or without chat interaction. Included so agents are
  aware they exist and what they imply for workflow flow.

Example output (regenerated against the current `docs/default_config.yaml`):

```
## Workflow Commands

**intent** (planner)
  - new          Create a new intent file
  - push         Push pending content to master
  @ ask          Read the plan; ask clarifying questions
  ( answer )     Human answers questions in chat
  @ critique     Critique the plan
  ( revise )     Human revises the plan
  @ plan         Write a detailed plan from an intent  (→ planning)
  @ subdivide    Break into planning documents  (→ planning)

**planning** (planner)
  ( adjust )     Human edits the plan directly
  @ critique     Critique the plan
  ( revise )     Human revises the plan
  - todo         Approve for implementation  (→ todo)

**todo** (human operator)
  - implement    Start implementation  (→ implementing)
  - push         Push pending content to master

**implementing** (builder)
  @ ask          Read the plan; ask clarifying questions
  ( answer )     Human answers questions in chat
  @ build        Implement the plan
  - check        Run verification checks and hand off  (→ checking)

**checking** (reviewer)
  ( review )     Human reviews the proposed change
  @ pr           Write a thorough code review
  ( adjust )     Human edits the review or code directly
  @ fix          Fix issues from the most recent review
  - validate     Run verification checks
  - accept       Accept and merge to main  (→ done)
  - reject       Reject and close out  (→ done)
```

## Fields added to config.yaml

### Field added to `StateConfig`

- `description`: agent/human readable description of the state.

### Field added to `WorkflowStep`

- `description`: agent/human readable description of the command.

## Command

```python
@cli.command("claude.md")
@click.option("--output", type=click.Path(), help="Output file path. Default: master/CLAUDE.md")
@click.pass_context
def generate_claude_md(ctx: click.Context, output: str | None) -> None:
    """Regenerate the auto-generated orientation sections of CLAUDE.md."""
```

In addition to the `claude.md` command, the CLAUDE.md file is also generated as part of `workflow init`.
Keep the implementation DRY: both commands should use the same implementing function.

## Hash file

After generating, compute a SHA-256 hash of the full CLAUDE.md and write it to
`{project_dir}/.claude_md_hash` (next to the `.workflow_root` marker, *not* inside
`master/`). A single hash file covers CLAUDE.md across all repos in the project — they
are kept in sync via `transfer_branch_to_main`, so one trusted hash is sufficient and
matches what `_check_claude_md` in `workflow/validate.py` already reads. The hash file
is used by `validate` (plan 005) to detect agent tampering.

## File organization

- `workflow/commands/claude_md.py` — the generate command and helper functions
- `workflow/templates/claude_md_states.md.j2` — jinja2 template for the state machine section
- `workflow/templates/claude_md_commands.md.j2` — jinja2 template for the command listing section
- Generation logic is implemented as pure functions that take `WorkflowConfig` and return `str`,
  making them straightforward to unit-test without touching the filesystem. The same pure
  functions should be reusable from `workflow status` (next-step display, plan 006) so the
  marker conventions (`-` / `@` / `( ... )` / `→`) do not drift between the cheat sheet,
  CLAUDE.md, and the status output. Where possible, route step rendering through
  `WorkflowStep.render()` rather than duplicating marker logic.

## Dependencies

- Plan 002 (models): WorkflowConfig, PromptCommand, ScriptCommand, ManualStep
- Plan 004.1 (CLI and registry): cli group
- jinja2 (already a project dependency, used by plan 004.2 prompt dispatch)

## Risks

- The generated content must be clear enough for agents to understand the workflow without
  seeing full prompt text. Iterative refinement will be needed based on agent behavior.
- The marker-based replacement is simple but fragile if someone manually edits around the markers.
  Accept this as a known limitation.
- Two separate marker pairs add a small risk of one being deleted accidentally.
  The command should warn (but not fail) if a marker pair is missing and it falls back to appending.

