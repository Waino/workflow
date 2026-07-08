# 020: Script command robustness (preconditions + idempotent resume)

Input: `intent/020_robustness`
Completeness: complete, but large — build in the phased commit order in "Build sequence". Subdivide into 020.1/020.2/020.3 if a single PR is too big.

## Goal

Script commands frequently run halfway, fail, and leave the system inconsistent.
Re-running the same command is not currently safe, and recovery is fully manual.
This plan makes the in-scope commands (a) check cheap, statically-detectable
failure causes up front, and (b) skip already-completed steps on re-run, so a
fixed-and-rerun cycle resumes where it left off. No automatic rollback is added.

## Scope

In scope, by priority:
- High: `init`, `start`.
- Medium: `check`, `accept`, `implement` (the cross-repo git handoffs).
- New: `workflow doctor` (recovery diagnostics).
- Shared: a small precondition/resume vocabulary; read-only git helpers; a
  dry-run change so read-only guards run for real.

Out of scope: `todo`, `reject`, `push`, `new`, `go`, prompt commands. They may
adopt the same vocabulary later but are not touched here.

## Design decisions (locked)

These were settled during critique; the builder must honor them.

1. **Preconditions catch, do not guarantee.** They cover the common,
   cheaply-detectable causes (wrong remote, dirty tree, existing branch, leftover
   lock). Network/docker/disk failures are not pre-checkable; those rely on
   idempotent re-run.
2. **Guards check postconditions, never mere path existence.** A `bare/` left by a
   failed clone must not cause the clone step to be skipped.
3. **Each git operation in the cross-repo commands is its own step** with its own
   precondition + postcondition (see per-command tables).
4. **Transition moves are a precondition class.** A transition `git mv`s the plan
   file; on resume it may already be in the target dir. The move + its commit must
   detect this and skip, not fail on a missing source.
5. **No leftover git locks.** Precondition: no `.git/index.lock` in any repo the
   command touches, with an actionable message.
6. **`--continue` contract:** asserts the rough partial state is a valid product of
   a prior run of *this same command*; proceeds without repairing. Documented as
   **dangerous** — the operator is responsible for verifying state. Only steps whose
   state cannot be checked with high confidence are gated behind it.
7. **`--continue` is separate from `--force`** (validation bypass). Accepted overlap;
   both flags get precise help text.
8. **Resume preview before mutating.** Every in-scope command evaluates all guards
   first, prints which steps will skip/run, and — when a rough step is detected but
   `--continue` was not given — prints the preview and exits, so the operator can
   react before rerunning with `--continue`.
9. **`start` becomes the control pane**; it must not `assert_in_workflow_session`.
   It creates the session when absent, or attaches when present. `--reset` kills the
   session + containers and recreates, but refuses the self-kill when invoked from
   inside the target session.
10. **Dry-run previews resume:** read-only guard queries execute for real in
    dry-run; only mutating steps are simulated.

## Shared vocabulary

New module `workflow/steps.py`. Deliberately lightweight — helpers plus a small
two-pass collector, not a framework that owns execution. Commands still call the
existing git helpers inline.

```python
def require(condition: bool, message: str) -> None:
    """Precondition guard: raise WorkflowError(message) when condition is false."""
    if not condition:
        raise WorkflowError(message)

class StepStatus(enum.Enum):
    SATISFIED = "satisfied"   # postcondition already holds -> skip
    WILL_RUN = "will_run"
    NEEDS_CONTINUE = "needs_continue"  # rough state; requires --continue

class ResumePlan:
    """Collects step decisions, renders the preview, and gates on --continue.

    Two-pass usage: evaluate every guard (read-only) to classify each step, then
    render() the preview, then abort_unless_continue(), then run only the steps
    whose status != SATISFIED.
    """
    def satisfied(self, name: str, reason: str) -> None: ...
    def will_run(self, name: str) -> None: ...
    def needs_continue(self, name: str, reason: str) -> None: ...
    def render(self) -> None: ...                     # prints "[skip]/[run]/[continue]" lines
    def should_run(self, name: str) -> bool: ...       # False for SATISFIED
    def abort_unless_continue(self, *, cont: bool) -> bool:
        """If any NEEDS_CONTINUE step exists and cont is False: render + return True (caller exits)."""
```

