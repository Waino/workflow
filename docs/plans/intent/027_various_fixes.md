# 027: Various Fixes

## 1) Bug: Explicit agent not respected

I ran the command `workflow rebase builder/docs/plans/implementing/023.1`.
The command was sent to `planner`, even though `builder` is expliclity named in the command.
This should never happen: an explitictly named agent must always be respected.

The `builder` agent doesn't have `rebase`, as typically it is either `planner` or `reviewer` that needs rebasing.
However, the decision to allow prompt commands to be sent to any agent by
explicitly naming the agent exists so that prompt commands don't need to be
defensively added to all agents for the rare cases where they are needed.

### 1.1) Make unexpected recipients easier to spot

Whenever sending a command blind (not the active tmux pane), there should be a loud warning
"would send to 'planner' [red](Not the active agent in tmux!)[/red]:"

### 1.2) Rebase specifically

`rebase` should be usable in all agents, preferably without needing to specify the agent explicitly.
Maybe `rebase` should be copied to `builder` as well.

### 1.3) Decisions

- Root cause: `_resolve_plan_target` (`workflow/tmux_dispatch.py`) resolves the plan to `builder/...`,
  finds no `rebase` target for the builder worktree, and falls back to the first target (planner).
  The `{{ branch }}`/git metadata is then also taken from the planner worktree.
- The silent fallback to the first target in `_resolve_plan_target` is removed.
- Recipient rule: the prompt goes to the agent owning the worktree the plan resolved to.
  The definition is picked in this order: the plan's own state; another state owned by the same agent;
  otherwise the command is *borrowed* from another agent.
  Git metadata (`{{ branch }}` etc.) comes from the actual target worktree.
  A plan resolving to `canon` (no agent) is an error.
- Borrowing requires `--edit`: going outside the defined workflow, the user should adapt the prompt.
  Without `--edit`, fail with an error listing the owning states/agents and pointing at `--edit`.
  Borrowing a prompt with a `transition` (`plan`, `subdivide`) is refused even with `--edit`:
  its audit entry would map the plan to the wrong worktree/state in the resolver's audit source.
- `also_valid_in_states` is not reused for borrowing (borrowing is ad-hoc, not declared in config),
  nor for 1.2: a plain second definition keeps cheatsheet, AGENTS.md, resolver and audit mapping untouched.
- Every dispatch prints the resolved plan path and the recipient, not only on blind sends.
  Consequence: an unprefixed `rebase 023.1` may also go to builder if the active tmux window steers resolution there;
  acceptable given the warning below.
- Blind-send check: compare the target with the active window of the tmux session
  (reuse `_active_tmux_repo` from `workflow/plan_resolver.py`).
  The warning is printed on both dry-run and real sends. No confirmation dialog:
  a blind real send instead waits an extra `tmux.prompt_send_delay`, giving time to Ctrl-C.
- A blind send does not rotate the control pane (`go`). Fix the `send_prompt_to_agent` docstring,
  which claims the window is selected.
- `rebase` is added to the `implementing` state (builder) in `docs/default_config.yaml`,
  using a YAML anchor on the prompt text so it is defined once. No migration step for existing project configs.
- `doctor` reports audit entries that are impossible under the current config:
  unknown commands, and transition prompts logged by an agent that doesn't own them.
- Out of scope: whether `resolve_plan` always respects explicitly specified fields of the fuzzy input
  (separate intent).

## 2) Plan paths in prompts

Prompts unnecessarily include the agent in the plan path, e.g. "Read the plan at builder/docs/plans/implementing/022_user_docs.md."
The path is not relative to the agent work dir.
This doesn't appear to cause any confusion, but it's better to fix it.

Decision: add a `path_in_worktree` helper on `Plan` (next to `absolute_path`) that strips the worktree prefix.
Prompts use `{{ plan.path_in_worktree }}`; update `docs/default_config.yaml` accordingly.
Stack entries rendered in prompts (`StackEntry.path`) follow the same convention.

## 3) Improvement: Next step in status

The next step column in `workflow status` is almost always empty.
It is quite hard to reason about the next step afterwards, based only on what is currently stored in the audit log.
Let's avoid the need to reason:
- each command showing a next step should record the text that was displayed in the audit log (a new next_step field)
- `status` can reshow the exact same text

Remove any now obsolete functions introduced only to support the old way of deducing next steps.

