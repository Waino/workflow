# 024: Avoiding failed --ff-only merges

Input: `docs/plans/intent/024_avoiding_failed_merges.md`, `docs/plans/intent/001_idea.md`
Completeness: complete (large — five phases, each independently committable; may be split at phase
boundaries if the diff grows unwieldy)

## Goal

Cross-repo handoffs (`todo`, `implement`, `accept`) merge a branch into `main` with `--ff-only`. That
merge fails whenever `main` has moved on since the branch was cut — which happens routinely under
normal parallel use: a new intent is pushed, or another plan transitions. Two concrete defects make
this worse than it needs to be:

1. The source repo's `main` is refreshed *before* canon pushes the transition commit, so it is left
   one commit behind after every handoff. Branches are then cut from a stale `main` and are
   guaranteed to fail the next `--ff-only`.
2. Nothing recovers from a legitimately behind branch — the command just fails, and the human has to
   hand-rebase.

Plus one unrelated defect found while auditing: `reject` fails outright in its default invocation
because it looks for the plan file in the wrong state directory.

Outcome: handoffs succeed under interleaved work; when they cannot, they rebase automatically and
verifiably, or fail fast with an actionable recovery path.

## Scope

In scope: `workflow/git_helpers.py`, `workflow/state.py`, `workflow/plan_resolver.py`,
`workflow/commands/workflow.py`, `workflow/commands/doctor.py`, `workflow/tmux_dispatch.py`,
`docs/default_config.yaml`, `docs/example_run_agent_in_docker.sh`, docs, tests.

Out of scope: changing the branch model, the `check` command (audited clean), any change to how
plans are resolved, and merge strategies other than fast-forward-or-rebase (no merge commits ever).

## Phase 0 — reproduce first