Rationale for the two-pass shape: decision 8 (preview before mutation) forces
separating "classify" from "run". This is the minimum structure that satisfies it
while keeping the per-step logic inline in each command. (Deviation note: this is
slightly more than the pure `if not done(): do()` sketch, but that sketch cannot
print a preview before mutating.)

Uniform preview output (example):
```
Resume plan for `init`:
  [skip] AGENTS.md committed        already up to date
  [skip] bare clone                 bare is a valid clone of canon
  [run]  agent clones
  [!!]   venvs                      exist; pass --continue to recreate (destructive)
```

## Read-only git helpers + dry-run change

Guards need read-only queries that **always execute**, even in dry-run. Current
helpers short-circuit in dry-run (`git_branch_exists`→False, `git_status_porcelain`→
""), which would make every guard look unsatisfied. Add to `git_helpers.py`:

- `git_ref_exists_remote(repo, ref) -> bool` — does bare/origin have the ref (via
  `git ls-remote --heads` or `rev-parse origin/<ref>`).
- `git_is_ancestor(repo, a, b) -> bool` — `git merge-base --is-ancestor a b`.
- `git_remote_url(repo, name) -> str | None` — for origin-config checks.
- `git_index_lock_present(repo) -> bool` — `.git/index.lock` (or worktree lock).
- `git_is_valid_repo(path, *, bare: bool=False) -> bool` — `git rev-parse` succeeds
  and (for bare) `--is-bare-repository` is true.

These take **no `dry_run` param** (always run). The existing mutating helpers keep
their `dry_run` behavior. Change `git_branch_exists`/`git_status_porcelain` callers
that are used as *guards* to use the always-run variants; the dry-run sentinels
remain only where the value gates a mutation that is itself simulated.

## Per-command step tables

Legend: **Pre** = up-front precondition (fail fast). **Skip-if** = postcondition
that makes the step a no-op. **Flavor**: `auto` = automatic skip; `continue` =
rough, gated behind `--continue`; `always` = safe to repeat, never skipped.

### init  (add `--continue`)

Up-front preconditions: canon has `.git`; `canon/.git-pre-commit-hook` exists;
no index lock in canon; bare — if present — is a valid bare repo (else fail with
"bare exists but is not a valid bare clone; remove it or fix it").

| # | Step | Skip-if (postcondition) | Flavor |
|---|------|-------------------------|--------|
| 1 | Generate + commit AGENTS.md in canon | `git_commit_if_changed` no-ops (already up to date) | auto |
| 2 | `git clone --bare canon bare` | bare is a valid bare repo whose HEAD derives from canon | auto |
| 3 | `git clone bare <agent>` per agent | agent dir is a valid clone with origin=bare | **continue** |
| 4 | per agent: `git config push.*` + pre-commit symlink | config values already set AND symlink points at `../../.git-pre-commit-hook` | auto |
| 5 | wire canon→bare: `remote add origin`, `config push.*`, `push -u` | origin already points at bare (correct) AND bare has canon HEAD; **fail** if origin exists but points elsewhere | auto (+fail branch) |
| 6 | `touch .workflow_root` | marker file exists | auto |
| 7 | create venvs (`--venv --reset` per agent) | venv dir exists (rough) | **continue** |

Notes: step 3 and step 7 are the rough ones. On a plain re-run where agent clones
already exist, `init` prints the preview and exits telling the operator to pass
`--continue`; with `--continue`, existing clones/venvs are recreated/skipped
appropriately (clones: skip existing; venvs: delete existing before recreating via `--venv --reset`).

### start  (extend `--reset`; no `--continue`)

