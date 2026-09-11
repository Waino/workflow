# 026: Stacking bugs

Input: `intent/026_stacking_bugs.md`
Completeness: complete

## Goal

Two bugs made a live `implement --onto` run leave an unusable state: the stacked plan's file never
reached the shared feature branch, and the stack could not be resolved afterwards at all. Both are in
code shipped by 025 (stacked plans). Fix them, plus the sibling instance of the same root cause found
while auditing.

## Scope

In: `workflow/stack.py`, the `implement` command paths in `workflow/commands/workflow.py`, and their
tests. Out: any change to the stack data model (`onto` / `branches` frontmatter), to the
`check`/`accept`/`reject` cascades, or to the general fuzzy resolver in `workflow/plan_resolver.py`.

## Problem summary

### Bug 1: the `origin/main` merge is classified against a stale `origin/main`

`_classify_implement_onto_steps` decides whether to merge with
`git_is_ancestor(builder_repo, "origin/main", feature_branch)`. That question is asked *before*
`_run_implement_onto_steps` commits the stacked plan's transition in canon and pushes it, i.e. before
`origin/main` grows the very commit the merge exists to deliver. Whenever the branch already contained
`origin/main` as it stood at classify time, the step is reported as
`[skip] merge origin/main into stack branch    origin/main already merged` and the plan file never
lands on the branch. Everything downstream then fails: `ask`/`build` cannot resolve the plan in
builder, and `check` would abort in `transition_plan` with "not found in builder".

Why the e2e tests missed it: `test_stacked_plans_full_loop` creates *and* pushes plan B to `todo`
after A's branch already exists, so `main` has advanced, the ancestor check is false, and the merge
runs by accident. The real-world failure had both plans already on `main` in `todo` before A was
implemented, so nothing had advanced `main` since the branch was cut.

### Bug 2: `onto` is resolved with the fuzzy multi-repo resolver

`resolve_stack` resolves the `onto` value via `resolve_plan`, which searches every repo. A base plan
in `implementing` exists both in canon and in the agent's clone, and neither the tmux nor the audit
source narrows it, so `_disambiguate` raises `AmbiguousPlanError` — reported by `resolve_stack` as
"`does not resolve to a plan: Ambiguous reference`". This blocks every command that resolves a stack,
including read-only ones like `ask`.

`onto` values are written by the tool itself and are always canonical plan names, so fuzzy resolution
is the wrong mechanism: an exact-name lookup scoped to one repo is both correct and unambiguous.

The same defect applies to the user-typed `--onto` argument in `_resolve_onto_base_plan`, which also
calls `resolve_plan` without any repo bias.

## Decisions

1. **Always run the `origin/main` merge; drop its classification.** `git merge origin/main` is a
   no-op ("Already up to date") when there is nothing pending, and after the canon push `main` has
   essentially always advanced. Re-deriving the predicate at run time would encode the same condition
   in two places; classifying on "is the plan file on the branch" would still be a pre-push check and
   would also miss unrelated `main` commits. Alternatives considered and rejected for those reasons.
2. **Repo-scoped exact-name lookup for stack references**, preferring the repo the referring plan was
   resolved in, then canon. Rejected alternative: adding a repo-bias parameter to `resolve_plan` —
   more general, but it grows the resolver's already-dense constraint machinery and still leaves
   state-level ambiguity. The lookup also keeps prompt-rendered member paths inside the agent's own
   repo, which the existing `{{ plan.path }}` convention expects.
3. **Stack-resolution failures keep failing hard for all commands**, including `ask` and `critique`. A
   broken stack is real misconfiguration; degrading to a warning would add a second render path and
   let agents act on incomplete stack context. Bug 2's fix removes the spurious failure anyway.
4. **Audit the other `ResumePlan` classifications** for the same "predicate invalidated by an earlier
   step of the same run" shape, and fix genuine instances (see below).

## Fix 1: unconditional merge in `implement --onto`