The diagnosis of issue 1 is inference, not a confirmed reproduction (see the intent's caveat).
Before writing any fix:

1. Build an integration-test reproduction in `tests/test_integration_workflow.py`, reusing the real-git
   fixtures already there (`_init_canon_repo`, `_drive_pipeline_through_check`, and the
   `--no-verify`-free commit stand-ins). Drive: `todo` plan A → push a new intent from canon →
   planner creates a branch and a plan → `todo` plan B, and assert the failure.
2. If the reproduction does not fail as predicted, find the sequence that does (candidates: two
   `todo`s on separate planner branches; `todo` followed by `implement`; a `push` interleaved) and
   record the actual mechanism in this plan before continuing.
3. Keep the reproduction as a regression test; it must pass at the end of Phase 2.

Do not skip this. Everything else is designed against this diagnosis.

## Phase 1 — transitions locate their own source file

`transition_plan` (`workflow/state.py`) builds the `git mv` source from `plan.state`, but `plan.state`
comes from whichever repo the resolver found the file in. With `repo_override="canon"` the two can
disagree — for a `checking` plan, canon's `main` still has the file under `implementing/`, so
`reject`'s default invocation dies on a `git mv` of a non-existent path.

Changes:

- Add `find_plan_file_in_repo(project_dir, repo_name, plan_file_name) -> Path | None` to
  `workflow/plan_resolver.py` (it already knows how to walk `docs/plans/<state>/`; reuse
  `find_all_plans_in_repo` rather than re-globbing). Excludes `reviews/`. Raise `WorkflowError` when
  the same basename exists under more than one state dir in that repo — `doctor` already treats that
  as an anomaly, and silently picking one would corrupt state.
- `transition_plan` resolves its source through that helper and raises a clear `WorkflowError` when
  the file is absent: `"{name} not found in {repo}; push it to main first (workflow push {name}) or
  pass --target-repo"`. Watch for an import cycle (`state` → `plan_resolver`); if one exists, move
  the helper into `plan_resolver` and import it lazily inside the function, or relocate it to a small
  shared module — do not duplicate the walk.
- `reject` (`workflow/commands/workflow.py`) needs no other change for this: with the lookup fixed,
  the default `--target-repo canon` path transitions the file in canon on `main`, commits and pushes.
- `reject` must **not** merge the feature branch (this contradicts the old `001_idea.md` text, which
  has been corrected). Add a printed note that `feat/{plan}` is left unmerged and can be deleted
  manually — deleting an unmerged branch automatically is destructive and is not done here.

Tests: unit tests for the lookup helper (found / absent / duplicated), and an integration test
rejecting a plan in `checking` through the default canon path (currently failing).

## Phase 2 — one sync helper, called after the canon push

New in `workflow/git_helpers.py`:

```python
def refresh_main(repo_path: Path, *, dry_run: bool = False) -> None
def park_on_main(repo_path: Path, *, dry_run: bool = False) -> None
```

- `refresh_main`: if `git_current_branch(repo_path) == "main"` → `git pull --ff-only`; else →
  `git fetch origin main:main`. The refspec form updates `refs/heads/main` without touching the
  working tree or HEAD, and git opportunistically updates `refs/remotes/origin/main` as well —
  **assert that opportunistic update in a test**, because the rebase path targets `origin/main`.
- `park_on_main`: if the tree is dirty (`git_status_porcelain`) → warn via `error_console` and call
  `refresh_main`; else check out `main` (skip if already there) and `refresh_main`. Never fails on a
  dirty tree: the caller has already completed a successful handoff by the time this runs.

Remove steps 5–6 (`checkout main` + `pull` in the source repo) from `transfer_branch_to_main` and
update its docstring. Call sites, each *after* the command's final `git_push(canon_repo)`:

| command | call |
| --- | --- |
| `todo` | `park_on_main(planner_repo)` |
| `implement` | `park_on_main(planner_repo)` when the transfer ran; builder keeps its new `feat/*` branch, so `refresh_main(builder_repo)` only |
| `accept` | `park_on_main(reviewer_repo)` |
| `reject` | `refresh_main` on the repo that owns the branch (do not park an agent that may still be mid-work) |
| `push` | replace the `pull_target` `git_pull` with `refresh_main(pull_target)` — the target is normally on a feature branch, which is exactly why the current pull never advances its `main` |
| `_dispatch_prompt` (`workflow/tmux_dispatch.py`) | add `refresh_main(repo)` next to the existing `git_pull`, so an agent asked to rebase has a current `origin/main` |

Guard `refresh_main` against a repo with no `origin` remote (`git_remote_url` returns `None`) — some
tests construct bare-bones repos; warn and return rather than raising.

The Phase 0 regression test must now pass.

## Phase 3 — rebase fallback in `transfer_branch_to_main`

### Flow

Decide fast-forwardability **in the source repo**, before pushing anything — this avoids the
ordering hazard of pushing first and discovering the problem in canon:

1. `git fetch origin` in the source repo.
2. Determine the branch head ref: the local branch if it exists, otherwise `origin/{branch}`.
3. If `git_is_ancestor(source_repo, "origin/main", head)` → **fast-forward path** (today's
   behaviour): `git push origin` in source → `git pull` in canon → `git merge --ff-only origin/{branch}`
   → `git push origin` in canon.
4. Else → **rebase path**:
   a. Refuse unless `allow_rebase=True` was passed (set from the resume classification, see below):
      a rebase must never be a surprise.
   b. Safeguards, each a `require(...)` with a message naming the fix:
      - source tree clean;
      - `git_current_branch(source_repo) == branch`;
      - `branch` is not checked out in any other worktree (`other_worktrees: Sequence[Path]` passed
        by the caller — the other three agent repos plus canon).
   c. In `--dry-run`: print what would be rebased and return `TransferResult.REBASED` without
      touching anything.
   d. `git rebase origin/main`. On `CalledProcessError`: run `git merge-tree --write-tree origin/main
      HEAD` to collect conflicting paths for the message, `git rebase --abort` (best effort, warn if
      it fails), then raise `WorkflowError` listing branch, conflicting paths, and the recovery:
      `workflow rebase {plan}`.
   e. Call the optional `post_rebase_verify: Callable[[], None] | None` callback. It raises on
      failure, which aborts *before* anything is pushed or merged. This is where the caller re-runs
      the pre-commit hook; `git_helpers` stays free of config/docker knowledge.
   f. `git push --force-with-lease` in source.
   g. `git fetch origin` in canon (its `origin/{branch}` is now stale after the force-push), then
      `git pull`, `git merge --ff-only origin/{branch}`, `git push origin`.

Signature:

```python
class TransferResult(enum.Enum):
    FAST_FORWARD = "fast_forward"
    REBASED = "rebased"

def transfer_branch_to_main(
    source_repo: Path,
    canon_repo: Path,
    *,
    branch: str | None = None,
    allow_rebase: bool = False,
    other_worktrees: Sequence[Path] = (),
    post_rebase_verify: Callable[[], None] | None = None,
    dry_run: bool = False,
) -> TransferResult
```

Callers print a loud warning when the result is `REBASED`, naming the branch and the fact that
history was rewritten.

### Resume classification (the risky part — pin it down here)

Each of `_classify_todo_steps`, `_classify_implement_steps`, `_classify_accept_steps` gains a rebase
step next to its existing transfer step, e.g. `_STEP_ACCEPT_REBASE = "rebase feat/... onto main"`:

- probe (read-only, in the source repo, after a `git fetch origin`):
  `git_is_ancestor(source, "origin/main", head)` → `satisfied("branch already based on current main")`,
  else `will_run`.
- The run phase passes `allow_rebase=resume.should_run(_STEP_*_REBASE)`.
- The fetch during classification is a ref-only mutation with no worktree effect. This is a
  deliberate, documented exception to the "guards are read-only" rule in the `git_helpers` module
  docstring — accurate previews are impossible without it. Note it in that docstring.

Resume semantics to document in code comments and cover with tests:

- *Rebased and force-pushed, canon merge failed*: rerun classifies the transfer as still needed and
  the rebase as satisfied → plain fast-forward. Clean.
- *Rebased locally, force-push failed*: rerun's ancestry probe is satisfied (local branch already sits
  on `origin/main`), and the fast-forward path's plain `git push` succeeds because the remote branch
  is an ancestor of nothing — verify this in a test; if the plain push is rejected, make the
  fast-forward path fall through to the force-push rather than failing.
