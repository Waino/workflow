# 012: Plan resolution respec — implementation

Input: `intent/012_plan_resolution_respec`

## Goal

Implement the design in `intent/012`. Three independent but related changes:

1. Rename `doublecheck` → `validate` throughout the package and the default config.
2. Rename the builder→reviewer handoff command from `push` to `check`, and introduce a
   new generic `push` content-push command that does not transition state.
3. Replace the single-repo `resolve_plan` and the per-repo loops in
   `workflow/commands/workflow.py` and `workflow/tmux_dispatch.py` with a single merged
   resolver that combines constraints from CLI, git history, audit log, and tmux state.

The three changes are staged as three phases, committed separately. Each phase leaves
the package green under black, ruff, pytest, and mypy.

## Scope

- Phase 1: Mechanical rename `doublecheck` → `validate`.
- Phase 2: `push`/`check` split. New `push` with `repo_override` config field.
- Phase 3: New merged resolver and callers.

Out of scope (future work, per intent §Future work):
- Recording the planner branch name on `plan` / `subdivide` audit log entries.
- Introducing a type-level `Ticket` class distinct from `Plan`.
- Moving resolution of reviews into the merged resolver — reviews keep their own
  entry point per intent §Decisions (prefix detection on `reviews/`).

## Phase 1: Rename `doublecheck` → `validate`

Mechanical rename, no behavior change. Done first so later phases can refer to the
new name without ambiguity.

Renames:
- `workflow/doublecheck.py` → `workflow/validate.py` (`git mv`).
- `workflow/doublecheck.py::doublecheck` → `validate`. All imports follow.
  The `as run_doublecheck` alias in `workflow/commands/workflow.py` becomes
  `as run_validate`.
- `@register_script("doublecheck")` in `workflow/commands/workflow.py` → `"validate"`.
  The Click command function `doublecheck` → `validate`. Internal helpers
  `_resolve_doublecheck_plan` / `_state_has_doublecheck` → `_resolve_validate_plan` /
  `_state_has_validate`.
- Default config `docs/default_config.yaml`: the `name: doublecheck` / `script: doublecheck`
  entry under the `checking` state becomes `name: validate` / `script: validate`.
- Tests:
  - `tests/test_doublecheck.py` → `tests/test_validate.py` (`git mv`), function and
    import renames inside.
  - In `tests/test_cli.py` and `tests/test_workflow_commands.py`: all `doublecheck`
    references (function names, CLI invocations, monkeypatched symbol names) rename
    to `validate`. `run_doublecheck` monkeypatches become `run_validate`.
- Internal identifier inside `workflow/validate.py`: the `CheckResult` type name,
  `CHECK_NAMES`, and `infer_target_repo` keep their names.

Pre-existing intent doc already uses `validate` (per the prior terminology sweep).

Commit message: `refactor: rename doublecheck to validate`.

## Phase 2: `push` / `check` split

Current state: `@register_script("push")` in `workflow/commands/workflow.py` performs
the builder→reviewer handoff (pre-commit rerun, validate, `git push` in builder,
`git fetch` in reviewer, transition `implementing` → `checking`).

Target state per intent §Check and §Push:

- `check` (new name, existing logic): strict-state builder→reviewer handoff.
  Renamed `@register_script("check")`. Docstring updated. Audit log entry's
  `command` field becomes `"check"`. Reminder key becomes `"check"` in whatever
  state transitions point at it.
- `push` (new generic command): no state transition. Pushes the current branch of
  a resolved repo to bare, with an optional pull into another repo.

### New `push` command shape

```python
@register_script("push")
@click.argument("plan_path")
@click.pass_context
def push(ctx: click.Context, plan_path: str) -> None:
    """Push manual edits for a plan to bare. No state transition."""
```

- Resolve the plan via the new merged resolver (phase 3). Until phase 3 lands,
  phase 2 uses the existing `resolve_plan` with a soft fallback — see the
  migration note below.
- After resolution, the resolved plan's repo is the source; `git push origin`
  in that repo.
- If the resolved state has a configured `pull_target` (see below), also
  `git pull` in the target repo.
- Write an audit log entry with `command="push"` and `input_plan=plan.name`.

### `repo_override` configuration field