In `_classify_implement_onto_steps`, replace the `git_is_ancestor` branch with a plain
`resume.will_run(_STEP_IMPLEMENT_ONTO_MERGE_MAIN)`, and in `_run_implement_onto_steps` drop the
`resume.should_run(...)` gate around the fetch+merge so it always executes — the same
classify-as-`will_run`, run-ungated shape already used for `"push canon"`. Keep the step listed in the
resume output for transparency. Add a short comment stating *why* it is unconditional (the push
earlier in the same run moves `origin/main`, so no pre-push predicate can be trusted).

`git_merge(..., ff_only=False)` already handles the up-to-date case, so no helper change is needed.

## Fix 2: repo-scoped stack reference lookup

Add to `workflow/stack.py`:

```python
def find_stack_member(project_dir: Path, reference: str, prefer_repo: str = RepoName.canon) -> Plan | None:
    """Find the plan named by a stack reference (`onto` value or `--onto` argument).

    Matches the canonical plan name, or the bare sequence number for a hand-written
    reference. Searches *prefer_repo* first, then canon, so a stack resolved from an
    agent's clone keeps that clone's paths.
    """
```

Matching details:
- Normalize *reference*: take the last `/`-separated segment, strip a `.md` suffix. This makes
  `023.1`, `023.1_project_metadata_file`, `implementing/023.1_...` and a full path all work.
- Candidates come from `find_all_plans_in_repo` (skipping `Review` instances); match
  `plan.name == normalized` first, then `plan.sequence_number_str == normalized`.
- Do **not** filter by state: a base that has been rejected or has already moved to `done` must still
  be findable, otherwise `resolve_stack` would start raising for a child whose base was rejected.
- Return `None` when nothing matches; callers produce the error message.

Then:
- `resolve_stack` uses `find_stack_member(project_dir, onto, prefer_repo=current.path.parts[0])`,
  raising `WorkflowError` with the existing wording shape
  (`f"{current.name}'s onto={onto!r} does not resolve to a plan"`, plus the repos searched) when it
  returns `None`. The existing cycle and `_MAX_STACK_DEPTH` guards stay exactly as they are.
- `_resolve_onto_base_plan` uses `find_stack_member(project_dir, onto)` (canon-first) for an explicit
  `--onto PLAN`, failing with a message naming the reference when not found. The bare-`--onto`
  (infer-from-branch) path is unchanged. Note that passing `command_name="implement"` to `resolve_plan`
  was considered and rejected: `implement`'s configured states are `todo`/`planning`/`implementing`,
  so a base in `checking` — an explicitly supported `--onto` target — would start failing with
  `SourcesDisagreeError`.

`config` becomes unused in `resolve_stack` and `stack_entries`; remove the parameter there and in the
helpers that only carried it through (`_check_onto_preconditions`,
`_require_builder_not_on_unfinished_stack`). Call sites to update: `workflow/commands/workflow.py`
(`check`, `accept`, `implement` paths), `workflow/tmux_dispatch.py`, `tests/test_stack.py`. After this,
`stack.py` no longer imports `resolve_plan`, `AmbiguousPlanError` or `PlanNotFoundError`, and its
module docstring's dependency note ("imports `workflow.plan_resolver`") needs adjusting to the
`find_all_plans_in_repo`-only dependency.

## Fix 3: audit finding — plain `implement` on a pre-existing branch

Audit of every `_classify_*` function for predicates invalidated by an earlier step of the same run:

| classification | predicate depends on | verdict |
|---|---|---|
| `_STEP_IMPLEMENT_ONTO_MERGE_MAIN` | `origin/main` in builder | **broken** — Fix 1 |
| `_STEP_IMPLEMENT_FETCH_BUILDER` / `_CHECKOUT_MAIN_BUILDER` / `create <branch>` | builder branch state | sound for the fresh-branch path; leaves a gap when the branch pre-exists (below) |
| `_STEP_IMPLEMENT_ONTO_CHECKOUT`, `_STEP_CHECK_CHECKOUT_*`, `_STEP_ACCEPT_TRANSFER`, `_STEP_ACCEPT_REBASE`, `_STEP_TODO_*`, `_classify_rebase_step`, all `init` steps | state not mutated earlier in the same run | sound |