- *Source repo parked on `main` by a prior successful run*: the rebase path's "branch must be checked
  out" guard fires. That is intended — the recovery is the `rebase` prompt, whose agent checks the
  branch out. The error message must say so.

### Post-rebase verification

In `accept` (and `todo`/`implement`, where the source repo is planner and the hook is equally cheap),
pass `post_rebase_verify=lambda: _run_pre_commit_hook(config, <source repo name>, dry_run=dry_run)`.
It only runs on the rebase path.

### `rebase` prompt

Add a `rebase` prompt command to both `planning` and `checking` in `docs/default_config.yaml`
(keep each state's command list in its existing order — script/prompt grouping, not alphabetical):

```yaml
      - command_type: prompt
        name: rebase
        description: Rebase the working branch onto current main.
        prompt: |
          The branch {{ branch }} can no longer be fast-forward merged into `main`,
          and the automatic rebase hit conflicts.
          Fetch from origin and rebase {{ branch }} onto `origin/main`.
          Resolve the conflicts, preserving the intent of both sides; do not drop changes from `main`.
          Run the pre-commit hook and make sure it passes.
          Do not push: you have no write access to the shared repository.
        reminder: validate
```

`{{ branch }}` renders as the target repo's current branch (`_git_metadata` in
`workflow/tmux_dispatch.py`), which is correct for both planner and reviewer.