Intent §Worked example 3 refers to "`repo_override: master`" on the generic
`push` command. Model it on `ScriptCommand` as an optional soft constraint:

```python
class ScriptCommand(WorkflowStep):
    ...
    repo_override: RepoName | None = None
```

Semantics (resolver phase 3): when `repo_override` is set on a script command,
it contributes `{repo_override}` to the repo-set constraint. It is a soft
constraint (just another information source that participates in the
intersection), not a hard one.

Default config entries for the new generic `push`:

```yaml
  intent:
    commands:
      - command_type: script
        name: push
        script: push
        repo_override: master
        reminder: ask
  todo:
    commands:
      - command_type: script
        name: push
        script: push
        repo_override: master
```

The existing `push` entry in the `implementing` state is renamed to `check`:

```yaml
  implementing:
    commands:
      ...
      - command_type: script
        name: check
        script: check
        transition: checking
        reminder: review
```

### Migration note (phase 2 without phase 3)

Until phase 3 lands, the generic `push` temporarily resolves with
`resolve_plan(plan_path, project_dir, repo=RepoName.master)` — matching the
`repo_override: master` semantics by hand. Phase 3 replaces this with the
merged resolver and makes `repo_override` a first-class constraint.

Commit message: `feat(workflow): split push into check (handoff) and push (content)`.

## Phase 3: Merged plan resolver

Replaces the current `resolve_plan` (single-repo) and the per-repo loops in
`workflow/commands/workflow.py::_resolve_validate_plan` and
`workflow/tmux_dispatch.py::_resolve_plan_target`.

### Public API

```python
# workflow/plan_resolver.py

def resolve_plan(
    fuzzy_path: str,
    project_dir: Path,
    *,
    config: WorkflowConfig,
    command_name: str | None = None,
) -> Plan: ...
```

- `fuzzy_path`: user-provided path fragment. Absolute paths and full
  `repo/docs/plans/state/name` paths continue to short-circuit.
- `command_name`: name of the CLI subcommand being resolved (`"ask"`, `"push"`,
  `"check"`, etc.). Used to derive the subcommand's constraints from the
  config. `None` means "no subcommand constraint" — legal paths only, used
  internally for tooling that already knows the plan's state/repo.

The old `expected_states` and `repo` parameters are removed. Script commands
with strict state requirements (`todo`, `check`, `accept`, `reject`) validate
the resolved plan's state themselves after resolution and raise
`WorkflowError` on mismatch. This matches intent §Decisions.

### Internal structure

Split the resolver into a pipeline of pure functions, each returning one
information source's constraint (or `None` when the source is silent):

```python
@dataclass(frozen=True)
class _Constraint:
    repos: frozenset[RepoName] | None  # None = silent on repo
    states: frozenset[PlanState] | None  # None = silent on state

def _from_fuzzy_path(fuzzy_path: str) -> tuple[_Constraint, int | None, str | None]: ...
def _from_subcommand(config: WorkflowConfig, command_name: str | None) -> _Constraint: ...
def _from_git_history(project_dir: Path, seq: int, slug: str | None) -> _Constraint: ...
def _from_audit_log(project_dir: Path, seq: int) -> _Constraint: ...
def _from_tmux(config: WorkflowConfig, command_name: str | None) -> _Constraint: ...
```

Intersection rule (per intent §Decisions): intersect `repos` and `states`
independently across all non-silent sources. Silent sources (`None`) are
skipped on their dimension only.

### The fuzzy path parser

- If `fuzzy_path` is absolute: fast path — validate it lives under
  `{project_dir}/{repo}/docs/plans/{state}/` and return. Reviews under
  `reviews/` are rejected here and routed to the review resolver.
- Otherwise, split on `/`, strip `.md`. Extract: repo hint (if parts[0] is a
  `RepoName`), `docs/plans/` noise, state hint (if next part is a `PlanState`),
  sequence number, slug. Reuse the existing `_extract_components` helper,
  renamed to `_extract_hints` and returning a `_Constraint` plus `(seq, slug)`.
- A sequence number is mandatory (per intent §Option 1). Absence raises
  `PlanNotFoundError`.
