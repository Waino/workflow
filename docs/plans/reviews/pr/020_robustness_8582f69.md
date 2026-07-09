# PR Review: 020_robustness (commit 8582f69)

Branch: `feat/020_robustness` vs `origin/main` (0164ebf)
Plan: `docs/plans/checking/020_robustness.md`
Scope reviewed: full diff (12 files, +1812 / -239).

## Verdict

Solid, well-structured implementation that follows the locked design decisions closely.
Preconditions, read-only guards, dry-run behavior, and the two-pass `ResumePlan` all match
the plan. Tests are broad and pass (356 passed; black + mypy --strict clean).

However there is **one high-severity correctness bug in `doctor`** that makes it emit false
anomalies (and exit non-zero) on essentially every real project. Plus a couple of
medium/minor items. Details below.

---

## High severity

### 1. `doctor` duplicate-plan detection flags intents and reviews as duplicates

`_duplicate_plan_anomalies` (workflow/commands/doctor.py) groups **all** plans returned by
`find_all_plans_in_repo` by `sequence_number_str:slug` and flags any key that appears in more
than one state dir. But `find_all_plans_in_repo` also returns:

- **intent** files — which by spec share the *same* number and slug as their derived plan
  (1:1 intent→plan mapping), and are never moved out of `intent/`; and
- **review** documents (`reviews/critique`, `reviews/pr`) — whose parsed slug equals the
  reviewed plan's slug.

So a plan legitimately co-existing with its originating intent and/or its PR/critique review
is reported as "appears in multiple state dirs". This is not the botched-`git-mv` signature
the check is meant to catch; it is the normal, healthy steady state.

Reproduced against this very repo (as `canon`):

```
canon: 012:plan_resolution_respec appears in multiple state dirs: done, intent, review
canon: 002:models_and_config appears in multiple state dirs: done, review
canon: 020:robustness appears in multiple state dirs: checking, intent
... (one line per plan that has an intent or a review)
```

Because `doctor` exits non-zero when any anomaly is found, its scriptable contract is broken:
on any mature project it will always report failure. This defeats the command's purpose.

**Fix:** restrict duplicate detection to the pipeline states only
(`planning, todo, implementing, checking, done, rejected`) — exclude `INTENT` and any
`Review`/`state == REVIEW` entries before grouping. (`_active_feature_branches` is unaffected:
it filters on `IMPLEMENTING/CHECKING`, which excludes intents and reviews already.)

**Test gap that hid this:** `test_doctor.py` never places an intent or review file alongside a
plan. Add a case with a `reviews/pr/NNN_slug_hash.md` (and an `intent/NNN_slug.md`) present and
assert no duplicate anomaly is raised.

---

## Medium severity

### 2. Preview (classification) and execution are separate code paths; `should_run` is unused

`ResumePlan.should_run` — the API the plan's two-pass design uses to gate execution — is never
called in production code (only in `test_steps.py`). Each command classifies in
`_classify_*_steps` for the preview, then **re-derives** the same conditions independently in
`_run_*_steps`. This duplicates the postcondition logic (exactly the "postcondition correctness
is the whole ballgame" risk in the plan) and lets the printed preview drift from actual behavior.

Concrete divergences where the `[skip]` preview overstates what happens:

- **init "wire canon to bare":** classified `satisfied` when `origin` already exists, but
  `_run_init_steps` unconditionally calls `_wire_canon_to_bare`, which always runs
  `git push -u origin HEAD`. If canon is ahead of bare, this mutates bare despite the `[skip]`.
- **init "AGENTS.md committed":** classified `satisfied` when unchanged, but execution always
  regenerates the file and `git add`s it (commit then no-ops). Working tree is still touched.

Functionally low-risk today (the operations are idempotent), but decision 8 ("resume preview
before mutating") is only as trustworthy as the preview's accuracy. Recommend either driving
`_run_*` off `resume.should_run(name)` (single source of truth), or explicitly documenting the
preview as advisory and re-deriving. Watch this before the pattern spreads to more commands.

### 3. Inlined transfer in `implement` drops two safety guards from `transfer_branch_to_main`

`_run_implement_steps` inlines the planning→main transfer (per plan, to make each git op
guardable) instead of calling `transfer_branch_to_main`. But the helper had two assertions the
inline version omits:

- source repo not on `main` ("expected a feature branch"), and
- **canon on `main`** ("won't clobber a feature branch").

If canon is unexpectedly left on a feature branch, the inlined path would
`git merge origin/<planner-branch>` into it and `git push` — the exact clobber the helper
guarded against. Low probability, but it is a real safety net that silently disappeared on the
`implement` path. Recommend re-adding at least the canon-on-`main` check (the `accept` command
already asserts this; `implement` should too).

---

## Minor / nits

- **`check` resume runs pre-commit + validate against builder even after builder moved to
  `main`.** Steps 1–2 are `always` flavor and run before the transition check; on a resume where
  a prior run already advanced builder to `main`, the sandbox pre-commit hook and validate run
  against `main` rather than the feature branch. Harmless in practice but not the intended target.
- **`git_status_porcelain` `subprocess.run` lacks the `# noqa: S603`** that its sibling helpers
  carry (cosmetic; bandit currently passes).
- **`_transition_if_needed` relies on `git_commit_if_changed`, which does not `git add -u`.** In
  the normal and interrupted-after-`git mv` paths the rename is already staged, so this is fine;
  but a move present only in the working tree (unstaged) would silently no-op the commit. Edge
  case, worth a one-line comment noting the "must be pre-staged" assumption at the call site.

## Positives

- Read-only git helpers correctly bypass dry-run; mutating helpers keep it. Clean separation,
  well-documented in the module docstring.
- `start` control-pane semantics (attach vs. create, `--reset` self-kill refusal) match
  decision 9; `test_start_reset_refuses_self_kill` covers it.
- `init` bare-corruption precondition + test present and actionable.
- Resume/no-op tests for init/check/accept/implement, and the dry-run-preview-without-mutation
  test, all present and meaningful.
- Recorded deviations (plan_resolver ambiguity, doctor scope) are honest and appropriately scoped.

## Suggested follow-ups

1. Fix finding #1 (pipeline-states-only in `_duplicate_plan_anomalies`) + add the intent/review
   regression test. This is merge-blocking: `doctor` is currently unusable as a pass/fail gate.
2. Decide on #2: consult `should_run` in execution, or document the preview as advisory.
3. Re-add the canon-on-`main` guard to `implement` (#3).