**Config changes regenerate AGENTS.md**: run `workflow agents-md`, and update the golden fixture
`tests/fixtures/agents_md_default_section.txt` plus this repo's own `AGENTS.md` managed section and
`.agents_md_hash`. `test_render_workflow_section_matches_golden` will otherwise fail.

### Docker: mount `bare` read-only

`docs/example_run_agent_in_docker.sh` mounts only the agent's own repo, so `git fetch` inside a
container cannot reach `origin` and the rebase prompt cannot work. Add to the long-lived agent
container's `docker run`:

```
                    -v "${PROJECT_DIR}/bare:${PROJECT_DIR}/bare:ro" \
```

Mount at the host path so it matches the absolute `origin` URL recorded at clone time. A read-only
mount makes `fetch` work and `push` fail by construction — add a comment saying that is deliberate.
Update `docs/source/docker.rst`.

## Phase 4 — cleanup and diagnostics

**Delete the merged feature branch on `accept`**, after the final canon push, all failures downgraded
to warnings (the accept has already landed):

1. `require` (as a condition, not an exception) `git_is_ancestor(canon, f"origin/{branch}", "main")`.
2. `refresh_main` then `git branch -d {branch}` in reviewer and in builder when the branch exists
   there. `-d` (never `-D`) refuses to delete anything unmerged — that refusal is the safety net.
3. `git push origin --delete {branch}` from canon.

**`workflow doctor` staleness checks** (`workflow/commands/doctor.py`), read-only and fetch-free —
compare SHAs against bare directly:

- Add `git_rev_parse(repo_path, ref) -> str | None` to `git_helpers` (returns `None` when the ref is
  unknown).
- For each worktree repo: if `rev-parse refs/heads/main` differs from bare's `refs/heads/main` →
  anomaly `"main is stale (differs from bare)"`. After this plan's fixes this should never happen at
  rest, so it is a genuine anomaly and may fail the exit code.
- For each active `feat/*`/`plan/*` branch: if bare's `main` SHA is not an ancestor of the branch head
  → informational note `"not based on bare main; accept will rebase"`. Add a `notes` field to
  `RepoCheck`, rendered in the table but excluded from the non-zero exit condition: a
  rebase-pending branch is normal during parallel work.

## Verification

- `tests/test_git_helpers.py`: unit tests for `refresh_main` (both branches of the on-main check, and
  the opportunistic `origin/main` update), `park_on_main` (clean and dirty), `git_rev_parse`.
  `test_transfer_branch_to_main_runs_steps_in_order` must be rewritten — its full monkeypatching
  cannot express the new decision logic. Prefer moving its coverage to real-git integration tests.
- `tests/test_integration_workflow.py` (real repos — this is where the value is):
  1. the Phase 0 reproduction/regression;
  2. `accept` with `main` advanced by an unrelated commit → rebase happens, `main` fast-forwards, the
     result is `REBASED`;
  3. `accept` with a genuine conflict → fails fast, branch left un-rebased (`git rebase --abort` took
     effect), `main` untouched;
  4. resume after a rebase whose canon merge failed → completes as a fast-forward;
  5. `reject` of a `checking` plan through the default canon path;
  6. `accept` deletes the feature branch locally and on bare.
- `--dry-run` on `todo`/`accept` with a rebase pending: preview shows the rebase step, no refs change
  (assert SHAs before/after).
- `tests/test_doctor.py`: stale-main anomaly, rebase-pending note (must not affect exit code).
- Full suite plus `mypy --strict` and `black` via the pre-commit hook.

## Risks

1. **Force-push.** The only place in the system that rewrites published history. Review the
   safeguards in Phase 3 carefully; a bug here can destroy an agent's work. The dry-run assertions
   are not optional.
2. **Phase 0 may invalidate the diagnosis.** If the reproduction shows a different mechanism, stop
   and revise this plan rather than fixing both.
