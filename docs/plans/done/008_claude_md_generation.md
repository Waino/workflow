# 008: AGENTS.md Generation

Input: `intent/001_idea`

## Goal

Generate the workflow orientation section of AGENTS.md from configuration so
that agents have accurate, up-to-date awareness of the plan state machine and
available commands before they act. Full prompt texts live in config and are
sent directly to agents via libtmux dispatch (plan 004.2); AGENTS.md is not
the source of prompts.

## Scope

- One `workflow agents_md` command updates a single auto-generated section of
  `master/AGENTS.md`. It writes the file and prints a reminder; it does
  not stage, commit, or push. The operator reviews with `git diff` and
  commits when satisfied.
- The same generation function is called by `workflow init` to seed
  AGENTS.md *before* the bare clone, so all agent clones inherit it.
  Init does auto-commit (one-time bootstrap).
- Static AGENTS.md content (git branch strategy, coding standards, general
  orientation) lives outside the marker pair and is never touched.
- A hash file (`.agents_md_hash`) records the AGENTS.md sha and the
  config.yaml sha for `validate` integrity verification (plan 005) and for
  idempotency checks in the command itself.

## Audience and tone

AGENTS.md is read by agents, not humans, so the generated content is
deliberately semi-structured (tagged bullet lines per state) rather than
prose or a human-friendly table. The format prioritises unambiguous parsing
by an LLM: every command line carries an explicit `[type]` tag, every
transition is rendered the same way, and per-state blocks share the same
fixed layout.

## Generated section

### Markers

A single marker pair delimits the auto-generated region:

```markdown
<!-- WORKFLOW BEGIN -->
... generated content ...
<!-- WORKFLOW END -->
```

### Marker handling rules

A marker line is recognised only when it appears at column 0 (no leading
whitespace), as the entire content of the line (apart from a trailing
newline). No fenced-code-block parsing is performed: if a user places a
marker-looking string inside a fenced block but still at column 0, it
counts as a marker. This is documented as a constraint; do not work
around it.

| State of markers in the file                            | Behaviour                                          |
|---------------------------------------------------------|----------------------------------------------------|
| 0 markers (BEGIN absent and END absent)                 | Append a fresh marker pair + content at EOF.       |
| Exactly one BEGIN and one END, BEGIN strictly before END| Replace the content between them.                  |
| Any other state (count mismatch, wrong order, dupes)    | Raise `AgentsMdGenerationError`; write nothing.    |

The "0 markers → append" path is intentional: it lets a user author the
static parts of AGENTS.md, run `workflow agents_md` once to drop the
managed block at EOF, and then move the block to the preferred location
with their editor before the next run.

### Static introductory paragraph

The managed section begins with a fixed paragraph baked into the Jinja
template (not a Python constant, not derived from config). It explains
the workflow stance to agents:

> The workflow below is enacted by a human operator. Agents do not invoke
> `workflow` subcommands themselves; they receive rendered prompts in
> their tmux window. This listing exists so an agent knows in advance
> which state a plan is in, which actions are expected in that state,
> and which actions belong to other states. For example, an agent
> reading a plan in the `planning` state must not start writing code; an
> agent in the `implementing` state must not rewrite the plan.
>
> The human operator may deviate from the workflow at any time. A prompt
> command may therefore arrive in a state where it is not listed below
> (for example, `ask` may be sent in any state). When this happens,
> follow the prompt as given.

### Per-state blocks (tagged bullet lines)

For each state in `config.states`, in config-iteration order, emit:

```
### <state> (agent: <agent-name-or-"human operator">)
<description>
Inbound: <command-name> from <source-state> [<type>], ...   (or "(none)")
Outbound: <command-name> -> <target-state> [<type>], ...    (or "(none)")
Commands:
- [<type>] <name>[ -> <transition>] — <description>
- ...
```

`<type>` is one of `script`, `prompt`, `manual`. Manual steps never carry
a transition. The `-> <transition>` clause is only emitted for commands
whose `transition` field is set. When a state has no commands, render
`Commands: (none)` on a single line.

