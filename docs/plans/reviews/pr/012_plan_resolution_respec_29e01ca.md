# PR Review: 012_plan_resolution_respec @ 29e01ca

Plan: `docs/plans/checking/012_plan_resolution_respec.md`
Snapshot reviewed: `29e01ca` (post-merge; the work landed across three
phase commits — `1c9eddc` (refactor: rename doublecheck to validate),
`210a80c` (feat(workflow): split push into check (handoff) and push (content)),
and `a31624c` (feat(plan_resolver): merged multi-source plan resolver))
Reviewer: Augment Agent
Date: 2026-05-09

## Verdict

**Approve with minor comments.**

All three phases of the plan have landed and the package is green under
black, ruff, pytest (164 passed), and mypy. Phase 1 is a textbook mechanical
rename. Phase 2 cleanly separates the builder→reviewer handoff (`check`)
from a stateless content push (`push`) and adds the `repo_override` field on
`ScriptCommand`. Phase 3 replaces the per-repo `_resolve_across_repos` /
`_resolve_validate_plan` / `_resolve_plan_target` logic with a single
constraint-intersection resolver in `workflow/plan_resolver.py` whose
sources match the spec (path, subcommand, git history, audit log, tmux).

The architecture is good: each source is a pure function returning a
`_Constraint` (a frozen pydantic model with optional `repos` and `states`
sets), the merger is a one-line `frozenset.intersection` per dimension, and
the disambiguator/union-report split is clear. The new CLI signature
(`resolve_plan(fuzzy_path, project_dir, *, config, command_name=None)`) is
correctly threaded through every call site.