3. **Resume interaction.** Plan 020.1 deliberately treats `transfer_branch_to_main` as one
   idempotent unit; the rebase breaks that assumption. The three resume scenarios listed in Phase 3
   must each have a test.
4. **A clean rebase is not a correct rebase.** The re-run pre-commit hook catches build/test
   breakage, not semantic drift. The `REBASED` warning is the human's cue to look.
5. **`transition_plan`'s new lookup changes behaviour for all commands**, not just `reject`. Verify
   the existing transition tests still pass unmodified; if one needs changing, understand why before
   changing it.
6. **`git branch -d` in builder** can refuse when builder's `main` is stale — hence the `refresh_main`
   first, and warnings rather than errors.

## Dependencies

None on unimplemented plans. Builds on the resume/classification machinery from plan 020.1 and the
docker script from plans 017/021.

## Deviations

- **Phase 0 reproduction confirmed the diagnosis**, not the literal sequence in the Goal text (a
  plain `todo` A → `push` a new intent → `todo` B does *not* fail: `push`'s `pull_target: planner`
  happens to refresh planner's `main` past the missing commit before the second branch is even
  cut). The failing sequence: `todo 005_foo` succeeds but leaves planner's local `main` one commit
  behind bare's (missing its own "Transition 005_foo to todo" commit, per the ordering hazard);
  cutting `plan/006_bar` from that stale `main` and running `todo 006_bar` then fails `git merge
  --ff-only -- origin/plan/006_bar` in canon, because canon's `main` has since advanced past
  `006_bar`'s base. Regression test: `test_todo_fails_when_second_branch_cut_after_first_todo` in
  `tests/test_integration_workflow.py`.
  - An earlier version of this test cut *both* sibling branches from the same base commit before
    either `todo` ran (as `subdivide`/`mvp` would), mirroring the plan's "two `todo`s on separate
    planner branches" candidate. That scenario is a genuine divergence (the second branch's base
    predates canon's advanced `main` through no ordering fault -- the branches were simply cut
    concurrently), so it requires Phase 3's rebase fallback and is *not* fixed by Phase 2's
    `park_on_main` alone. It was replaced with the sequential-cut version above so the Phase 2
    regression test isolates issue 1 from issue 2's rebase fallback; the concurrent-branches case is
    covered by Phase 3's real-git integration tests instead.
- **Phase 2 call sites**: `todo` and `accept` call `park_on_main`/`refresh_main` unconditionally
  after their final canon push (both are idempotent no-ops on a resume where nothing changed); only
  `implement`'s `park_on_main(planner_repo)` is gated on whether the transfer actually ran, since
  without a transfer there is no planner branch involved at all.
- **Phase 3 conflict messaging uses `REBASE_HEAD`, not `HEAD`.** Mid a paused, conflicted rebase,
  `HEAD` is detached at the `onto` commit (`origin/main`) itself, so `git merge-tree --write-tree
  origin/main HEAD` reports zero conflicts (both sides are identical). `REBASE_HEAD` is git's own
  ref for "the commit currently being replayed" and is what must be diffed against `origin/main` to
  see the real conflict. Verified with a real conflicting rebase in
  `test_transfer_branch_to_main_rebase_conflict_aborts_and_raises`.
- **`rebase` prompt placement**: inserted as the last prompt command in each state (after `revise`
  in `planning`, after `fix` in `checking`), immediately before that state's script commands --
  preserves the plan's "script/prompt grouping" instruction while keeping it next to the other
  recovery-oriented prompts.
