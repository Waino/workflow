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
one commit behind. The next `plan/*` branch is cut from that stale `main`, and the `--ff-only` merge
of *that* branch fails. The same off-by-one applies to `implement` and `accept`.

Caveat: this is inference, not a confirmed reproduction. With planner on `main` one commit behind,
the immediately following `todo` should *not* fail (its `git push` is a no-op and the canon merge is
a no-op main-into-main); the failure should only surface once a new branch has been cut from the
stale `main`. The observed failure came from a sequence of `todo`s whose exact order is no longer
certain. The plan must therefore start by building a reproduction and turning it into a regression
test, and adjust the fix if the reproduction contradicts this diagnosis.

A related staleness source: `push` (canon -> bare, pull planner) pulls the planner repo while planner
is usually on a `plan/*` branch, so planner's local `main` never advances at all.

## Diagnosis of the `reject` defect

(Supersedes an earlier, incorrect audit note in this file claiming that `reject` never reaches
`main`.) `reject` defaults to `--target-repo canon`, transitions the plan file in canon on `main`,
commits and pushes -- so the transition does reach `main`. The real defect is a path mismatch:
`transition_plan` derives the *source* path from the resolved plan's state directory, but the
resolved state comes from whichever repo the resolver found the plan in. For a plan in `checking`,
the resolver reports `checking/` (the transition to `checking` was committed on the feature branch),
while canon's `main` still has the file under `implementing/`. The `git mv` therefore targets a
non-existent path and `reject` fails outright in its default invocation.

## Decisions

1. **Transitions must locate the source file in the repo they operate on.** Fix `transition_plan` to
   find the plan file by name under `docs/plans/*/` *in the target repo*, instead of trusting the
   resolved plan's state directory. Applied to all transitions, not just `reject`, so there is only
   one way to construct a transition.
2. **`reject` must not merge the feature branch.** Rejection means the work is unwanted; an
   `--ff-only` merge of `feat/{plan}` would drag the entire abandoned implementation into `main`.
   `reject` transitions the plan file in canon on `main`, commits, pushes, and leaves the feature
   branch unmerged. `001_idea.md`'s "Reject" section (which specified push + merge to main) is
   updated accordingly -- it predates the feature-branch design.
3. **One shared sync helper, with best-effort parking.** Replace both the tail of
   `transfer_branch_to_main` and the ad-hoc pulls with:
   - `refresh_main(repo)`: bring the repo's `main` (and `origin/main`) up to date without touching
     the working tree -- `git pull --ff-only` when `main` is checked out, otherwise
     `git fetch origin main:main`.
   - `park_on_main(repo)`: check out `main` and refresh it when the tree is clean; otherwise warn and
     fall back to `refresh_main` alone.
   Callers park the source repo *after* the canon push (fixing issue 1), and refresh `main` in every
   repo they touch. This subsumes the earlier separate decisions about `sync_source_repo` and about
   `push` fetching `main:main`.
4. **The rebase fallback lives in `transfer_branch_to_main`, so it covers all its callers**
   (`todo`, `implement`, `accept`), not just `accept`. The planner side hits the identical failure
   whenever an intent is pushed or another plan transitions while a `plan/*` branch is open.
5. **The real `git rebase` is the sole gate; `merge-tree` is only used for error messaging.**
   `git merge-tree --write-tree` answers a different question than "will this rebase apply cleanly"
   and can disagree in both directions; using it as a gate risks refusing a rebase git would have
   completed. It is still worth running on failure to name the conflicting paths in the error output.
6. **Safeguards on the rebase path** (all required): the source repo's tree must be clean; the branch
   must be the one checked out in the source repo; the branch must not be checked out in any other
   agent repo; nothing is rebased or force-pushed in `--dry-run`; the force-push uses
   `--force-with-lease`. Force-pushing follows "only the human operator pushes" -- it is the
   human's script doing it, never an agent.
7. **Re-verify after a rebase.** When (and only when) a rebase actually happened, re-run the
   pre-commit hook via the docker script against the source repo *before* canon merges the branch.
   The rebase path is rare, so the cost is negligible, and merging a tree nobody has built would
   defeat `accept`'s role as the last quality gate.
8. **Recovery prompt: a generic `rebase` command** in both `checking` and `planning`. The agent only
   rebases and runs the pre-commit hook -- it must not be told to push, because agents have no write
   access to `bare`. The next steps are the human's: review the rebase, run
   `workflow validate PLAN --target-repo <repo>`, and re-run the transition (which force-pushes).
9. **Mount `bare` read-only into agent containers.** `docs/example_run_agent_in_docker.sh` currently
   does not mount `bare` at all, so `git fetch`/`rebase origin/main` cannot work inside a container.
   A read-only mount enables fetch and rebase while making push fail by construction, which is the
   enforcement we want.
10. **Delete the feature branch on `accept`** (local copies in reviewer/builder via `git branch -d`,
    and the remote branch on `bare`), only after confirming it is merged into `main`. Failures here
    are warnings, not errors: the accept has already landed.
11. **Add staleness checks to `workflow doctor`**: each repo's `main` versus `bare`'s `main`, and
    whether an active feature branch is based on `bare`'s current `main` (informational -- a
    rebase-pending branch is normal during parallel work).
12. **Surface the rebase in the resume preview** so `--dry-run` reports the most surprising action the
    tool can take before it happens.

### Audit notes (parallel/interleaved work)

- `check` is safe: it never merges to `main`, and it pulls builder's `main` at the end, so no
  staleness accrues there.
- `implement` cuts the `feat/*` branch after pulling builder's `main`, so it is correct once the
  upstream staleness (decision 3) is fixed.
- Rebasing rewrites hashes, so `docs/plans/reviews/pr/{plan}_{commit}.md` filenames may reference a
  commit that no longer exists in `main`'s history. Cosmetic; accepted.