- If the hints already fix (repo, state, seq) and a unique file matches on
  disk, skip the git/audit/tmux sources entirely (fast path from intent
  §Decisions last bullet).

### The subcommand constraint

For the resolver to know which repos/states a command can act on, it walks
`config.states` looking for `PromptCommand` or `ScriptCommand` entries with
a matching `name`. For each match:
- States where the command appears (plus any `also_valid_in_states`) are added
  to `states`.
- The repo derived from `state_config.agent` (via `_repo_for_prompt_state` for
  prompts, `state_config.agent or RepoName.master` for scripts) goes into
  `repos`.
- If the command is a `ScriptCommand` with `repo_override`, that repo is
  added too (soft constraint).
- Prompt commands contribute a hard constraint that master is not in
  `repos` (prompts can only dispatch to agents).

### The git history source

Implements intent §"Git history as an information source":

```python
def _git_newest_touch(
    repo_path: Path, seq: int, slug: str | None,
) -> tuple[int, PlanState, Path] | None:
    """Run `git log --all --no-merges --max-count=1 --format='%H %ct' --name-status`
    with `:(glob)docs/plans/*/<filename>.md` pathspecs and parse the newest
    commit's path-status line into (committer_timestamp, state, resolved_path).
    Returns None when no commit touched the file in this repo.
    """
```

Runs once per repo (master/planner/builder/reviewer). The repo with the
highest timestamp wins; its parsed state is the git-source state constraint.
Rename handling: if the line starts with `R`, use the destination path
(after the second tab). Same-commit `D old` + `A new` with matching basenames
is treated as a rename (see intent caveat 2).

Filename globs:
- When slug is known: `<seq>_<slug>.md`.
- When slug is unknown: `<seq>_*.md`.

Use `workflow.shell.run_command` to execute git; on non-zero exit (e.g. the
repo is empty), return `None` rather than raising.

### The audit log source

Replay `read_audit_log(project_dir)` for entries mentioning `seq` in either
`input_plan` or `output_plans`. Derive repo/state from the `command`:

| command    | repo     | resulting state |
| ---------- | -------- | --------------- |
| `new`      | master   | intent          |
| `plan`     | planner  | planning        |
| `subdivide`| planner  | planning        |
| `todo`     | master   | todo            |
| `implement`| master   | implementing    |
| `check`    | builder  | checking        |
| `accept`   | master   | done            |
| `reject`   | master   | rejected        |
| `push`     | (skip)   | (skip)          |

The most recent matching entry provides `_Constraint(repos={...}, states={...})`.
If no entries match, the source is silent (`_Constraint(None, None)`).

The mapping table lives next to `write_audit_log` in `workflow/audit.py`
as a module constant; the resolver imports it.

### The tmux source

Read via libtmux: the session is `config.tmux.session_name`; the "active
window" is `session.active_window` (already a libtmux concept). If the
active window name matches a `RepoName.{planner,builder,reviewer}`, produce
`_Constraint(repos={<that_repo>, RepoName.master}, states=None)` per intent
§Decisions. Otherwise the source is silent.

Must not raise on tmux absence — resolution works fine without tmux. Catch
`libtmux.exc.LibTmuxException` / `OSError` and return a silent constraint.

### Merging the constraints and final disambiguation

1. Collect constraints from all sources.
2. Compute `repos = intersection of non-None .repos`; same for `states`.
3. Scan the filesystem under the surviving `repos × states` combinations for
   files whose stem matches the sequence number (and slug if known). The
   slug still comes from the filesystem: every source above is silent on the
   slug.
4. If exactly one file matches, construct and return the `Plan`.
5. If multiple files match, raise `AmbiguousPlanError` whose message lists
   each match as a relative path (`{repo}/docs/plans/{state}/{name}.md`).
6. If zero files match:
   - If the intersection on either dimension is empty, build a union report:
     for each `(repo, state)` combination that at least one source supports,
     print which sources supported it. Raise `WorkflowError` with this
     multi-line message so the CLI layer shows it and exits non-zero.
   - Otherwise raise `PlanNotFoundError` with the tried `(repo, state)` set.

The worked examples in intent §Option 1 are the acceptance scenarios for
this section — each becomes an integration test in `tests/test_plan_resolver.py`.

### Caller updates

- `workflow/commands/workflow.py`: every `resolve_plan(...)` call loses its
  `expected_states` / `repo` kwargs and gains `command_name` / `config`.
  The strict-state commands (`todo`, `implement`, `check`, `accept`, `reject`)
  add an explicit state check after resolution:
  ```python
  if plan.state not in EXPECTED_STATES_FOR[command_name]:
      raise WorkflowError(
          f"{command_name!r} expects plan in {EXPECTED_STATES_FOR[command_name]}, "
          f"got {plan.state.value!r}."
      )
  ```
- `workflow/commands/workflow.py`: `_resolve_validate_plan`, `_resolve_across_repos`,
  and `_state_has_validate` are deleted — the merged resolver subsumes them.
  The `validate` CLI command calls `resolve_plan(..., command_name="validate")`.
- `workflow/tmux_dispatch.py`: `_resolve_plan_target` is simplified — it calls
  `resolve_plan(fuzzy_path, config.project_dir, config=config, command_name=cmd_name)`
  and then looks up the matching `_PromptTarget` by `(command_name, plan.state)`.
  The per-repo loop, the `targets_by_repo` dict, and the double iteration are
  gone.

Commit message: `feat(workflow): merged multi-source plan resolver`.

## File organization

Phase 1:
- `workflow/validate.py` (renamed from `workflow/doublecheck.py`)
- `workflow/commands/workflow.py` — identifier and registration renames
- `docs/default_config.yaml` — `doublecheck` entry renamed to `validate`
- `tests/test_validate.py` (renamed from `tests/test_doublecheck.py`)
- `tests/test_cli.py`, `tests/test_workflow_commands.py` — identifier renames

Phase 2:
- `workflow/commands/workflow.py` — split `push` into `check` + new `push`
- `workflow/config.py` — `ScriptCommand.repo_override: RepoName | None = None`
- `docs/default_config.yaml` — add generic `push` entries; rename the handoff
- `tests/test_workflow_commands.py` — rename the existing push test to
  `test_check_runs_pre_commit_and_prints_review_reminder`; add a new test
  for the generic `push` command

Phase 3:
- `workflow/plan_resolver.py` — rewrite (kept filename)
- `workflow/audit.py` — add the `command → (repo, state)` table as a module
  constant
- `workflow/commands/workflow.py` — remove `_resolve_validate_plan` /
  `_resolve_across_repos`; update every `resolve_plan(...)` call site
- `workflow/tmux_dispatch.py` — simplify `_resolve_plan_target`
- `tests/test_plan_resolver.py` — rewrite the `TestResolvePlan` class to
  exercise the new signature and the worked examples from intent §Option 1
- `tests/test_cli.py`, `tests/test_workflow_commands.py` — update resolver
  call shapes in the thin integration tests

## Dependencies

- Intent 012 — the specification this plan implements
- Existing 005-series modules (`doublecheck`, state transitions, git helpers)
- `libtmux` — already a project dependency

## Risks

- **Phase 3 touches every command.** Stage the merged resolver behind the
  public `resolve_plan` so the callers' diffs stay localized to kwarg
  changes. Run the full test suite after each phase.
- **Git history queries across four repos on every resolve.** In practice
  these repos are small and `--max-count=1` short-circuits; but worth
  keeping an eye on. The fast path (fuzzy path fully determines repo +
  state + seq) sidesteps git entirely for the hot path.
- **Audit log's empty-source vs. missing-entry distinction.** A brand-new
  intent has no audit log entries — the source must return silent, not an
  empty set. Unit test this explicitly.
- **Tmux absence in headless CI** must result in a silent source, not a
  crash or a false constraint.
- **Rename across branches** during phase 1: the `git mv` keeps history;
  no cross-branch issues because `validate` is a file rename within this
  branch.

## Testing

- Pre-commit hook (`black`, `ruff`, `pytest`, `mypy`) passes after each
  phase.
- Each phase has its own commit, merged (locally) only once the whole
  chain is green end-to-end.
- New unit tests for each new helper (`_from_*` functions in phase 3).
- New integration tests covering the three worked examples from intent §12.
- Adjust existing tests rather than deleting — signature changes propagate
  through mechanical edits, not by removing coverage.