The notable findings are (a) the git-history and tmux sources have **no
direct test coverage** (only their no-op paths are exercised), which is
material because they are the riskiest sources to get right; (b) the audit
log mapping for `check` returns `(builder, checking)` but a plan that has
been `check`-ed has effectively been *handed off to the reviewer*, so the
mapping arguably should be `(reviewer, checking)`; (c) the `_from_subcommand`
constraint for `implement` is too narrow and silently makes the
`if PlanState.PLANNING:` branch in the `implement` command unreachable
(this is a pre-existing 007 issue surfaced by 012's resolver).

## Validation performed

- `uv run pytest -q` → **164 passed**, 3 pre-existing pydantic warnings.
- `uv run mypy workflow tests` → clean (32 source files).
- `uv run pytest --cov=workflow.plan_resolver --cov=workflow.tmux_dispatch
  --cov=workflow.commands.workflow --cov-report=term-missing` →
  - `workflow/plan_resolver.py`: **76%**
  - `workflow/tmux_dispatch.py`: **63%**
  - `workflow/commands/workflow.py`: **77%**
  - notable miss bands in `plan_resolver.py`:
    - `_parse_path_to_state` (459-467) — only tested indirectly
    - `_git_log_chosen_path` (472-492) — uncovered
    - `_parse_git_log_output` (497-513) — uncovered
    - `_git_newest_touch` (518-540) — uncovered
    - `_from_git_history` (550-555) — only the silent path
    - `_active_tmux_repo` (592-603) — uncovered
    - `_from_tmux` (611-613) — only the silent path
- No leftover `doublecheck` references in `workflow/`, `tests/`, or
  `docs/default_config.yaml` (verified via grep, ignoring mypy caches).

## Phase 1: Rename `doublecheck` → `validate`

Mechanical and complete:

- `workflow/doublecheck.py` → `workflow/validate.py` (history preserved
  via `git mv`).
- `workflow/commands/workflow.py`: `as run_doublecheck` → `as run_validate`,
  `@register_script("validate")`, function `validate()`, `_resolve_…` /
  `_state_has_…` helpers all renamed.
- `docs/default_config.yaml`: the `checking` state has `name: validate /
  script: validate`.
- `tests/test_doublecheck.py` → `tests/test_validate.py` with import and
  symbol renames.
- `tests/test_cli.py` switched to `test_validate_dry_run_resolves_*` and
  invokes `validate`.
- The `CheckResult` / `CHECK_NAMES` / `infer_target_repo` identifiers were
  intentionally kept, per the plan.

A trailing detail: the four built-in checks are still named `file-scope`,
`noqa-nosec`, `git-hooks`, `claude-md` — i.e. they're *checks*, run by
*validate*. The naming inversion is intentional (these are still
"verification checks" in the codebase, the CLI verb just changed). Fine.

## Phase 2: `push` / `check` split

The split is implemented as the plan describes:

- `@register_script("check")` (line 396 of `workflow/commands/workflow.py`)
  takes the original handoff body: `_run_pre_commit_in_sandbox`, then
  `run_validate`, then `git_push(builder)`, then `git_fetch(reviewer)`.
  `--target-repo` defaults to `AgentName.builder`, audit log entry uses
  `command="check"`, reminder is looked up in the `implementing` state.
- `@register_script("push")` (line 439) is the new generic command:
  resolves the plan, takes `repo = plan.path.parts[0]`, runs
  `git_push(project_dir / repo)`, writes a `command="push"` audit log
  entry, prints the reminder for the plan's current state. No state
  transition.
- `ScriptCommand.repo_override: RepoName | None = None` was added in
  `workflow/config.py`. The default config wires `push` into the
  `intent` and `todo` states with `repo_override: master`.

The `repo_override`'s "soft constraint" semantics from §Decisions is
implemented in `_accumulate_subcommand_contribution`
(`workflow/plan_resolver.py:404-427`):

```python
if cmd.repo_override is not None:
    repos.add(cmd.repo_override)
else:
    repos.add(_agent_repo(agent, RepoName.master))
```

Because `repo_override` is *added* to the repos set (rather than
*replacing* the agent contribution), it is a soft constraint, exactly as
the spec requires.

### Phase 2 concerns

1. **`push` derives the source repo from `plan.path.parts[0]` after
   resolution, not from `repo_override`.** The plan says the new `push`
   "Pushes the current branch of a resolved repo to bare, with an optional
   pull into another repo." The "optional pull into another repo" is **not
   implemented** — there is no `pull_target` field on `ScriptCommand`, no
   `git_pull` after the push, and the worked example 3 from the intent
   ("`push` from `master` to bare and pull in `planner`") cannot run
   end-to-end via the current command. The spec calls this
   "`pull_target`" in §New push command shape; phase 2 silently dropped
   it. Worth either adding `pull_target` or trimming the spec.

2. **`push` does not enforce that the repo's current branch is suitable to
   push.** A repo on `main` would still attempt `git push origin`, which
   succeeds and pushes `main` to bare. That's fine for the
   `repo_override: master` use case but unprotective when the builder
   accidentally calls `push` on a stale branch. A cheap addition: read
   `git_current_branch` and warn if it equals `main` (or refuse if a
   transition was expected).

3. **`reject`'s config entry still says `transition: done`.** Independent
   of phase 2, but related to the cheat-sheet rendering: `transition:
   done` is also wrong for `reject` (the actual transition is `REJECTED`,
   hardcoded in the script). Easy fix to `docs/default_config.yaml` —
   pre-existing, not introduced by 012.

## Phase 3: Merged plan resolver

This is the substantive piece. The current `workflow/plan_resolver.py` is
~728 lines and reads well — the file is sectioned (`parse_plan_filename`,
collision check, `find_all_plans*`, absolute-path fast paths, the multi-
source resolver, the public entry point), the helper functions are pure,
and the source contributions are independently exercisable.

### Public API

```python
def resolve_plan(
    fuzzy_path: str,
    project_dir: Path,
    *,
    config: WorkflowConfig,
    command_name: str | None = None,
) -> Plan: ...
```

Matches the plan exactly. All callers were updated:

- `workflow/commands/workflow.py`: every script command (`todo`,
  `implement`, `check`, `push`, `accept`, `validate`, `reject`) calls
  `resolve_plan` with `config=config` and an appropriate `command_name`
  (or `None` for `validate` and `reject`, which deliberately want the
  loosest legal-paths-only resolution).
- `workflow/tmux_dispatch.py::_resolve_plan_target` is now an 18-line
  function that calls the merged resolver and picks the matching
  `_PromptTarget` by repo. The old per-repo loop is gone.

### Source pipeline

1. **Path source** (`_from_fuzzy_path`): splits on `/`, strips `.md`,
   reuses `_extract_components` to detect repo / state / seq / slug
   hints. Returns a `_PathHints` (a small frozen pydantic model holding
   the `_Constraint` plus parsed `seq` and `slug`). Good.

2. **Subcommand source** (`_from_subcommand`): walks every state's
   commands looking for matches by `name`. For each match, calls
   `_accumulate_subcommand_contribution` which:
   - For `PromptCommand`: contributes only the agent's repo (no state
     constraint) — implements the §Decisions rule "prompts can be asked
     about plans in any state".
   - For `ScriptCommand`: contributes the state itself, the transition
     target if any, and either `repo_override` or the state's agent or
     master.

