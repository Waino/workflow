# 025: Stacked Plans

## Goal

Sometimes plans are quite fine-grained, building on top of each other.
While building them one at a time makes sense, individual review becomes wasteful.
The situation can occur e.g. due to `subdivide` or due to small fix intents.

## Current state

- Currently `implement` always creates a new feature branch off main.
- Currently the `pr` prompt references exactly one input plan file.

## Desired state

- Add a flag `--stacked` that instead creates the new feature branch from an existing feature branch.
    - For example, `workflow implement PLAN_B --stacked PLAN_A` creates the branch for `PLAN_B`
      by cutting a branch off the (already built) feature branch of `PLAN_A`
- `pr` needs to be aware of the stacking.
    - When running `workflow pr PLAN_B`, the prompt should include all the plans that are part of the stack.
    - This means stacking needs to be tracked in the audit log. Store as a list of sequence number + slug.
- `accept PLAN_B` merges the feature branch of `PLAN_B`, which includes the commits for `PLAN_A`.
    - The feature branch for `PLAN_A` should be deleted at the same time as the one for `PLAN_B`,
      if it can be done without losing commits.

## Stacking more than once

- It is permissible to stack a plan on top of an already stacked plan. The stack is inherited.
