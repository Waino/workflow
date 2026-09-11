# PR review: 026 stacking bugs

Branch: `feat/026_stacking_bugs` @ `447250e` — 3 commits, 7 files, +293/-69.
Plan: `docs/plans/checking/026_stacking_bugs.md`.

Gates run locally: `pytest` 478 passed (53s), `mypy workflow` clean, `ruff check` clean,
`black --check` clean, `vulture` reviewed (no new findings beyond the pre-existing noise, see nit 11).

Both bugs the plan targets are genuinely fixed, and I verified both new integration tests fail
against pre-fix `workflow/` in a scratch copy (`[skip] merge origin/main into stack branch` /
missing `builder/docs/plans/implementing/005_foo.md`). The `find_stack_member` design is the right
shape: small, exact, repo-scoped, and it removes `resolve_stack`'s dependency on the fuzzy resolver.

Two findings are blocking; both are regressions introduced by the deviation in
`_check_onto_preconditions` and by Fix 3's classification, and both were reproduced end-to-end
against real git repos (throwaway scratch tests, deleted).

## Blocking

1. **`implement --onto` now accepts a *rejected* (or done-but-uncleaned) base.** The state gate
   `base_plan.state == PlanState.IMPLEMENTING` was replaced by two `git_path_exists_at_ref` reads
   against builder's branch tree, so canon's state is no longer consulted at all. `reject` leaves
   the feature branch in place by design (`"...is left unmerged and can be deleted manually"`), and
   that branch's tree still carries `docs/plans/implementing|checking/<plan>.md` — so both reads
   succeed and the stack is built on a rejected base. `stack_child` does not block it either
   (`_stackable_plans` filters rejected). Reproduced: implement `005_foo`, commit, `reject 005_foo`,
   then `implement 006_bar --onto 005_foo` exits 0 and prints `Stack on feat/005_foo: 005_foo,
   006_bar`; against `origin/main` the same scenario correctly exits 1 with
   `must be in 'implementing' or 'checking', got 'rejected'`. The same hole applies to a `done` base
   whenever `delete_merged_branch`'s best-effort cleanup warned and skipped.
   Fix: keep a canon-state gate (`require(base_plan.state == PlanState.IMPLEMENTING, ...)`, which
   covers checking too since canon never shows `checking`) *and* use the ref-tree read only for the
   checking-vs-implementing distinction it was introduced for. Add the rejected-base case to the
   integration tests.

2. **Fix 3 leaves the Bug-1 failure mode intact when builder is not on the pre-existing branch, and
   labels it as a benign skip.** `_classify_implement_steps` marks
   `_STEP_IMPLEMENT_MERGE_MAIN` as `satisfied("builder is not on the feature branch")` — but nothing
   satisfied it: the transition commit this run pushes to `main` still never reaches the branch.
   Reproduced: branch `feat/005_foo` pre-created from `origin/main` with builder left on `main`,
   then `implement 005_foo --continue` exits 0 with
   `[skip] merge origin/main into existing feature branch   builder is not on the feature branch`,
   and `docs/plans/implementing/005_foo.md` is absent from the branch — the exact unusable state 026
   exists to eliminate, just via the "branch exists but plan not yet implementing" `--continue` door
   rather than `--onto`. The plan's "do not touch builder when it sits on another branch" guarantee
   is worth keeping, but the run must not report success silently.
   Fix options, cheapest first: classify as `needs_continue`/print a warning naming the branch and
   the manual merge; or (preferred) fast-forward the branch ref without a checkout when it is not
   checked out (`git fetch origin main:<branch>` is not safe in general, but
   `git_path_exists_at_ref` + an explicit warning is). At minimum, replace the `satisfied` reason
   with one that says the plan file will not land on the branch.

## Should fix

3. **The checking/implementing detection trusts builder's *local* branch ref only.** That ref is
   authoritative only because the default `check` (`--target-repo builder`) commits the transition
   there. With `check --target-repo reviewer`, builder's ref keeps `implementing/`, so
   `_check_onto_preconditions` silently takes the else-branch and skips the
   "reviewer has N commit(s) not yet pushed" guard entirely. Narrow (that path also pushes reviewer,
   so little is at risk today), but the guard is exactly the kind that must not depend on which flag
   an earlier command used. Belt-and-braces: `git_fetch(builder_repo)` first and accept either
   `base_branch` or `origin/{base_branch}`.

