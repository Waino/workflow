# Improving script command robustness

The issue: commands frequently run halfway and then fail, leaving the system in an inconsistent state.
Rerunning the same command is not successful, and rollback is fully manual and tedious.

Let's improve precondition checking, to prevent starting modifications if the command would fail.
Let's make the steps that compose into the commands idempotent when possible,
to enable the same command to be rerun to reattempt later steps, skipping already done steps.

## Scope

High priority: init and start.
Medium priority: check.
Low priority: all other script commands.

## Precondition checking

Let's assume the command runs a sequence of steps, e.g. A-B-C.
The step B has some precoditions that do not depend on A,
and step has some precoditions that depend on neither A nor B.
Before running A, we should check these preconditions, in addition to any preconditions of A.
Preconditions of later steps that are made true by earlier steps can not be checked in advance.

Failing preconditions should stop execution early, with a clear actionable error message.

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

### Idempotence when the state check is incomplete

If the state can be checked, but only roughly, idempotence should be gated behind a flag `--continue`.
Example: the existence of per-agent clones can be detected,
but it is not feasible to check that their state exactly matches a fresh clone.
If clones already exist, the precondition check should initially fail,
but allow the user to rerun with override by passing `--continue`.

## No automatic rollback

Automatic rollback for errors will not be added.
It adds substantial complexity, and carries a real risk of causing further damage in unexpected states.
Recovery should be manual, but as rare as possible.
