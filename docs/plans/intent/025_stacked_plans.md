# 025: Stacked Plans

## Goal

Sometimes plans are quite fine-grained, building on top of each other.
While building them one at a time makes sense, individual review becomes wasteful.
The situation can occur e.g. due to `subdivide` or due to small fix intents.

## Current state

- `implement` always creates a new feature branch off main, named `feat/<plan name>`.
- Every command derives the branch name from the plan name (`feat/{plan.name}`).
- The `pr` prompt references exactly one input plan file.
- A plan's `todo -> implementing` transition is committed on `main` in `canon` and pushed,
  and the feature branch is cut from that commit, so the plan file is present on the branch.

## Desired state

One feature branch carries a **stack of plans**. `workflow implement PLAN_B --onto PLAN_A`
does not cut a new branch: it continues building on `PLAN_A`'s existing feature branch.

- The branch keeps the name it was created with, `feat/<root plan of the stack>`.
- `PLAN_A` stays in `implementing` while `PLAN_B` is built on top of it.
- `check PLAN_B` pushes the branch and moves only `PLAN_B` to `checking`.
- `pr PLAN_B` reviews the whole branch and its prompt lists every plan in the stack, in order.
- `accept PLAN_B` merges the branch and moves every plan in the stack to `done`, then deletes
  the single branch.
- Stacking onto an already stacked plan is permissible; the stack grows by one.

## Decisions

1. **One branch per stack, not a branch per plan.** `--onto` continues on the existing branch
   instead of cutting a child branch from it. Rationale: since no plan in a stack is ever reviewed
   or merged on its own, per-plan branches would be created, never independently reviewed, and
   deleted unmerged — while forcing ancestry preservation, extra cleanup and multi-branch guards.
   The one capability lost is aborting the top plan alone; that is handled by manually rolling the
   branch back (see decision 8).
2. **Flag name `--onto`**, mirroring `git rebase --onto` semantics: the argument names what the new
   work sits on top of.
3. **Base plan must be in `implementing`.** Once a plan reaches `checking`, its branch is published
   and the reviewer usually holds unpushed commits (the `pr` review document, `fix` commits) that the
   builder cannot see; continuing to build on that branch would silently diverge. Hard error
   otherwise, and also when the base's branch does not exist in `builder`.
4. **`--onto` must name the current tip of the stack.** If another non-rejected plan is already
   stacked onto `A`, `implement C --onto A` errors and names the tip to use instead. This keeps the
   stack a list, not a tree.
5. **Stacking is recorded in the plan file's frontmatter**: `onto: <base plan name>` in `PLAN_B`.
   The frontmatter is version-controlled and travels with the plan, unlike the audit log, which is a
   local scratch file. The full stack is derived by walking the `onto` chain; the branch name is
   derived from the root of the chain. This requires a frontmatter parser, which plan 023.6 also
   introduces — see the dependency note below.
6. **The audit log keeps a resolved `stacked_on` field too** (full stack, base-first) on the
   `implement` entry, so `info`/`status`/`log` can report stacks without parsing plan files.
   The plan files remain the source of truth; the log is an echo for reporting.
7. **Plan-file transitions stay on `main` in `canon`, as today.** After the transition is pushed,
   `implement --onto` merges `origin/main` into the feature branch in `builder`, so `PLAN_B`'s file
   is present under `implementing/` on the branch and `check` can move it. The merge commit is
   acceptable: with a single branch there is no ancestry invariant to preserve, and `accept`'s
   existing ff-then-rebase path still applies.
8. **`reject PLAN_B`** transitions only `PLAN_B` and warns that its commits are still on the branch,
   so the operator must roll the branch back manually before continuing with the rest of the stack.
   No cascade, no automatic branch surgery.
9. **Mid-stack guard.** `check A` and `accept A` hard-error while a non-rejected plan is stacked
   onto `A`, naming the tip plan to use instead. Prompt commands aimed at a mid-stack plan (`pr`,
   `fix`) error the same way. Once the plan stacked onto `A` is rejected, `A` becomes actionable
   again.
10. **`accept` cascades.** Accepting the tip transitions every plan in the stack to `done`, including
    plans that are still in `implementing`. That edge (`implementing -> done`) does not exist in the
    configured state machine, so the cascade must be explicit and visible in the audit log rather
    than a silent file move.
11. **Surfacing.** `status` shows stack membership per plan, `log` renders `stacked_on`, and the
    next-step reminders mention the stack.
12. **Sequencing.** Plan and build against the current API (`RepoName`, `project_dir / repo`),
    landing before the 023.x out-of-band series; the 023.2 refactor will sweep this code along.

## Dependency note: frontmatter

Plan 023.6 (in `todo`) introduces `workflow/frontmatter.py` with a `PlanFrontmatter` model carrying a
`branches` key. This plan needs the same parser earlier, for a different key (`onto`). Resolution:
025 introduces `workflow/frontmatter.py` with `PlanFrontmatter(onto: str | None)` and `extra="allow"`;
023.6 extends the same model with `branches`. 023.6 must be updated to reflect that the module
already exists.

## Rejected approach: a branch per plan

The first version of this intent had `--stacked` cut `feat/B` off `feat/A`, keeping one branch per
plan; `accept B` would then merge `feat/B` and delete `feat/A` as well, "if it can be done without
losing commits". Rejected because the child branch has no independent life cycle: it is never pushed
for its own review, never merged on its own, and only ever deleted. The cost was substantial —
`feat/A` had to remain an ancestor of `feat/B` (so `origin/main` had to be merged rather than
rebased), branch cleanup needed ancestry or patch-equivalence probes, and `check`/`accept` needed
guards for every branch in the stack.
