# PR review: 025 stacked plans (`implement --onto`)

Branch: `feat/025_stacked_plans` @ `5a880d0` — 7 commits, 17 files, +1636/-106.
Plan: `docs/plans/checking/025_stacked_plans.md`.

Gates run locally: `pytest -m "not slow"` 447 passed (98s), `mypy workflow tests` clean,
`ruff check` clean, `vulture` reviewed.

Overall: the design lands close to the plan. `workflow/stack.py` is a clean single home for
stack shape, the `feat/` literal sweep is complete and enforced by a regression test, and the
frontmatter module is small and well tested. Three findings are blocking: one bricks a plan
file, one leaves `doctor` permanently red, one prints a command that can destroy `main`.
Findings 1, 3 and 4 were reproduced against real git repos (throwaway scratch tests, deleted).

## Blocking

1. **Self-stack is unguarded and permanently bricks the plan file.** `_check_onto_preconditions`
   never checks `plan.name != base_plan.name`. Builder normally sits on `feat/<plan>` while
   implementing, and `implement` legitimately accepts a plan already in `implementing`
   (`_EXPECTED_STATES_FOR`), so `workflow implement <that plan> --onto` (bare `--onto`, inferring
   the base from the current branch) resolves the plan as its own base. Reproduced:
   the run writes **and commits** `onto: 005_foo` into `005_foo.md`, pushes canon, and only then
   fails with `onto cycle detected`. Afterwards `check`, `accept` and every prompt dispatch for
   that plan fail with the same error; recovery needs a hand edit of the plan file.
   Fix: `require(plan.name != base_plan.name, ...)` in `_check_onto_preconditions`.

2. **Preconditions are not all before mutation, contrary to plan §3.** `resolve_stack` — which
   raises on a cycle, an unresolvable `onto`, or the depth cap — is first called in `implement`
   *after* the frontmatter write, the transition commit and `git_push(canon)` (via the
   `prior_stack` computation feeding the audit log and `_print_stack_note`). Any of those errors
   therefore leaves a committed-and-pushed mutation behind and exits 1. Resolve (and validate)
   the resulting stack in the precondition block; reuse the value afterwards.

3. **`doctor` is permanently red after any accepted stack.** `_stack_anomalies` iterates
   `_PIPELINE_STATES`, which includes `done`/`rejected`. `accept` deletes the shared branch but
   the done plan keeps `branches.docs.branch` in its frontmatter, so the
   "recorded branch … exists nowhere" check fires forever. Reproduced end-to-end: after the
   full loop, `doctor` prints `006_bar: recorded branch 'feat/005_foo' exists nowhere` and exits 1.
   Fix: apply the `branches` existence check only to plans in `implementing`/`checking` (the
   orphaned-branch check still needs the done/rejected plans, so filter per check, not globally).

4. **`reject`'s rollback hint can hard-reset `main`.** The note prints
   `git -C <project>/builder reset --hard <anchor>` with no branch context. `check` parks builder
   on `main`, so rejecting a stacked plan after `check` (an explicitly documented scenario in
   `workflow.rst`) prints a command that resets builder's `main` to a feature-branch commit.
   Reproduced: builder is on `main` at reject time in that flow. Print the branch too — e.g.
   `git -C <builder> checkout <branch> && git -C <builder> reset --hard <anchor>` — or use
   `git branch -f <branch> <anchor>`, which is safe regardless of what is checked out.

## Should fix

5. **`git_commits_ahead_of_remote` fails open.** `returncode != 0 → 0` means a missing
   `origin/<branch>` (never pushed, pruned, stale fetch) silently satisfies precondition 1 — the
   exact condition the guard exists to prevent (reviewer's `pr`/`fix` commits excluded from the
   stack). Distinguish "no remote branch" from "zero ahead" and raise. `int(...)` on unexpected
   stdout would also raise a bare `ValueError`.

6. **The frontmatter write discards unknown keys.** `_run_implement_onto_steps` constructs a fresh
   `PlanFrontmatter(onto=…, branches=…)` carrying over only `branches`, so any extra key is lost on
   write — defeating the stated purpose of `extra="allow"` (023.6 keys survive a parse but not a
   restack/rerun). Use `existing.model_copy(update={"onto": …, "branches": …})` when
   `existing is not None`.

7. **`reject`'s stacked-plan branch is selected on `branches`, not on `onto`.** With
   `fm.onto is None` but a `branches` entry present the message reads
   `None and this plan share the branch …`. That is hand-edit-only today, but 023.6 writes
   `branches` for *every* plan in oob mode, at which point unstacked plans hit this path. Gate on
   `fm.onto is not None`.