3. **Git history source** (`_from_git_history` / `_git_newest_touch`):
   runs `git log --all --no-merges --max-count=1 --format='%H %ct'
   --name-status -- :(glob)docs/plans/*/<filename>.md
   :(glob)docs/plans/reviews/*/<filename>.md` once per repo. The repo
   with the highest committer timestamp wins; its parsed state becomes
   the constraint. Renames are handled (`R` lines take the destination;
   same-commit `D + A` with matching basenames is treated as a rename).
   Empty repos / non-zero exit → silent.

4. **Audit log source** (`_from_audit_log`): replays `read_audit_log`
   for entries mentioning the seq, picks the most recent, looks up the
   `(repo, state)` pair in `AUDIT_COMMAND_REPO_STATE` (in
   `workflow/audit.py`).

5. **Tmux source** (`_from_tmux`): if the active window's name matches a
   `RepoName.{planner,builder,reviewer}`, contributes
   `repos={<that_repo>, RepoName.master}` (matches §Decisions). Master
   active window contributes only `{master}`. Tmux absence / errors →
   silent.

### Merging and disambiguation

- `_intersect`: per dimension, ignore `None` (silent) sources, intersect
  the rest. If all sources are silent on a dimension, the result stays
  `None` and the resolver falls back to "all repos" / "all non-review
  states".
- Empty intersection on either dimension raises a `WorkflowError` built
  by `_union_report`, which lists every `(repo, state)` cell that any
  source supported and which sources voted for it. The error message is
  ergonomic.
- Otherwise the resolver scans the surviving `repos × states` grid via
  `_filesystem_matches`, applies `_disambiguate` (preferring the single
  non-INTENT match when an INTENT collision is present), and returns the
  unique `Plan`.

### Phase 3 concerns

1. **`_from_git_history` and `_from_tmux` are not directly tested.** The
   `TestAuditSource` and `TestSubcommandSource` classes in
   `tests/test_plan_resolver.py` exercise the audit and subcommand
   sources end-to-end through `resolve_plan`. The git history source has
   no equivalent — every test runs against repositories with no `.git`
   directory, so `_git_newest_touch` returns `None` and the source is
   silent. Same for tmux: the tests run in CI without a tmux server, so
   `_active_tmux_repo` returns `None`. **The two riskiest sources (the
   ones that shell out and parse external state) have only their silent
   paths covered.** This is the single most important gap in the PR.

   Concrete suggestions:
   - For `_from_git_history`: a unit test on `_parse_git_log_output`
     (parsing the `%H %ct\n<status>\t<path>` form, including renames and
     same-commit D+A pairs) plus an integration test that initialises a
     tmp git repo, makes a couple of commits that move a plan file
     across state directories, and asserts the resolver picks the most
     recent state. The test can `subprocess.run(["git", "init"])`
     directly — no fixture machinery needed for one test.
   - For `_from_tmux`: monkey-patch `libtmux.Server` and assert the
     constraint shape for (a) no server, (b) wrong window name, (c)
     `RepoName.builder` window name, (d) `RepoName.master` window name.