- **Phase 1's strict source lookup broke `--dry-run` on a never-before-transferred plan.**
  `transition_plan`'s scan-based lookup runs even in `--dry-run`, but a dry run never actually
  pushes, so a plan being `todo`'d (or rebased) for the very first time genuinely isn't in the
  target repo yet -- the old behaviour (trusting `plan.path`'s directory, unchecked) happened to
  paper over this because `git_mv` itself never touches disk in dry-run. Fixed in `transition_plan`:
  in `--dry-run` only, a missing source falls back to the same guessed path the old code used,
  purely for the preview print. Real runs still get the accurate, strict lookup. Caught by (and
  regression-tested in) `test_todo_dry_run_previews_rebase_without_mutating` and
  `test_transition_plan_dry_run_guesses_source_when_not_yet_landed`.
- **This repo's own `AGENTS.md`** has no accompanying `.agents_md_hash` (this is the tool's source
  checkout, not a live workflow project instance with a `bare`/agent-repo layout), so only the
  managed section was resynced against the golden fixture -- which incidentally also picked up two
  unrelated pre-existing entries (`architecture`, `mvp`) that had drifted out of sync with
  `docs/default_config.yaml` before this plan.

## Post-review fixes (round 1)

Applied after `docs/plans/reviews/pr/024_avoiding_failed_merges_b929f03.md`; decisions taken with
the human operator in that review turn.

- **Force-pushing is now an explicit, guarded, opt-in act.** The fast-forward path publishes the
  source branch by explicit refspec (`git push -u origin <branch>`, so the failure and any retry
  concern the same ref, and `push.autoSetupRemote`'s tracking is preserved), skipping the push
  entirely when the branch does not exist locally. Its `--force-with-lease` fallback now requires
  all three of: no commits on `origin/<branch>` without a patch-equivalent on the local branch
  (`git cherry`, in `remote_branch_has_unique_commits` — a fetch makes the lease itself
  meaningless, so the lease is not a safeguard here); the new `--force-push` flag; and a passing
  `post_rebase_verify`, since the interrupted run that rebased never got to verify.
- **Flag naming.** `--force` (skip failing validation) is renamed `--force-validation` on `todo`
  and `check`, so that the three forcing flags stay distinguishable: `--force-validation` (bypass
  validation), `--continue` (user vouches for a partial state), `--force-push` (rewrite a published
  branch). The rebase path's own force-push stays automatic — it is already gated on the rebase
  step's classification, and requiring a flag there would defeat the automatic rebase this plan
  exists to add. It gained the same `remote_branch_has_unique_commits` guard, checked *before* the
  rebase: afterwards every remote commit is a rewritten one, and a rebase that resolved anything is
  free to change patch ids, which would make the check refuse a perfectly good rebase.
- **Every clone's `main` is refreshed after every handoff** (`_sync_repos_best_effort`), not just
  the two the handoff touched — otherwise `doctor`'s stale-main check reports anomalies at a
  healthy rest state (a `todo` leaves builder and reviewer behind), and the untouched clones cut
  their next branch from a stale `main`. Only the handing-off agent's clone is parked on `main`;
  the others are refreshed via the refspec fetch, since they may be mid-work. Sync failures are
  warnings, never errors: the handoff has already landed by then (same rationale as
  `_rotate_best_effort`).
- `doctor`'s stale-main message is now "main differs from bare (stale or unpushed)": the check is
  symmetric, and an unpushed local commit is the other thing it can catch.
- `transition_plan` translates a `PlanCollisionError` from its repo-wide scan into "cannot
  transition X while sequence number N collides in <repo>", instead of reporting a collision
  between two unrelated plans as though it were the transition's own problem.
- The rebase step's classification no longer touches the source repo at all when the transfer will
  not run (`implement` on a `todo` plan no longer requires a healthy planner clone), and the run
  phase reuses the classification's fetch via `already_fetched=True`.
- `delete_merged_branch` previews in `--dry-run` instead of probing a merge that a dry run never
  performed and warning about a branch that is fine.
- New tests: force-push refused without the flag / with remote-only commits / with a failing
  verification; the rebase path refused with remote-only commits; `todo` and `implement` rebasing a
  concurrently-cut sibling branch end to end (including that `post_rebase_verify` re-runs the hook
  against *planner*); `transition_plan`'s reframed collision error; the no-origin `refresh_main`
  warning.