The one gap: in plain `implement`, when the feature branch already exists, `_STEP_IMPLEMENT_FETCH_BUILDER`
is skipped, so builder's branch never receives the transition commit that the same run pushes to
`main` — the identical failure mode as Bug 1, reachable via the `--continue` path ("branch exists but
plan not yet implementing"). Fix: after the push, when the branch already existed *and* builder is
currently on it, fetch and merge `origin/main` into it, reusing the same helper as Fix 1 (extract the
two-line fetch+merge into a small module-level helper so both paths share it). Do not touch builder
when it sits on some other branch — the existing "leave work in progress alone" guarantee holds.

## Verification

Unit tests (`tests/test_stack.py`):
- `find_stack_member` / `resolve_stack`: base plan present in both canon and builder in the same
  state resolves without error, and the returned member's path is in the referring plan's repo.
- Reference forms: canonical name and bare sequence number both resolve.
- Missing reference still raises `WorkflowError` naming the reference (keeps
  `test_resolve_stack_unresolvable_onto_raises` green); cycle and depth-cap tests unchanged apart
  from the dropped `config` argument.

Integration test (`tests/test_integration_workflow.py`, next to `test_stacked_plans_full_loop`) — the
Bug 1 regression, and the case the existing suite cannot express: create *and* `todo` both plans
first, then `implement A`, commit, `implement B --onto A`. Assert
`builder/docs/plans/implementing/006_bar.md` exists on the branch after the run, and that
`check 006_bar` then succeeds. Also assert the resume output no longer reports the merge as skipped.

Integration test for Fix 3: plain `implement` with a pre-existing `feat/<plan>` branch (the
`--continue` path) leaves the plan file present in builder.

One-off manual check in this project: with the in-flight 023.1/023.2 stack,
`workflow --dry-run ask 023.2` must resolve and render (it currently fails with the ambiguity error),
and `workflow status` must still show the stack.

## Recovery note for the current in-flight stack

`main` was already merged into `feat/023.1_project_metadata_file` by hand, so 023.2's file is on the
branch and its frontmatter is written. The fixes must therefore be idempotent against that state:
re-running `implement 023.2 --onto` after the fix should classify frontmatter and transition as
satisfied and merge as a no-op. No manual repair beyond the merge already performed is expected.

## Risks

1. **Unconditional merge creates a merge commit on the feature branch on every `--onto`.** Already
   true by design in 025 (which notes the rebase interaction in its risk list); this fix only makes it
   reliable. `accept`'s rebase path is already covered by
   `test_stacked_accept_rebases_when_main_advanced_after_onto`.
2. **The merge could now conflict where it previously silently no-op'd.** That is the correct
   behaviour — a real conflict between `main` and the stack must surface — but it is a new failure
   mode for operators to see. The error is a plain failing `git merge`, which leaves the branch in a
   conflicted state for manual resolution; do not add automatic abort handling in this plan.
3. **Dropping `config` from `resolve_stack`/`stack_entries` is a wide signature change.** Mechanical,
   but mypy must be clean across all call sites; do not leave an unused parameter behind as a
   compatibility shim.
4. **Out of scope, but noticed while tracing:** `resolve_stack` does not filter rejected members, so a
   child whose *base* was rejected will still cascade that base in `check`/`accept`
   (`_transition_stack_members` would move a rejected plan into `checking`/`done`).
   `_stackable_plans` filters rejected for child lookups, so the two directions disagree. Not fixed
   here — it needs its own decision about what rejecting a stack base should mean. Worth a follow-up
   intent.

## Dependencies

Builds on `done/025_stacked_plans.md` (fixes its implementation). No other plan dependencies; does not
conflict with the in-flight 023.x series, which touches different modules.