Up-front preconditions: project_dir set; agents configured; docker script findable;
if `--reset` and running inside the target session → **fail** ("refusing to kill the
session you are attached to; detach first, or run without --reset").

| # | Step | Behavior |
|---|------|----------|
| 1 | resolve session state | session exists? |
| 2 | if exists and not `--reset` | skip creation, just attach (decision 9) |
| 3 | if exists and `--reset` | kill session + reset containers, then recreate |
| 4 | if absent | create session + windows |
| 5 | attach / switch-client | always |

This supersedes the earlier "session-exists is a hard precondition failure" answer:
per decision 9 `start` attaches when the session already exists rather than erroring.

### check  (`--continue` not needed; steps are auto/always)

Up-front preconditions: plan in `implementing` (existing enforce); builder on a
`feat/*` branch, not `main`; no index lock in builder/reviewer.

| # | Step | Skip-if | Flavor |
|---|------|---------|--------|
| 1 | pre-commit hook in sandbox | — | always |
| 2 | run validate | — | always |
| 3 | transition plan → checking (mv + commit in builder) | plan file already in `checking/` | auto |
| 4 | push builder branch to bare | bare has branch at builder HEAD sha | auto |
| 5 | fetch in reviewer | — | always |
| 6 | checkout branch in reviewer | reviewer already on branch | auto |
| 7 | pull reviewer | — | always |
| 8 | checkout main + pull in builder | builder already on main | auto |
| 9 | rotate pane | (best-effort, already downgraded) | always |

### accept

Up-front preconditions: plan in `checking`; reviewer on a `feat/*` branch; no locks
in reviewer/canon; canon on `main`.

| # | Step | Skip-if | Flavor |
|---|------|---------|--------|
| 1 | push reviewer branch to bare | bare has branch at reviewer HEAD | auto |
| 2 | pull canon | — | always |
| 3 | merge `origin/<branch>` into canon main (ff-only) | `origin/<branch>` is ancestor of canon `main` | auto |
| 4 | transition plan → done (mv + commit in canon) | plan file already in `done/` | auto |
| 5 | push canon | bare `main` at canon `main` sha | auto |
| 6 | checkout main + pull reviewer | reviewer already on main | auto |

### implement

Up-front preconditions: plan in `planning`/`todo`; builder working tree clean; no
locks; **feat branch handling** — if `feat/{plan}` exists AND plan already in
`implementing`, treat as a satisfied prior run (skip creation); if it exists but the
plan is not yet `implementing`, that is a rough leftover → `NEEDS_CONTINUE`.

| # | Step | Skip-if | Flavor |
|---|------|---------|--------|
| 1 | (planning only) `transfer_branch_to_main` — push planner, pull canon, ff-merge, push canon, checkout+pull planner. Guard each sub-op. | plan already past `planning` (in `todo`/later) | auto |
| 2 | transition plan → todo (planning only) | plan file already in `todo/` | auto |
| 3 | transition plan → implementing (mv + commit in canon) | plan file already in `implementing/` | auto |
| 4 | push canon | bare `main` at canon `main` sha | auto |
| 5 | fetch builder | — | always |
| 6 | checkout main + pull builder | builder already on main | auto |
| 7 | create `feat/{plan}` from main in builder | branch exists | auto (see precondition) |
| 8 | rotate pane | best-effort | always |

`transfer_branch_to_main` must be refactored so each of its five git ops is
individually guarded (it currently runs them unconditionally). Add guarded variants
or inline the guards in `implement`/`todo`'s caller path; keep the helper's existing
signature working for `todo` (which is out of scope for resume but shares the code).

## doctor command

New `workflow doctor` (register_script). Read-only; makes manual recovery tractable
given no rollback. For each repo (`canon`, `bare`, `planner`, `builder`, `reviewer`):
report current branch, clean/dirty, presence of index lock, and whether it matches
the expected resting state (agents on `main` when idle; canon on `main`; bare
reachable). Also flag plan files that appear in more than one state dir. Rich table
output; exit non-zero if any anomaly is found. No mutations, no `--dry-run` special
casing needed.

## Build sequence

Recommended commit order (also the subdivision boundary if split):
1. `workflow/steps.py` + read-only git helpers + dry-run change, with unit tests.
2. `init` + `start` refactor onto the vocabulary, with resume tests.
3. `check` + `accept` + `implement` (incl. guarded `transfer_branch_to_main`), tests.
4. `doctor`, with tests.

## Verification

- **Unit tests** (`tests/test_steps.py`, `tests/test_git_helpers.py`): `require`,
  `ResumePlan` classification/preview/abort logic, and each new read-only helper
  against real temp repos.
- **Partial-failure / resume tests** (extend `tests/test_integration_workflow.py`,
  which already builds real temp git repos and mocks docker/tmux). Pattern per
  command: run to completion once; then delete/rewind a late artifact (e.g. remove
  the pushed branch from bare, or move the plan file back a state) and re-run,
  asserting (a) satisfied earlier steps are skipped (assert on preview output /
  that no redundant mutation occurs), and (b) the command completes. Also assert the
  `NEEDS_CONTINUE`-without-`--continue` path prints the preview and exits non-zero
  without mutating.
- **`init` bare-corruption test:** create a `bare/` dir that is not a valid bare
  repo; assert the precondition fails with the actionable message and nothing is
  cloned.
- **`start` self-kill test:** simulate being inside the target session with
  `--reset`; assert it refuses.
- **dry-run test:** with a partially-completed state, assert `--dry-run` prints an
  accurate resume preview (read-only guards ran) and performs no mutation.
- Docker/tmux stay mocked (sandbox cannot run them); note this limitation in the
  test module as done elsewhere.

## Risks

1. **Two-pass classification vs. inline guards.** Decision 8 forces a classify-then-
   run split, slightly heavier than the sketched inline `if not done()`. Kept minimal
   via `ResumePlan`; watch that it doesn't grow into the rejected Step framework.
2. **Postcondition correctness is the whole ballgame.** A wrong skip-if (e.g. checking
   path existence instead of validity) reproduces the original inconsistency bug. The
   `init` bare/clone guards are the highest-risk; review the ref/sha checks carefully.
3. **`--continue` builds on corruption by design.** Documented as dangerous; mitigated
   only by the preview + operator responsibility, not by repair.
4. **`transfer_branch_to_main` refactor touches the shared `todo` path.** `todo` is
   out of scope for resume; ensure its behavior is unchanged when guards are added.
5. **ff-only merge re-run.** `git merge --ff-only` on an already-merged branch returns
   0 ("Already up to date"), so the guard is belt-and-suspenders, but the *push* and
   *transition commit* after it are the real re-run hazards — guard them explicitly.
6. **Dry-run read-only queries** now hit the real repos; ensure they never mutate and
   handle a not-yet-initialized project gracefully (e.g. bare absent).

## Dependencies

None blocking. Builds on existing `git_helpers.py`, `shell.py`, `tmux_dispatch.py`,
and the integration test harness. Related but independent: 019 (tmux gaps, in
`implementing`).

## Deviations (recorded during implementation)

- **`plan_resolver` ambiguity on same-command reruns.** Resolving a bare plan
  identifier (e.g. `005_foo`) after the *same* transition command (`implement`,
  `check`, or `accept`) has already run once produces a `SourcesDisagreeError`:
  the audit source's most-recent entry now records that same command against
  its *target* state's owning repo, which can differ from the subcommand
  source's repo (derived from the command's *defined* state). This is a
  pre-existing `plan_resolver.py` limitation (out of scope for 020, not
  modified here), only surfaced by the new resume tests that call the same
  command twice. Worked around in tests by passing a fully-qualified
  `repo/docs/plans/state/name.md` path on the rerun, which is the same
  workaround an operator would use in practice. Left as a follow-up candidate
  for `plan_resolver.py`, not fixed in this plan.
- **`doctor` anomaly scope.** Implemented pragmatically per the brief spec:
  branch/dirty/lock/validity per clone, plus "stray branch" detection for
  `builder`/`reviewer` (non-`main`, non-`feat/<active plan>`) and duplicate
  plan files across state dirs *within a single repo*. Cross-repo staleness
  (e.g. `planner`/`reviewer` lagging behind `canon` between transitions) is
  normal/expected during active work and is intentionally not flagged, since
  agents only pull to `main` between tasks, not proactively after every canon
  change.