### Example output (regenerate against the current `docs/default_config.yaml`)

```
<!-- WORKFLOW BEGIN -->
[static intro paragraph here]

### intent (agent: planner)
Plans being explored.
Inbound: (none)
Outbound: plan -> planning [prompt], subdivide -> planning [prompt]
Commands:
- [script] new — Create a new intent file.
- [script] push — Push pending content to master.
- [prompt] ask — Read the plan; ask clarifying questions.
- [manual] answer — Human answers questions in chat.
- [prompt] critique — Critique the plan.
- [manual] revise — Human revises the plan.
- [prompt] plan -> planning — Write a detailed plan from an intent.
- [prompt] subdivide -> planning — Break the plan into smaller planning documents.

### planning (agent: planner)
Plans being drafted.
Inbound: plan from intent [prompt], subdivide from intent [prompt]
Outbound: todo -> todo [script]
Commands:
- [manual] adjust — Human edits the plan directly.
- [prompt] critique — Critique the plan.
- [manual] revise — Human revises the plan.
- [script] todo -> todo — Approve for implementation.

### todo (agent: human operator)
Plans approved for implementation.
Inbound: todo from planning [script]
Outbound: implement -> implementing [script]
Commands:
- [script] implement -> implementing — Start implementation.
- [script] push — Push pending content to master.

### implementing (agent: builder)
Plans under active implementation.
Inbound: implement from todo [script]
Outbound: check -> checking [script]
Commands:
- [prompt] ask — Read the plan; ask clarifying questions.
- [manual] answer — Human answers questions in chat.
- [prompt] build — Implement the plan.
- [script] check -> checking — Run verification checks and hand off.

### checking (agent: reviewer)
Implementation complete, review in progress.
Inbound: check from implementing [script]
Outbound: accept -> done [script], reject -> rejected [script]
Commands:
- [manual] review — Human reviews the proposed change.
- [prompt] pr — Write a thorough code review.
- [manual] adjust — Human edits the review or code directly.
- [prompt] fix — Fix issues from the most recent review.
- [script] validate — Run verification checks.
- [script] accept -> done — Accept and merge to main.
- [script] reject -> rejected — Close out without merging.

### done (agent: human operator)
Merged to main. Implementation and review complete.
Inbound: accept from checking [script]
Outbound: (none)
Commands: (none)

### rejected (agent: human operator)
Will not be implemented.
Inbound: reject from checking [script]
Outbound: (none)
Commands: (none)

### review (agent: reviewer)
Codebase-wide review and audit tasks unrelated to a specific plan.
Inbound: (none)
Outbound: (none)
Commands:
- [prompt] full review — Perform a full review of the current codebase.
- [prompt] followthrough — Compare done plans against the codebase for unimplemented features.
- [prompt] refresh — Update planning/todo plans against decisions made since they were written.
<!-- WORKFLOW END -->
```

## Config schema changes

### `StateConfig`

Add **required** field:

- `description: str` — agent-facing description of the state. A model
  validator rejects empty/whitespace-only values.

### `WorkflowStep` (base of `PromptCommand`, `ScriptCommand`, `ManualStep`)

Add **required** field:

- `description: str` — agent-facing description of the command. A model
  validator rejects empty/whitespace-only values.

`docs/default_config.yaml` must be updated to include a non-empty
description for every state and every command before this plan is
merged.

## Output normalisation

The generation function normalises output deterministically:

- UTF-8 encoding, no BOM.
- LF line endings.
- Single trailing newline at end of file.
- No trailing whitespace on any line.
- Stable iteration order: `config.states` order for states; per-state
  command list order for commands.

Two consecutive `workflow agents_md` runs against the same config must
produce a byte-identical file. This is asserted by a unit test.

## Command

```python
@cli.command("agents_md")
@click.option("--check", is_flag=True,
              help="Exit 1 if regeneration would change AGENTS.md or the hash file; never writes.")
@click.pass_context
def agents_md(ctx: click.Context, check: bool) -> None:
    """Regenerate the auto-generated workflow section of AGENTS.md."""
```

