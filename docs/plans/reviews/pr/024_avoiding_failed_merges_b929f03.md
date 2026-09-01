# PR review: 024 — avoiding failed `--ff-only` merges

Branch: `feat/024_avoiding_failed_merges` @ `b929f03` (7 commits, diff vs `origin/main`)
Plan: `docs/plans/checking/024_avoiding_failed_merges.md`
Reviewer: reviewer agent (recall-oriented review; code not modified)

## Verdict

The plan is implemented faithfully, phase by phase, and the deviations are documented in the plan
file. Quality gates pass on my checkout: `pytest` 405 passed, `ruff check` clean, `mypy workflow
tests` clean, `black --check` clean.

Two findings should be addressed before merge: the unconditional force-push fallback in the
fast-forward path (F1, data-loss vector) and `doctor`'s new stale-main anomaly, which will fire
routinely at rest and flip `doctor`'s exit code (F2). The rest are medium/low.

## High

### F1 — `transfer_branch_to_main`'s fast-forward path force-pushes on *any* push failure

`workflow/git_helpers.py`:

```python
try:
    git_push(source_repo, dry_run=dry_run)
except subprocess.CalledProcessError:
    if dry_run:
        raise
    run_command(["git", "push", "--force-with-lease", "origin", branch], cwd=source_repo)
```

Three problems compound here:

1. **The `except` is overbroad.** Any non-zero `git push` — transient network failure, a rejected
   push of a *different* ref, a server-side hook — is treated as "must be the resume-after-rebase
   case" and answered with a force-push.
2. **`--force-with-lease` gives no protection here**, because `branch_needs_rebase()` ran
   `git fetch origin` moments earlier. The default lease is the just-updated
   `refs/remotes/origin/<branch>`, so the lease always matches the remote and the push always
   succeeds — including when `origin/<branch>` has commits the local branch does not (they are
   silently dropped from bare). This is the classic "force-with-lease after fetch is not safe"
   pitfall, and it sits in exactly the place the plan flagged as risk 1.
3. **The pushed ref differs between the two attempts.** `git_push(source_repo)` is
   `git push origin` with no refspec, i.e. it pushes *the current branch* (which for `accept` need
   not be `branch`), while the fallback explicitly force-pushes `branch`. A rejected push of
   `main` (source repo parked on main, local `main` behind) therefore triggers a force-push of the
   feature branch — a failure of one ref answered by rewriting another.

The scenario the fallback exists for (rebased locally, force-push failed) cannot be distinguished
by ancestry, but it *can* be distinguished by patch-equivalence. Suggested fix: push the explicit
refspec in both attempts, and gate the force on "nothing unique on the remote":

```python
def _remote_commits_are_all_local(repo: Path, branch: str) -> bool:
    """True when every commit unique to origin/<branch> has a patch-equivalent on <branch>."""
    out = run(["git", "cherry", branch, f"origin/{branch}"])  # '+' == missing locally
    return not any(line.startswith("+") for line in out.splitlines())
```

and only force when that holds (otherwise raise, naming the divergence). Add a test with a remote
branch carrying a commit absent locally, asserting the push is *refused*.

### F2 — `doctor`'s "main is stale" anomaly will fire at rest under normal use

`_check_staleness` flags any difference between a worktree's `refs/heads/main` and bare's, and
anomalies drive `doctor`'s non-zero exit. But this branch only refreshes the repos each command
touches:

| command | repos whose `main` is refreshed |
| --- | --- |
| `todo` | planner, canon |
| `implement` | planner (if transfer ran), builder, canon |
| `check` | (unchanged) reviewer/builder pulls only |
| `accept` | reviewer, builder (via `delete_merged_branch`), canon |
| `push` | canon, `pull_target` |

So after a plain `todo`, builder's and reviewer's `main` are one commit behind bare and `doctor`
reports two anomalies and exits 1 — at a perfectly healthy rest state. The plan's premise ("after
this plan's fixes this should never happen at rest") only holds for the repos involved in the last
handoff. Corroborating evidence inside the diff: `test_doctor_cli_exits_zero_on_clean_setup` had to
be changed from `_init_repo` to real clones of bare to keep passing.