8. **`git_list_branches` is dead code** (`workflow/git_helpers.py:252`, confirmed by grep and
   vulture — no caller anywhere). Delete it.

9. **Two planned tests are missing.** Plan §Verification lists (a) `main` advancing between
   `implement --onto` and `accept`, exercising the ff-then-rebase path of `transfer_branch_to_main`
   — this is the mitigation named in Risk 5 for merge commits being flattened — and (b) `--onto`
   refused when the base is in `checking` with a reviewer commit ahead of bare. Neither exists, so
   precondition 1 and the whole `base in checking` path have zero coverage. `_stack_anomalies` is
   also entirely untested (finding 3 would have been caught).

## Minor

10. **New integration tests cost ~55s of wall clock in real `sleep`.** The 7 stacked-plan tests
    take 66s (suite total 98s), almost all of it `_settle()`'s `1.1s` sleeps. Stamping
    `GIT_COMMITTER_DATE`/`GIT_AUTHOR_DATE` in the `_git` helper would make the ordering
    deterministic *and* fast; failing that, mark them `slow`.

11. **`_setup_has_plan`'s mid-stack guard is broader than the plan.** Plan §5 scopes it to the
    `pr`/`fix` dispatch; the implementation blocks every prompt for a `checking` plan (including
    `ask` and `rebase`). Also, `stack_child` is called *outside* the `try` that converts
    `WorkflowError` into a friendly message, so malformed frontmatter surfaces as a traceback.

12. **`check` does not verify the pushed branch matches the recorded one.** It pushes whatever
    branch the target repo is on but tells reviewer to check out `branch_for_plan(plan)`. A cheap
    `require(current_branch == branch, ...)` when the plan is still `implementing` would close the
    gap (the existing check only asserts the `feat/` prefix).

13. **`check` now forces `repo_override=target_repo`** where the old code derived the repo from
    `plan.path.parts[0]` while committing in `target_repo`. This looks like a genuine fix for a
    latent mv/commit repo mismatch, but it is a behavior change the plan does not mention — worth a
    line in the plan or commit body.

14. **`implement --onto` mixes repo assumptions.** The frontmatter is written and `git_add`ed in
    canon (`locate_plan_file(..., RepoName.canon)`), while the following `_transition_if_needed`
    commits in `plan.path.parts[0]`. The resolver pins `implement` to canon in practice, so this is
    latent, but passing `repo_override=RepoName.canon` explicitly would make it robust — otherwise a
    resolution elsewhere leaves a staged, never-committed change in canon.

15. **`tip_plan_for_branch` falls back to `candidates[0]`** when every candidate has a child (only
    possible with a cycle), silently returning an arbitrary plan. Prefer raising, or state the
    fallback's rationale in the docstring.

16. **Repeated frontmatter reads.** `tip_plan_for_branch` calls `stack_child` per candidate, each
    re-scanning all canon plans and re-reading files; `doctor._branch_exists_anywhere` spawns a git
    process per repo per plan. Fine at current plan counts — add a `ponytail:` comment naming the
    O(n²)/process-per-plan ceiling rather than leaving it implicit.

17. **Nits.** Two `assert config.project_dir is not None` eleven lines apart in `_setup_has_plan`
    (hoist one). `workflow/stack.py` imports `WorkflowConfig` at runtime although it is used only in
    annotations, unlike the `TYPE_CHECKING` blocks elsewhere. `_MAX_STACK_DEPTH` is private but
    imported by tests — acceptable, just noting.

## Verified good

- No `feat/` literal remains outside `stack.py`; all five call sites converted, and the grep
  regression test enforces it.
- Frontmatter round-trip, malformed-YAML, missing-fence, extra-key and body-preservation tests are
  thorough; `write_frontmatter` keeps the body byte-identical.
- Both `build` and `pr` templates render correctly stacked and unstacked under `StrictUndefined`
  (checked by rendering them directly), and `stack` is always passed.
- Stack member paths in the `pr` prompt resolve to the reviewer worktree in the real flow
  (verified against a live stack), so the `{{ e.path }}` entries are readable by the agent.
- `check`/`accept` cascade the same `stack` list through classification and execution (Risk 2),
  and the audit log records `output_plans`/`stacked_on` as designed.
- The 023.2 / 023.6 plan amendments required by §Dependencies are present on main.