Behaviour:

- Resolve `project_dir` from `WorkflowConfig`; error if outside a workflow project.
- Compute the would-be AGENTS.md content and the would-be hash file
  content in memory.
- **Idempotency check**: if the existing AGENTS.md is byte-identical to
  the would-be content **and** the existing `.agents_md_hash` matches
  the would-be hash file, print `AGENTS.md is up to date.` and exit 0
  without writing anything.
- `--check` mode: if not idempotent, print a one-line summary of what
  would change (file content, config hash, or both) and exit 1. Never
  writes. If idempotent, exit 0 silently. The same flag covers CI
  (exit code only matters) and local dev (the one-line summary is
  enough to know what to look for in `git diff`).
- Default mode (writing):
  - Write `master/AGENTS.md` and `{project_dir}/.agents_md_hash`
    atomically (write to temp files, then rename).
  - Print: `AGENTS.md written. Review with 'git diff' in master, then commit and push.`
  - The command does **not** stage, commit, or push. That is left to
    the operator, who reviews the diff and may decide to revert.

The standalone command performs no git operations. This is the
deliberate choice: `git diff` is the right tool for inspecting and
reverting changes to AGENTS.md, and an automatic commit would compete
with that workflow. The one exception is `workflow init` (see
[Integration with `workflow init`](#integration-with-workflow-init)),
which does commit because init runs once at project bootstrap and the
bare clone must already contain AGENTS.md.

## Public function signature

```python
def generate_and_write_agents_md(
    config: WorkflowConfig,
    project_dir: Path,
    *,
    agents_md_path: Path | None = None,
) -> Path:
    """Regenerate AGENTS.md and refresh the trusted hash file.

    Returns the path that was (or would be) written.
    Raises AgentsMdGenerationError when markers are malformed,
    or when generation cannot proceed safely.

    Performs no git operations. The caller (the `agents_md` command or
    `workflow init`) is responsible for any staging/commit/push.
    """
```

Pure helpers (no I/O), tested in isolation:

- `render_workflow_section(config: WorkflowConfig) -> str` — produces
  the managed text (static intro + per-state blocks) by rendering the
  Jinja template.
- `find_managed_section(text: str) -> ManagedSection | None` — scans
  for the marker pair using the column-0 rule. Returns a
  `ManagedSection(begin_line, end_line)` when exactly one well-ordered
  pair is found, `None` when no markers are present, and raises
  `AgentsMdGenerationError` on any malformed marker state.
- `replace_managed_section(text: str, span: ManagedSection, managed: str) -> str` —
  substitutes the content between the marker lines (markers preserved).
- `append_managed_section(text: str, managed: str) -> str` — appends a
  fresh marker pair plus content at EOF, with a single blank-line
  separator from any prior content.
- `normalise(text: str) -> str` — applies the output normalisation rules.
- `compute_hash_file_contents(agents_md_text: str, config_yaml_bytes: bytes) -> str`
  — formats the `v1 ...` line.
- `parse_hash_file(text: str) -> HashFileV1` — returns a pydantic
  `HashFileV1` model with fields `version: Literal["v1"]`,
  `agents_md_sha: str`, `config_yaml_sha: str`. Raises on malformed
  input or wrong version tag.

## Hash file

Path: `{project_dir}/.agents_md_hash` (next to `.workflow_root`, *not*
inside any repo). One hash file covers AGENTS.md across all repos.

### Trust model

`.agents_md_hash` is read and written **only by script commands run by
the human operator** (`workflow agents_md`, `workflow init`,
`workflow validate`). It is not mounted into agent containers and no
prompt command reads or writes it. The file therefore lives somewhere
agents structurally cannot reach, which is what makes it usable as a
tamper signal: an agent that modifies AGENTS.md inside its own clone
has no way to also update the trusted hash.

This trust property must not be eroded:

- Do not surface `.agents_md_hash` in AGENTS.md or any prompt template.
- Do not read or write it from any prompt command.
- Do not copy it into any agent-visible location.

Format (single line, space-separated, single trailing newline):

```
v1 <agents_md_sha256> <config_yaml_sha256>
```

- `v1` is a literal version tag. Future format changes bump this.
- `<agents_md_sha256>` is the SHA-256 of the on-disk AGENTS.md bytes
  after normalisation.
- `<config_yaml_sha256>` is the SHA-256 of the on-disk `config.yaml`
  bytes (raw, no normalisation).

Parsed as a pydantic model:

```python
class HashFileV1(BaseModel):
    version: Literal["v1"]
    agents_md_sha: str
    config_yaml_sha: str
```

### `_check_agents_md` update (in `workflow/validate.py`)

The existing check already reads the first whitespace-separated token of
`.agents_md_hash` as the trusted AGENTS.md sha. Update it to:

- Parse via `parse_hash_file` (returns `HashFileV1`). Fail if the
  version tag is not `v1`.
- Compare `agents_md_sha` to the on-disk file as today.
- When `agents_md_sha` mismatches, additionally compare
  `config_yaml_sha` against the on-disk `config.yaml`. If that also
  mismatches, include this in the failure message:
  "`config.yaml` has changed since AGENTS.md was last generated; run
  `workflow agents_md`."

## Integration with `workflow init`

Init currently does, in order:

1. Assert `master/` is a git repo.
2. `git clone --bare master bare`.
3. Clone bare into each agent.
4. Configure remotes / push settings.
5. `git push -u origin HEAD` from master.
6. Touch `.workflow_root`.

Updated init flow inserts AGENTS.md generation **before the bare clone**
so all agent clones inherit it. Init is a one-time bootstrap and does
auto-commit (unlike the standalone `agents_md` command):

1. Assert `master/` is a git repo.
2. **New**: `generate_and_write_agents_md(config, project_dir)` writes
   `master/AGENTS.md` and `.agents_md_hash`.
3. **New**: in master, `git add AGENTS.md && git commit -m "chore: generate initial AGENTS.md"`
   (skipped if there is nothing to commit, i.e. master already had a
   byte-identical AGENTS.md).
4. `git clone --bare master bare` — the bare clone now contains the
   committed AGENTS.md.
5. Clone bare into each agent.
6. Configure remotes / push settings.
7. `git push -u origin HEAD` from master.
8. Touch `.workflow_root`.

`.agents_md_hash` is written in step 2 and is never committed (it lives
outside any repo).

There is no dirty-tree guard. The operator is expected to use
`git status` / `git diff` to manage the state of master, and to avoid
running `workflow agents_md` while agents are mid-task (see Risks).

## Exceptions

Add a new exception in `workflow/exceptions.py`:

```python
class AgentsMdGenerationError(WorkflowError):
    """Raised when AGENTS.md cannot be safely (re)generated."""
```

Raised on:

- Malformed markers (count mismatch, wrong order, duplicate pairs,
  marker line that is not at column 0).
- Malformed `.agents_md_hash` (consumed by validate).

## File organisation

- `workflow/agents_md.py` — pure generation functions
  (`render_workflow_section`, `find_managed_section`,
  `replace_managed_section`, `append_managed_section`, `normalise`,
  `compute_hash_file_contents`, `parse_hash_file`, `HashFileV1`,
  `ManagedSection`). Lives outside `workflow/commands/` so it can be
  imported from `validate.py` and `commands/workflow.py` (init)
  without circular imports.
- `workflow/commands/agents_md.py` — the `agents_md` Click command and
  the `generate_and_write_agents_md` orchestrator (idempotency check,
  atomic writes, reminder print). No git operations.
- `workflow/templates/agents_md_workflow.md.j2` — single Jinja2
  template producing the managed section (static intro paragraph baked
  into the template + per-state blocks).
- `tests/test_agents_md.py` — unit and CLI tests.

## Tests

Pure-function tests (no filesystem, no subprocess):

- `render_workflow_section` against the default config produces the
  exact example output above (golden test).
- Two consecutive renders of the same config are byte-identical.
- `find_managed_section`:
  - 0 markers → returns `None`.
  - 1 BEGIN + 1 END in order → returns the `ManagedSection` span.
  - 2 BEGIN + 2 END → raises `AgentsMdGenerationError`.
  - 1 BEGIN + 0 END → raises.
  - 0 BEGIN + 1 END → raises.
  - END strictly before BEGIN → raises.
  - Marker token with leading whitespace (e.g. `  <!-- WORKFLOW BEGIN -->`)
    → not counted as a marker (column-0 rule).
- `replace_managed_section` substitutes only the content between the
  marker lines, preserving the markers themselves and the surrounding
  static content byte-for-byte.
- `append_managed_section` appends with a single blank-line separator
  and produces a file ending in exactly one trailing newline after
  `normalise`.
- `normalise`: strips trailing whitespace, normalises CRLF→LF, ensures
  a single trailing LF, preserves UTF-8.
- `compute_hash_file_contents` produces the `v1 <sha> <sha>\n` format.
- `parse_hash_file` accepts well-formed input and returns a
  `HashFileV1`; raises on missing version tag, wrong version tag, and
  wrong token count.

Command tests (CliRunner):

- Idempotent run prints `AGENTS.md is up to date.` and exits 0; no
  writes occur.
- `--check` exits 1 with a one-line "would change" summary when
  AGENTS.md is stale; exits 0 silently when current. Never writes.
- Stale AGENTS.md → writes the file and `.agents_md_hash`, prints
  `AGENTS.md written. Review with 'git diff' in master, then commit and push.`,
  exits 0. No git commands are invoked.
- Missing or empty `description` field anywhere in the config → config
  load raises a Pydantic validation error.

Init tests:

- `workflow init` invokes `generate_and_write_agents_md` and commits
  AGENTS.md **before** the `git clone --bare` call (asserted via
  call-order on a mocked `run_command`).
- Init's AGENTS.md commit is skipped when `master/AGENTS.md` is already
  byte-identical to the generated content.

Validate tests:

- `.agents_md_hash` with a non-`v1` version tag → `_check_agents_md`
  fails with a clear message.
- AGENTS.md sha mismatch + config sha mismatch → failure message
  mentions config has changed since last generation.
- AGENTS.md sha mismatch + config sha match → failure message does not
  mention config drift.

## Dependencies

- Plan 002 (models): `WorkflowConfig`, `PromptCommand`, `ScriptCommand`, `ManualStep`.
- Plan 004.1 (CLI and registry): cli group.
- Plan 005 (`validate`): `_check_agents_md` integration.
- Plan 007 (`workflow init`): insertion point for the generation step.
- jinja2 (already a project dependency).

## Risks and accepted limitations

- The marker matrix is strict by design. Users who hand-edit the
  marker lines will get an explicit error rather than silent
  corruption. Markers must be at column 0, on their own line, with no
  surrounding fence on the same line; the parser does no markdown
  fence analysis.
- The "append on 0 markers" path is the only way the command will add
  content outside an existing managed block. It is intentional: it
  lets a user bootstrap AGENTS.md without first hand-writing markers.
- `workflow agents_md` and `config.yaml` edits must not run
  concurrently with active agent work. AGENTS.md and config define the
  contract the agent is acting under; modifying them mid-task
  invalidates that contract. The system does not enforce this — the
  operator is expected to schedule regenerations between agent tasks.
  Agent clones receive AGENTS.md updates via the `git pull` that every
  prompt command already runs in the target repo.
- `repo_override`, `pull_target`, `also_valid_in_states`, and
  `reminder` are not surfaced in AGENTS.md. They are runtime behaviour
  details that do not change what an agent should/should not do at a
  given state. If agent confusion emerges in practice, revisit.
- The generated content's clarity for agents is empirically
  determined; the format may need iteration based on observed agent
  behaviour.
- `.agents_md_hash` lives outside any git repo. If the file is lost
  (machine wipe, accidental delete), rerunning `workflow agents_md`
  regenerates it. There is no backup story; this is acceptable because
  the hash file is a derived artefact.