Options: (a) demote stale-main to `notes` (it is recoverable and self-healing on the next command),
(b) only treat it as an anomaly for canon, or (c) refresh all four repos' `main` after every
handoff. (a) or (b) is the small change; (c) is the behaviour the anomaly assumes.

Related: the message says "stale" but the check is symmetric — a worktree with an unpushed local
commit on `main` (interrupted run) also reports "main is stale". Reword to
"main differs from bare (stale or unpushed)".

## Medium

### F3 — cosmetic post-handoff steps can fail the whole command

`park_on_main`/`refresh_main` are documented as never failing on a dirty tree, but they still raise
`CalledProcessError` when the underlying git call fails — e.g. `git fetch origin main:main` refuses
a non-fast-forward update, `git pull --ff-only` on a diverged `main`. In `todo`, `implement`,
`accept` and `push` these run *after* the transition commit has been pushed, and the surrounding
`except` turns them into `exit 1`. The operator sees a failed command for work that fully landed —
the exact failure mode `_rotate_best_effort` exists to avoid. Wrap these calls the same way
(`_park_best_effort`), or catch inside `refresh_main`/`park_on_main` and warn.

### F4 — a failed `post_rebase_verify` is not re-checked on the rerun

If the pre-commit hook fails after the rebase, nothing is pushed (good, and tested). But the rerun
takes the fast-forward path — `branch_needs_rebase` is now satisfied — and pushes the rebased branch
via the F1 fallback **without running the hook again**. The rebase result that was explicitly
rejected as unverified gets published by the retry. Suggest running `post_rebase_verify` on the
force-push fallback branch too (it is only reached in the post-rebase resume case anyway).

### F5 — `transition_plan` now inherits repo-wide `PlanCollisionError`

`find_plan_file_in_repo` → `find_all_plans_in_repo` → `_check_collisions`, so an *unrelated*
sequence-number collision anywhere in the target repo (two agents both creating `025_*`) now blocks
every transition in that repo, not just resolution of the colliding plans. Previously transitions
were path-local. This may be acceptable strictness, but it is an undocumented behaviour change with
a confusing error surface; consider scoping the scan to the single basename, or catching
`PlanCollisionError` and re-raising with "cannot transition while … collides".

### F6 — the rebase path of `todo`/`implement` has no end-to-end coverage

Only `accept` gets real-git rebase integration tests (2 in `test_git_helpers.py`, 4 in
`test_integration_workflow.py`). For `todo`/`implement`, only the `--dry-run` preview is tested.
That leaves the `post_rebase_verify=lambda: _run_pre_commit_hook(config, RepoName.planner, …)`
wiring (repo argument, planner vs. reviewer) completely unexercised — a wrong repo name here would
run the hook against the wrong clone and no test would notice. The plan's own Deviations section
names the concurrently-cut sibling `plan/*` branches as the case that *requires* the rebase
fallback; that case is not integration-tested for `todo`.

### F7 — `_classify_implement_steps` unconditionally touches planner

`git_current_branch(planner_repo)` is evaluated even when `transfer_will_run` is `False`, so an
`implement` on a `todo`-state plan now hard-fails if the planner clone is missing/invalid, where
before it never touched planner. Compute the branch lazily inside the `transfer_will_run` arm.

### F8 — `--dry-run` can fail on guards it cannot possibly satisfy

`_rebase_onto_main` runs all four safeguards *before* the `if dry_run:` early return. A dry run of
`todo` while the planner has uncommitted work (the normal state mid-planning) therefore aborts with
"has uncommitted changes" instead of previewing. Consider printing the guard violations as part of
the preview and returning `REBASED`, rather than raising, when `dry_run` is set.

### F9 — `--dry-run accept` prints a false cleanup warning

In dry-run the transfer never lands, so `delete_merged_branch`'s ancestry probe fails and it prints
`[!] feat/x is not (yet) known to be merged into main; skipping branch cleanup` — alarming noise in
a preview. Skip the warning (or the whole call) when `dry_run` is set.