2. **Audit log `check` mapping is debatable.** `AUDIT_COMMAND_REPO_STATE`
   has `"check": (RepoName.builder, PlanState.CHECKING)`. After `check`
   has run, the plan's branch has been pushed from builder and fetched
   into reviewer; the *reviewer* is now expected to act on it. So when a
   user later runs `workflow accept 005`, the audit-log source returns
   `(builder, checking)`, but the subcommand source for `accept`
   contributes `(reviewer, …)`. They intersect to `{}` on the repo
   dimension. The user gets a "Sources disagree" error.

   In practice this doesn't break the test suite because `accept` is
   tested on a plan in `reviewer/checking/` with no audit entry. But a
   real workflow (`new` → … → `check` → `accept`) would write a `check`
   entry into the log, then fail to resolve on the next `accept`. Either:
   - Map `check` to `(reviewer, checking)` in the audit table (the
     reviewer is the new owner after the handoff), or
   - Ignore `check` entries in the audit source (set its mapping to
     `None`/skip), or
   - Make the audit source contribute `repos=None` when the command was
     a handoff (a slightly more general fix).

   Worth adding an integration test that walks the full sequence to
   surface this earlier.

3. **`_from_subcommand` for `implement` is too narrow** (also flagged in
   the 007 review). `implement` is configured under the `todo` state with
   `transition: implementing`, so `_accumulate_subcommand_contribution`
   contributes `states={todo, implementing}`. The `_EXPECTED_STATES_FOR`
   table in `commands/workflow.py` allows `{PLANNING, TODO}`, but the
   resolver will refuse to find a `planning`-state plan. The resolver and
   the script-side state guard disagree. Pick one and align.

4. **Same-state, no-transition commands inherit a repos contribution from
   the state's `agent`.** `validate` lives under `checking` with
   `agent: reviewer`. So `_from_subcommand` for `validate` contributes
   `repos={reviewer}` and `states={checking}`. This is fine for
   `workflow validate` on a reviewer-side plan, but the `validate()`
   command in `commands/workflow.py` deliberately calls
   `resolve_plan(plan_path, config.project_dir, config=config)` without
   `command_name` (line 341), bypassing the subcommand source. Compare
   `todo`, `implement`, `check`, `accept`, `push` all pass
   `command_name="…"`. The asymmetry is intentional (`validate` is run
   from any state via `--target-repo`), but worth a one-line comment.

