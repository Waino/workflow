# Improving script command robustness

The issue: commands frequently run halfway and then fail, leaving the system in an inconsistent state.
Rerunning the same command is not successful, and rollback is fully manual and tedious.

Let's improve precondition checking, to prevent starting modifications if the command would fail.
Let's make the steps that compose into the commands idempotent when possible,
to enable the same command to be rerun to reattempt later steps, skipping already done steps.

## Scope

High priority: init and start.
Medium priority: check, accept, implement (the cross-repo git handoffs, where a
half-completed run leaves the nastiest inconsistent state).
Low priority: all other script commands.

## Decisions (from critique 2026-07-06)

The following decisions refine the sections below and must be honored by the plan.

- **Preconditions catch, they do not guarantee.** Preconditions catch the common,
  cheaply/statically detectable failure causes early (wrong remote, dirty tree,
  existing branch, leftover lock). They cannot prevent network failure, docker
  daemon down, disk full, etc. Everything else relies on idempotent re-run.
- **Guards check postconditions, not mere existence.** "Idempotent" means a step is
  skipped when its *postcondition* holds, never when a path merely exists. A `bare/`
  directory left by a failed clone must not cause the clone step to be skipped.
- **Treat individual git commands as steps.** Within the cross-repo commands, each
  git operation (push to bare, pull to canon, ff-merge, push back, downstream
  fetch/checkout) is its own step with its own precondition and postcondition, and
  must be listed carefully in the plan.
- **Transition moves are a key precondition class.** A transition does a `git mv` of
  the plan file. On a resumed run the file may already be in the target-state
  directory; the transition step (and its commit) must detect this and skip rather
  than fail on a missing source.
- **No leftover git locks.** Add a precondition that no `.git/index.lock` (or similar)
  is present in any repo the command touches, with an actionable message — a killed
  prior run is the common cause and produces cryptic git errors otherwise.
- **Shared vocabulary.** Introduce two small helpers used across commands:
  `require(condition, message)` for preconditions and a `skip_if_satisfied` helper
  that logs a uniform `[skip] ...` line, so precondition/skip logging is consistent.
- **`start` and the control pane.** `workflow start` is *not* run in the control
  pane; it creates or attaches to the session and becomes the control pane, so it
  must not `assert_in_workflow_session`. But it must detect being invoked from
  within the target session and refuse the `--reset` self-kill to prevent damage.
- **`doctor` command.** Add a new `workflow doctor` command (do not overload
  `status`) that reports each repo's branch, cleanliness, and whether it matches
  expected post-command state — the recovery aid the no-rollback decision requires.
- **Dry-run previews resume.** Make read-only guard queries (pre/postconditions)
  execute for real in dry-run, simulating only the mutating steps, so dry-run
  accurately previews the skip/resume plan (including with `--continue`).
- **Testing.** A partial-failure test strategy (fail at step B, re-run, assert
  resume at B) is part of the plan, not an afterthought.

## Precondition checking

Let's assume the command runs a sequence of steps, e.g. A-B-C.
The step B has some precoditions that do not depend on A,
and step has some precoditions that depend on neither A nor B.
Before running A, we should check these preconditions, in addition to any preconditions of A.
Preconditions of later steps that are made true by earlier steps can not be checked in advance.

Failing preconditions should stop execution early, with a clear actionable error message.
Preconditions are a best-effort early guard, not a guarantee that the command will
succeed (see Decisions: "Preconditions catch, they do not guarantee").

## Idempotence

Let's assume the command runs a sequence of steps, e.g. A-B-C.
Step A can be made idempotent: we can check whether the system already meets the postconditions of A.
The first run fails on step B, due to an error that could not be prevented by precondition checking.
The user fixes the issue and runs the command again.
Now, step A is skipped as already complete, and running resumes by reattempting B, followed by C.

### Idempotence when the state check is complete

If the state can be checked with high confidence, idempotence should be automatic.
Example: the existence of a correctly configured git remote `origin` can be checked during `workflow init`.
If the origin exists and is correctly configured, the step is automatically skipped with a log message.
If the origin exists and is incorrectly configured, the precondition check should fail early, preventing execution.
Similarly, `bare` is validated as a real git repository with minimal config checks;
the check stays light — we cannot verify it exactly matches a fresh clone with no
irrelevant changes.

### Idempotence when the state check is incomplete

If the state can be checked, but only roughly, idempotence should be gated behind a flag `--continue`.
Example: the existence of per-agent clones can be detected,
but it is not feasible to check that their state exactly matches a fresh clone.
If clones already exist, the precondition check should initially fail,
but allow the user to rerun with override by passing `--continue`.

### The `--continue` contract

`--continue` asserts that the rough partial state is a valid product of a prior run
of *this same command*; it proceeds without repairing. **The operator is responsible
for verifying the state before using it; it is documented as dangerous.** If the
rough state is actually broken, `--continue` will build on the corruption. Only the
steps whose state cannot be checked with high confidence are gated behind
`--continue`; every other step stays a hard precondition or an automatic skip.

### Resume preview

Before mutating anything, a command prints which steps are already satisfied (will be
skipped) and which will run. When a step's rough state is detected but `--continue`
was not given, the command prints this preview and then exits (so the operator has
time to react to an incorrect plan before rerunning with `--continue`).

### `--continue` vs `--force`

`--continue` (override a rough precondition) is a separate flag from the existing
`--force` (bypass validation failures). This overlap is an accepted risk; the help
text for each must state precisely what it does.

## No automatic rollback

Automatic rollback for errors will not be added.
It adds substantial complexity, and carries a real risk of causing further damage in unexpected states.
Recovery should be manual, but as rare as possible.