## Low / nits

1. `docs/source/cli.rst` is not updated: the new `rebase` prompt command is missing from the prompt
   list; `accept` no longer just "merge to main, push" (it may rebase and it deletes the feature
   branch locally and on bare); `reject` still documented as "merge to `main`, push" although it
   explicitly does not merge and now prints an unmerged-branch note; `doctor`'s new notes column.
   `docker.rst` and the generated `AGENTS.md`/fixture *are* updated.
2. `test_todo_fails_when_second_branch_cut_after_first_todo` now asserts success — rename to
   `..._succeeds_...` (the docstring explains it, the name contradicts it).
3. `git merge-tree --write-tree` requires git ≥ 2.38 and, being a real merge, writes tree/blob
   objects into the source repo. On older git the conflict list silently comes out empty (message
   degrades, no crash) — worth a one-line comment; the object writes are worth mentioning next to
   the "read-only guard" module docstring.
4. Conflict reporting covers only the first conflicting replayed commit (`REBASE_HEAD`); fine, but
   the docstring's "collect conflicting paths" reads as if it were exhaustive.
5. `delete_merged_branch` re-runs `refresh_main(reviewer_repo)`, which `accept` just did via
   `park_on_main`; and when `origin/<branch>` is already gone it returns early, leaving stale local
   `feat/*` branches behind in reviewer/builder (which `doctor` then flags as a stray branch).
6. `_other_worktrees` docstring says "the other three agent repos plus canon"; it actually returns
   the other three of the four repos (canon included in that count).
7. Dead branch: `if dry_run: raise` inside the fast-forward push `except` is unreachable —
   `git_push` never executes in dry-run, so it cannot raise.
8. Double `git fetch origin` per command: once in `_classify_rebase_step` →
   `branch_needs_rebase`, once inside `transfer_branch_to_main`. Passing the classification result
   (already threaded through as `allow_rebase`) or caching would halve it.
9. `test_refresh_main_warns_and_returns_when_no_origin_remote` asserts nothing — capture and assert
   the warning, otherwise it only proves "does not raise" (which the comment claims but the name
   promises more).
10. Several tests use `del bare` to silence an unused return from `_setup_transfer_scenario`;
    unpacking into `_bare` (or returning a small named tuple) would read better.
11. `require(allow_rebase, …)`'s message ("rerun with the rebase step enabled (`workflow rebase
    …`)") conflates a CLI rerun with dispatching the `rebase` *prompt* to an agent; and the branch
    is effectively unreachable, since classification sets `allow_rebase` from the same probe
    (reachable only on a race where `main` advances between classify and run). Worth a comment.

## What is good

- Phase 0 was actually done, and the plan records that the literal predicted sequence did *not*
  reproduce, with the sequence that does — plus the reasoning for swapping the concurrent-cut
  variant out of the Phase 2 regression test.
- The `REBASE_HEAD`-vs-`HEAD` discovery for `git merge-tree` is a genuine subtlety, verified by a
  real conflicting rebase rather than a mock.
- The rewrite of `test_transfer_branch_to_main_runs_steps_in_order` into real-git scenarios is the
  right call and materially raises confidence in this diff.
- Dry-run non-mutation is asserted by before/after SHA comparison in both `todo` and `accept`, as
  the plan demanded.
- `_classify_accept_steps` correctly stops trusting a dangling `origin/<branch>` ancestry check once
  the branch has been cleaned up — an easy bug to miss when adding branch deletion.

## Fixes applied

All findings above were addressed except F8 (a `--dry-run` preview aborting with "has uncommitted
changes" was accepted as-is) and low 5/6/7/10/11 (deliberately left). The decisions taken while
fixing — the `--force-validation`/`--force-push` flag split, the patch-equivalence gate on every
force-push, and syncing all four clones' `main` after every handoff — are recorded in
`docs/plans/checking/024_avoiding_failed_merges.md` under "Post-review fixes (round 1)".
