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

### Open questions:

- Can we cheaply test whether a merge will be clean, or do we need to try and abort on non-ff/conflict?
- Are there other obvious points where the git dance is expected to fail if plans are worked on in parallel,
  interleaved in different ways? See e.g. `intent/010_pipeline_parallelism.md'.