4. **The new error message is self-contradictory in the very state 026 recovers from.** When the
   base is `implementing` in canon but its file is missing from the branch (a pre-fix Bug-1
   leftover, or a hand-broken branch), the failure reads
   `--onto base X must be in 'implementing' or 'checking', got 'implementing' (per canon)`. Mention
   the branch that was inspected, e.g. `...; no plan file for X found on 'feat/X' in builder`.

5. **`resolve_stack` no longer wraps `PlanCollisionError`.** It is not a `WorkflowError` subclass,
   and `find_all_plans_in_repo` still raises it. In `tmux_dispatch._setup_has_plan` the
   `stack_entries` call sits under `except WorkflowError`, so a sequence-number collision now
   escapes as a traceback instead of a rendered error. (`stack_child` in the same block has the same
   pre-existing exposure — worth fixing both by widening that `except`.)

6. **`--onto` reference forms narrowed more than the plan implies.** `find_stack_member` matches the
   exact name or the zero-padded `sequence_number_str`, so `--onto 25` (unpadded) and any
   slug-only/partial reference, which the fuzzy resolver accepted, now fail with
   `--onto '25' does not resolve to a plan.` Cheap mitigation: zero-pad a purely numeric first
   segment before comparing. Otherwise document the exact-reference requirement in
   `docs/source/workflow.rst`'s stacking section.

## Nits

7. **Redundant fetches.** `_sync_repos_best_effort` → `refresh_main(builder)` already fetches
   immediately before `_merge_origin_main_into` fetches again, in both implement paths (the `--onto`
   path fetches builder up to four times per run: classify, checkout step, refresh_main, merge).
   Passing an `already_fetched` flag (the pattern `transfer_branch_to_main` already uses) would drop
   two round trips and shorten the log.

8. **State directory names hardcoded as strings.** `f"docs/plans/checking/{filename}"` /
   `f"docs/plans/implementing/{filename}"` bypass `PlanState.*.value`, which is the single source of
   truth everywhere else.

9. **`require(...)` + `assert ... is not None` for mypy** in `_resolve_onto_base_plan`: asserts are
   stripped under `python -O`. `if base_plan is None: raise WorkflowError(...)` narrows without the
   assert and is one line shorter.

10. **Resume-output ordering/wording in plain `implement`.** `_STEP_IMPLEMENT_MERGE_MAIN` is
    classified before `create <branch>` but executed after it, and in the fresh-branch path its
    reason (`"feature branch is being created fresh in this run"`) reads as "already done" for a step
    that is merely moot.

11. **Now-dead test fixture.** `tests/test_stack.py`'s autouse `_silence_tmux` exists to neutralise
    `resolve_plan`'s tmux source; `stack.py` no longer calls `resolve_plan`, so it can go (vulture
    already flags it).

12. **`find_stack_member` signature/loop.** `prefer_repo: str` could be `RepoName`, and the
    `[prefer_repo] if prefer_repo == RepoName.canon else [prefer_repo, RepoName.canon]` dance is
    `list(dict.fromkeys([prefer_repo, RepoName.canon]))`. The two-pass name-then-sequence match per
    repo is correct as written (name beats sequence within a repo).

## Notes, no action requested

- Preferring the referring plan's repo means member `Plan`s (hence `member.state`) now come from a
  possibly-stale clone. Harmless for the transition cascades (`transition_plan` relocates by name in
  `repo_override`) and arguably better for `_require_builder_not_on_unfinished_stack`, but it is a
  real semantic shift that the plan records only as a path-rendering benefit.
- `resolve_stack`'s `seen` set still mixes plan names with raw `onto` strings, so a cycle expressed
  via bare sequence numbers is caught by the depth cap rather than the cycle check. Pre-existing;
  the message it produces already says "check for a cycle".
- The plan's "one-off manual check" against the live 023.1/023.2 stack is still unrun (recorded in
  the plan's deviations). Someone with the real multi-repo project should run
  `workflow --dry-run ask 023.2` and `workflow status` before this is relied on there.
- Risk 4 in the plan (rejected members still cascading through `check`/`accept`) is untouched, as
  agreed — but finding 1 makes the rejected-base direction worse, not just unchanged, so the
  follow-up intent is now more urgent.