Decisions:
- `next_step` holds the plain text exactly as displayed (e.g. `workflow todo 027_foo`).
  Nothing may ever parse it.
- `status` displays it with the `workflow ` prefix and the plan name stripped, to keep the column narrow.
- Prompt dispatch also prints its next step (from the command's reminder) and records it in the audit log.
- `validate` writes an audit entry, only on success.
  Its next step comes from a configured `reminder` (free text such as `accept or reject`),
  replacing the hardcoded `_print_validate_next_step`.
- Reminders are explicit: every command with a sensible next step gets a `reminder` in the default config,
  and the implicit fallback ("first command of the transition target state") is removed.
- Entries without `next_step` (old entries, commands without a reminder): `status` shows nothing, no fallback.

## 4) Bug: AGENTS.md dryrun

`workflow agents_md` does not respect `--dry-run`.

## 5) Resume plan

The resume plan is printed twice in `--dry-run`. Once is enough.

Cause: `ResumePlan.abort_unless_continue` (`workflow/steps.py`) re-renders after callers already rendered.
The plan may be printed on real runs too, but never more than once per command.

### 5.1) Improved plan naming

The naming of the plan is confusing when not resuming.
Let's have `--dry-run` print "Execution plan" if not resuming and "Resume plan" if resuming.

Decision: "resuming" means at least one step is already satisfied or needs `--continue`.

### 5.2) Improved command listing

`--dry-run` doesn't say which worktree is targetted (e.g says just "would run: git pull").
Add the worktree, e.g. "would run in reviewer: git pull".

Decision: print `cwd` relative to the current directory, falling back to the absolute path;
run from the project directory in oob mode this yields e.g. "would run in reviewer/docs: git pull".

## 6) Bug: Incorrect stack anomalies

Stack anomalies are incorrectly shown for plans that have been accepted.

```
Stack anomalies:
    - 023.2_layout_and_vocabulary: onto='023.1_project_metadata_file' does not resolve to a plan
    - 023.3_bare_remote_rename: onto='023.2_layout_and_vocabulary' does not resolve to a plan
    - 023.4_init_oob_and_bootstrap: onto='023.3_bare_remote_rename' does not resolve to a plan
```

There are no stale plans in non-done states:
```
find -iname "023.2_layout_and_vocabulary*"
./planner/docs/plans/done/023.2_layout_and_vocabulary.md
./builder/docs/plans/done/023.2_layout_and_vocabulary.md
./reviewer/docs/plans/done/023.2_layout_and_vocabulary.md
./canon/docs/plans/done/023.2_layout_and_vocabulary.md
```

`workflow status --all` also shows the plans as `done`

Cause (replicated with the project's audit log): `_name_matches_seq` in `workflow/plan_resolver.py` uses
`Path(name).stem`, which truncates `023.1_project_metadata_file` to `023`. The audit source is therefore silent
for every subdivided plan, so the done copies in several worktrees make `resolve_plan` ambiguous.
Non-subdivided stacks (the existing test) are unaffected, which is why the test passed.
`_top_level_number_from_name` has the same `.stem` bug.

Decision: fix both name parsers, and have `_stack_anomalies` (`workflow/commands/doctor.py`) look up `onto`
with `find_stack_member` (`workflow/stack.py`): `onto` is an exact name, fuzzy resolution is the wrong tool.
Add a regression test with dotted sequence numbers.


## 7) Trim stack column

`workflow status` easily becomes too wide due to `stack` column:
let's only show the sequence number in `stack`, omitting the slug.

Decision: look up the base with `find_stack_member` and show its sequence number;
fall back to the raw `onto` value if it does not resolve.

## 8) Test refactoring

Check for repetitive tests that could be made DRY using `pytest.mark.parametrize`.

Decision: conservative, separate sub-plan. Only the worst offenders, chosen during planning.
No coverage drop, no assertion dropped without a parametrized equivalent.

## 9) Additions from critique

- `Plan.worktree` property (raises on a path without worktree prefix) next to `path_in_worktree`;
  replaces the hand-rolled `path.parts[0]` parsing.
- A dry-run safety test: every registered script command run with `--dry-run` leaves files and git refs unchanged.

## Sequencing

Decision: three sub-plans.
- 027.1: prompt dispatch (items 1, 1.1, 1.2, 2; `Plan.worktree`; doctor audit check)
- 027.2: small fixes (items 3, 4, 5, 6, 7; dry-run safety test)
- 027.3: conservative test parametrization (item 8)
