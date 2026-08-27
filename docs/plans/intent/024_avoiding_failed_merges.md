# 024: Avoiding failed --ff-only merges

## Issue 1: todo and implement leave planner stale

There is a common workflow, where planner creates multiple plans (either due to `mvp` or `subdivide`).
Those plans are worked on as a whole, culminating in accepting multiple of them using `todo`.
The `todo` command pushes the transition to bare, but does not pull to planner.
The next `todo` then calls `transfer_branch_to_main(planner_repo, canon_repo)`,
which fails as planner is behind.
The workaround is to manually pull in planner between each `todo`.
The fix automates this pull as part of the previous `todo`, making planner aware of plans it has handed off.

The same issue and fix applies to `implement`.

## Issue 2: accept fails if main has progressed

The third caller of `transfer_branch_to_main` is `accept`, transferring from reviewer to canon.
If any commit has been added to main since the feature branch was created, the `--ff-only` will fail.
There are a few common cases where this failure is unexpected:
- A new intent is added
- Any plan is transitioned.
Out-of-order acceptance of built plans is not unexpected, and properly requires an explicit rebase.

The procedure for `accept` should be:
- ff-merge if possible
- else rebase the feature branch onto the current main if this can be done cleanly
- else fail fast
- the recovery is having reviever do the rebase (let's add a prompt `rebase-before-accept`)

### Open questions (resolved, see Decisions):

- Can we cheaply test whether a merge will be clean, or do we need to try and abort on non-ff/conflict?
- Are there other obvious points where the git dance is expected to fail if plans are worked on in parallel,
  interleaved in different ways? See e.g. `intent/010_pipeline_parallelism.md'.

## Diagnosis of issue 1 (more specific than the description above)

`transfer_branch_to_main` already ends with `checkout main` + `pull` in the source repo, so the
missing-pull framing is not quite right. The bug is ordering: that pull runs *before* canon commits
and pushes the state-transition commit ("Transition X to todo"). Planner's `main` therefore ends up
exactly one commit behind. The next planner branch is cut from that stale `main`, and the `--ff-only`
merge of that branch fails. The same off-by-one applies to `implement` and `accept`.

A related staleness source: `push` (canon -> bare, pull planner) pulls the planner repo while planner
is usually on a `plan/*` branch, so planner's local `main` never advances at all.

## Decisions

1. **Split the sync tail out of `transfer_branch_to_main`.** The helper does push -> pull -> merge ->
   push only. A new helper (`sync_source_repo`: checkout `main`, pull) is called by `todo`,
   `implement`, and `accept` as their *last* step, after the canon push. This fixes issue 1 for all
   three commands in one place instead of appending a redundant second pull per command.
2. **The rebase fallback lives in `transfer_branch_to_main`, so it covers all three callers**, not
   just `accept`. The planner side hits the identical failure whenever an intent is pushed or another
   plan transitions while a `plan/*` branch is open. The fail-fast error message names the recovery
   command appropriate to the plan's state.
3. **Rebase mechanism: belt-and-suspenders.** Require a clean worktree; pre-test with
   `git merge-tree --write-tree` (git >= 2.38; 2.43 is available here); then perform a real
   `git rebase origin/main` in the source repo, with `git rebase --abort` and fail-fast on conflict.
   The pre-test alone is not sufficient (merge cleanliness != per-commit rebase cleanliness), and the
   real rebase alone leaves a dirtier failure path -- hence both. After a successful rebase the branch
   must be force-pushed with `--force-with-lease` before canon can ff-merge it.
4. **No re-verification after a clean rebase**; print a loud warning that the branch was rebased.
   `accept` already assumes the human ran `workflow validate`, and re-running the pre-commit hook
   would require a `docker exec` that `accept` does not currently do. A clean rebase can still break
   things semantically -- document this as a risk.
5. **Recovery prompt is a generic `rebase` command**, available in both `checking` and `planning`
   (rather than the reviewer-only `rebase-before-accept` named in the original text), because the
   fallback now applies to planner as well.
6. **`push` also runs `git fetch origin main:main` in the pull target**, updating the agent's
   non-checked-out `main` without disturbing its feature branch. This prevents branches from being
   cut off a stale `main` in the first place, keeping the rebase path rare.
7. **Fold the `reject` fix into this plan.** Audit finding: `reject` transitions the plan and pushes
   the agent's branch, but performs no canon pull/merge/push. A rejected plan therefore still shows as
   `checking` on `main`, and the reject commit sits on an orphaned feature branch. This contradicts
   the `reject` spec in `001_idea.md` (push, pull to canon, transition, merge to main, push). Bring
   `reject` in line with `accept` (same transfer + sync + rebase-fallback path).

### Audit notes (parallel/interleaved work)

- `check` is safe: it never merges to `main`, and it pulls builder's `main` at the end, so no
  staleness accrues there.
- `implement` cuts the `feat/*` branch after pulling builder's `main`, so it is correct once the
  upstream staleness (decisions 1 and 6) is fixed.
- Rebasing rewrites hashes, so `docs/plans/reviews/pr/{plan}_{commit}.md` filenames may reference a
  commit that no longer exists in `main`'s history. Cosmetic; accepted.
