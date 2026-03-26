# PR Review: 005_plan_state_and_git @ 3b56c73

Plan: `docs/plans/checking/005.1_plan_state_transitions_and_git_helpers.md`, `docs/plans/checking/005.2_doublecheck.md`
Commit: `3b56c73af9e183e6b574b73108d7e1b956fd6f31`
Reviewer: Augment Agent
Date: 2026-03-26

## Verdict

**Request changes.**

The branch moves the architecture in the right direction: `state.py`, `git_helpers.py`, and
`doublecheck.py` are split cleanly, the Click wrapping change in `workflow/cli.py` is the right
shape for decorated script commands, and the existing test suite still passes. However, two core
logic bugs block the intended workflow, and the new functionality has almost no direct test
coverage.

## Validation performed

- `pytest -q` ✅
- `pytest --cov-report=term-missing -q` ✅
  - notable coverage gaps in new code:
    - `workflow/state.py`: **0%**
    - `workflow/doublecheck.py`: **22%**
    - `workflow/git_helpers.py`: **28%**
    - `workflow/commands/workflow.py`: **38%**
- `ruff check workflow tests` ✅
- `mypy workflow tests` ⚠️
  - fails in existing test code: `tests/test_cli.py:176: "object" has no attribute "command"`
  - this failure is not introduced by the files changed in this branch
- Reproduced CLI behavior with a temp project containing only
  `reviewer/docs/plans/checking/005_plan_state_and_git.md`:
  - `workflow --dry-run doublecheck 005` ❌
  - result: `No plan found for '005' in repos: ['master']`
- Reproduced `transition_plan(..., repo_override="master")` with a monkeypatched `git_mv`:
  - `repo_path=/project/master`
  - `source=/project/planner/docs/plans/planning/005_foo.md`
  - `target=/project/master/docs/plans/todo/005_foo.md`
  - this confirms the override is applied only to the destination repo, not to the source path

## Findings

### 1. `workflow doublecheck` resolves plans only in `master`, which breaks the normal planning/checking flow

**Severity: High**

`workflow/commands/workflow.py` calls:

- `resolve_plan(plan_path, config.project_dir)`

without a repo hint or expected-state hint. `resolve_plan()` defaults to searching only `master`.

That conflicts with both the intent doc and the new doublecheck plan:

- planning-stage plans normally live in `planner/docs/plans/planning/`
- checking-stage plans normally live in `reviewer/docs/plans/checking/`
- loose path parsing is supposed to let the operator omit the repo when the command already
  implies it

In practice, the new CLI command fails for the most natural invocation forms unless the user
spells an explicit repo prefix. The dry-run reproduction above demonstrates exactly that failure.

Files involved:

- `workflow/commands/workflow.py`
- `workflow/plan_resolver.py`
- `docs/plans/checking/005.2_doublecheck.md`
- `docs/plans/intent/001_idea.md`

**Fix direction:** resolve the plan the same way prompt commands do: search the repo implied by the
workflow stage (`planner` for planning, `reviewer` for checking, etc.), or otherwise search the
relevant repo set before selecting the canonical plan.

### 2. `transition_plan()` does not actually support `repo_override` for the source path

**Severity: High**

The helper is documented as supporting transitions in a different repo than the one recorded in
`plan.path` (in practice, `master` after push/pull/merge steps). But the implementation computes:

- `repo_path` from `repo_override`
- `target_dir` from `repo_override`
- `source` from the original `plan.path`

So with `plan.path == planner/docs/plans/planning/...` and `repo_override="master"`, the helper
tries to run `git mv` from a `planner/...` source to a `master/...` target while executing inside
the `master` repo. That is the wrong source file for the stated use case, and it will break the
future `todo` / `implement` / `accept` / `reject` flows that rely on transitioning the copy that
already exists in `master`.

Files involved:

- `workflow/state.py`
- `docs/plans/checking/005.1_plan_state_transitions_and_git_helpers.md`

**Fix direction:** when `repo_override` is provided, rewrite the leading repo component for both the
source and destination paths before invoking `git_mv`.

### 3. The new functionality is effectively untested

**Severity: Medium**

This branch adds the core logic for:

- state transitions
- git orchestration helpers
- doublecheck policy enforcement
- a new Click command path for `workflow doublecheck`

but it adds no direct tests for any of those behaviors. The coverage report shows that the new code
is largely unexercised: `workflow/state.py` is at 0%, `workflow/doublecheck.py` at 22%, and
`workflow/git_helpers.py` at 28%.

That matters here because the two workflow-blocking bugs above are exactly the kind of regressions
that focused command-level and helper-level tests should have caught.

**Fix direction:** add tests for at least:

- `workflow doublecheck 005 --dry-run` when the plan exists only in `planner` or only in `reviewer`
- `infer_target_repo()` for each plan state
- `transition_plan()` with and without `repo_override`
- the four individual doublecheck checks
- `transfer_branch_to_main()` and the `branch == "main"` failure path

### 4. Git-diff failures are not converted into the planned “hard command errors” path

**Severity: Medium**

`doublecheck()` wraps `git_fetch()` failures into `WorkflowError`, but the later calls to
`git_diff_branch()` in `_check_file_scope()`, `_check_noqa_nosec()`, and `_check_claude_md()` are
left uncaught. If `git diff origin/main...HEAD` fails, the command will bubble a raw
`CalledProcessError` instead of following the planned CLI error-handling path.

This is relevant to the risk called out in `005.2`: branch-diff edge cases around rebases or other
history oddities should be handled gracefully.

Files involved:

- `workflow/doublecheck.py`
- `workflow/git_helpers.py`

**Fix direction:** wrap `git_diff_branch()` failures in `WorkflowError` at the helper or
`doublecheck()` layer so the CLI can report them cleanly.

## Positive observations

- Splitting plan transitions, git helpers, and doublecheck into separate modules fits the existing
  architecture well.
- The `managed_hooks` config entry is a good extension point and matches the plan cleanly.
- Simplifying `_build_script_command()` so Click preserves function decorators is the right approach.
- The current branch keeps the existing suite green under `pytest` and `ruff`.

## Integration-style tests

Yes — this feature needs **thin integration tests now**, but not heavyweight end-to-end tests.

The risky parts are orchestration boundaries: Click parsing, repo selection, git-diff behavior,
temporary repo layout, and symlink/hash verification. Those are well suited to temp-directory tests
with `CliRunner`, monkeypatched git helpers, and a few local git repos created on the fly. True E2E
tests involving tmux, sandboxes, or networked remotes can wait.