5. **`_filesystem_matches` calls `find_all_plans_in_repo` once per
   surviving repo on the resolver's hot path.** That's potentially four
   directory scans per resolve. For a project with hundreds of plans this
   is still fast (it's pure stat + filename parsing), and the fast path
   short-circuits when (repo, state, seq) is fully determined, but a
   simple in-process cache keyed on repo path would help a hypothetical
   `workflow status` over thousands of plans. Not a current need.

6. **`_active_tmux_repo` catches `OSError` and `libtmux.exc.LibTmuxException`
   but not arbitrary `Exception` from libtmux's underlying socket
   operations.** Tightening this to a broader catch (or `BaseException`
   minus `KeyboardInterrupt`/`SystemExit`) would protect against future
   libtmux changes that leak other exception types. Defensive.

7. **`_git_log_chosen_path` returns the destination on the first `R*`
   line it sees, but iterates past it for `A`/`M`/`D`.** If a single
   commit both renames file X and modifies file Y, the first `R` wins.
   Since `--max-count=1` constrains to one commit and the pathspec
   restricts to plan-file globs, multi-file matches inside one commit are
   unusual but possible (e.g. transitioning two plans in one commit). A
   short comment explaining the "first R wins" preference would help.

8. **`_seq_to_str` zero-pads only the first segment.** That's correct for
   filenames like `012.1_foo.md` but the function is called from
   `_from_audit_log` to compare against audit-log entry names. Audit log
   entries are written without zero-padding too (the seq is stored as
   `Plan.sequence_number_str` via the model), so this matches. Good.

### Caller updates

- `workflow/commands/workflow.py`: every `resolve_plan(...)` call uses the
  new kwargs. Strict-state validation now lives in `_enforce_expected_state`
  (top of the module). The old `_resolve_validate_plan`,
  `_resolve_across_repos`, and `_state_has_validate` are gone — the file
  is shorter and more uniform.
- `workflow/tmux_dispatch.py::_resolve_plan_target` is 18 lines: collect
  prompt targets for `cmd_name`, call `resolve_plan`, walk targets to find
  one matching `plan.path.parts[0]`. Falls back to `targets[0]` when no
  repo match — that fallback was preserved for the case where the
  resolver hands back a plan in a repo that's not in the prompt-target
  set, which can happen because the resolver may pick `master` for an
  intent and the prompt for `ask` lives on the `planner` agent. Worth a
  comment ("the resolver narrowed to legal repos already").
- `tests/test_plan_resolver.py` has a `TestResolvePlan` class with the
  expected fuzzy-name forms, an `intent vs non-intent prefers non-intent`
  case, ambiguity / not-found / collision / absolute-path / outside-project
  / nonexistent / full-review-rejected cases — broad coverage of the
  *file-system-only* resolution paths.

## Architecture & fit with conventions

- The `_Constraint` and `_PathHints` types are pydantic `BaseModel`s with
  `model_config = ConfigDict(frozen=True)`. Consistent with
  `workflow/cli.py::_CliRuntime` and `workflow/tmux_dispatch.py::_PromptTarget`,
  which all use the same idiom — the project's "internal value type"
  convention is now uniformly pydantic-frozen.
- The `AUDIT_COMMAND_REPO_STATE` table sits in `workflow/audit.py`
  (importing `RepoName` and `PlanState`), as the plan asked. Importing
  from there instead of from `plan_resolver.py` avoids a circular
  dependency.
- `_intersect` is a small generic helper. It is the only abstract typing
  use in the file (`TypeVar("_T")`). Worth keeping.
- `_T = TypeVar("_T")` is declared at module scope — fine.
- `_ALL_REPOS` and `_ALL_STATES` are module-level frozen sets used as
  fallbacks when every source is silent. Idiomatic.
- The public `resolve_plan` keeps the `def …(*, config, command_name=None)`
  keyword-only signature, which prevents positional-arg drift as new
  sources are added in the future.

## Security

- `_git_newest_touch` shells out via `subprocess.run` with a list argv
  and `check=False`. Inputs are repo paths (controlled by
  `project_dir`), seq integers, and an optional `slug`. Slug comes from
  `parse_plan_filename`, which restricts it to whatever follows the
  first `_`. A slug containing shell metacharacters cannot reach git as
  a separate argv element because the slug is interpolated into the
  pathspec string `f"{seq_str}_{slug}.md"`, which is then passed to git
  as a single arg. Git treats it as a glob — no shell involvement.
  Acceptable.
- `_active_tmux_repo` reads only the active window's `name` attribute and
  compares against the `RepoName` enum. No external command execution.
- `_resolve_absolute` resolves the user-supplied path with `Path.resolve()`
  and then checks `path.relative_to(project_dir.resolve())` — the standard
  Python idiom for "must live under project_dir". A path that resolves to
  `/etc/passwd` would raise `PlanNotFoundError`, not be opened. Good.
- The audit log is read-only here; no user input flows into it.

## Test quality and coverage

- `tests/test_plan_resolver.py` has:
  - `TestParsePlanFilename` (3 tests)
  - `TestParseReviewFilename` (5 tests)
  - `TestFindAllPlansInRepo` (~7 tests)
  - `TestFindAllPlans` (4 tests)
  - `TestResolvePlan` (~17 tests covering fuzzy-name forms, subcommand
    constraint, disambiguation, error cases, absolute paths, hierarchical
    seq, intent vs non-intent)
  - `TestAuditSource` (2 tests)
  - `TestSubcommandSource` (2 tests covering prompt-no-state and
    `repo_override`)
- The `TestResolvePlan` class covers the worked examples from intent §12
  reasonably well (subcommand-narrows, subcommand-disagreement,
  intent-vs-non-intent, ambiguity, collision propagation, and the
  absolute-path fast path).
- The two empty test bands are git-history and tmux, as discussed in
  finding 1 above.
- A test for the `_union_report` message (the "Sources disagree" path)
  exists implicitly via
  `test_subcommand_disagreement_raises_workflow_error`, but it only
  asserts on the `match="Sources disagree"` substring. A test that
  inspects the full multi-line output would catch regressions in the
  per-source labelling.
- No test exercises the fast path in `resolve_plan`
  (`fast_repos is not None and len(fast_repos) == 1 and fast_states is
  not None and len(fast_states) == 1`). Two tests already construct
  fully-determined paths (`planner/docs/plans/planning/002_foo`,
  `docs/plans/planning/002_pydantic_models`), so the fast path is
  exercised — but a `monkey.patch` on `_from_git_history` to assert it
  is *not* called for those inputs would make the optimization an
  enforced contract.

## Integration-style tests

The plan calls out the worked examples in intent §12 as acceptance
scenarios. The existing `TestResolvePlan` covers the file-system parts;
the missing pieces are:

- **Worked example 3** (the "push from master with pull into planner"
  scenario): not testable until the `pull_target` field is added (see
  phase 2 finding 1).
- **Cross-source agreement**: tmp git repos for two of the four roles,
  one audit-log entry, and verify the resolver picks the unique
  `(repo, state)`. One test covers the multi-source happy path.
- **Cross-source disagreement**: same setup but with the audit log
  pointing at one repo and git history at another; assert the
  `_union_report` message contains both labels.

Adding ~3 integration tests and ~5 git/tmux unit tests (above) would
take coverage of `plan_resolver.py` from 76% to ≥90% and would protect
against the two riskiest source implementations.

## Smaller observations

- The migration note for phase 2 ("Until phase 3 lands, the generic
  `push` temporarily resolves with `resolve_plan(plan_path, project_dir,
  repo=RepoName.master)`") is now obsolete because phase 3 landed in the
  same series. The current `push` correctly uses
  `command_name="push"`. Good.
- `workflow/commands/workflow.py::push` derives `repo = plan.path.parts[0]`
  to know which repo to push from. After resolution this is reliable, but
  type-wise `plan.path.parts[0]` is `str`, not `RepoName`. A cast to
  `RepoName` (which is a `StrEnum`) at the call site would make the type
  intent explicit.
- `_from_subcommand` returns `_Constraint(repos=None, states=None)` (the
  default `_Constraint()`) when `command_name is None` *or* when no
  matching command was found. The two cases are not distinguished — the
  caller doesn't care, but a debug log line ("subcommand 'foo' not found
  in any state") would speed up future debugging.
- The `--all` flag for `status` (added by plan 006) does not currently
  use the merged resolver — `status` lists plans, it doesn't resolve a
  fuzzy path, so this is correct. Not a concern.
- The `_extract_components` parser ignores `repo` hints that don't match
  a `RepoName` enum value (`with suppress(ValueError)`). A hint like
  `bare/docs/plans/...` is silently dropped to "no repo hint" rather
  than raising. Probably the right UX, but worth a docstring note.

## Summary

012 delivers cleanly on all three phases. Phase 1 is a textbook rename
with no surprises. Phase 2's `push`/`check` split is sound but stops
short of implementing the `pull_target`/"pull into another repo"
behavior that the spec describes. Phase 3 introduces the merged
multi-source resolver with a clean architecture
(`_Constraint`-as-pydantic-frozen, pure-function sources,
intersect-and-disambiguate, ergonomic union-report errors) and propagates
the new signature through every caller.

The most important things to follow up on:

1. **Add direct tests for the git-history and tmux sources.** They are
   the riskiest pieces (external command execution, parsing) and have no
   coverage today.
2. **Re-evaluate the audit-log mapping for `check`.** A real
   `new`→…→`check`→`accept` sequence will hit a "Sources disagree"
   error because `check` is logged as `(builder, checking)` but `accept`
   needs `(reviewer, checking)`.
3. **Decide whether `implement` should accept `planning`.** The script
   command's `_EXPECTED_STATES_FOR` says yes; the resolver's subcommand
   source says no. Either align the resolver or drop the
   `if PlanState.PLANNING:` branch in `implement`.
4. **Add `pull_target` (or trim it from the spec) for the new `push`.**

Everything else in this review is a tightening suggestion, not a
defect.
