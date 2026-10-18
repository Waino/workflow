# PR review: 030_various_fixes (feat/030_various_fixes @ dde556b)

Plan: `docs/plans/checking/030_various_fixes.md`
Test suite at review start: 844 passed (plain `pytest`, incl. slow).
Regression tests added by this review (all strict xfail, documenting confirmed defects):
- `tests/test_tmux_session.py::test_real_tmux_untagged_session_raises_workflow_error` (×2, real tmux on a private socket)
- `tests/test_workflow_commands.py::test_resolve_planner_plan_reraises_original_when_planner_clone_missing`
Also added a passing real-tmux round-trip test (`test_real_tmux_tagged_session_is_found`).

Overall: the six sections land in the planned order, one commit each, and the code is lean and well-placed.
One high-severity bug breaks the documented migration path; the remaining findings are smaller gaps.

## High

1. **An untagged tmux session crashes every session command with a traceback, including `workflow start`.**
   `find_workflow_session` (`workflow/tmux_session.py:27`) expects `session.show_option(PROJECT_DIR_OPTION)` to return
   `None` for an unset user option. With libtmux 0.55 / tmux 3.4 it raises `libtmux.exc.InvalidOption` instead
   (confirmed with a real tmux server). Consequences:
   - `start` → `workflow_session_exists` → uncaught `InvalidOption` (`start` only catches `CalledProcessError`,
     `WorkflowError`), so the user gets a stack trace instead of the "belongs to an unknown project … kill-session"
     remedy. Every existing user hits this once after upgrading, which is the exact case the HISTORY entry and
     the plan's Risks section describe ("the error message must make the fix obvious").
   - Same for `_resolve_session` / `assert_in_workflow_session`, `is_inside_workflow_session`, and
     `attach_workflow_session`.
   - `kill_workflow_session` wraps it as "failed to kill tmux session …: invalid option" (misleading).
   - `active_tmux_repo` happens to swallow it (it catches `LibTmuxException`), so the resolver is unaffected.
   The unit tests mock `show_option` to return `None` for the missing tag, so the mock contract is wrong. These
   tests pass while real tmux behaves differently.
   Fix: in `find_workflow_session`, treat `libtmux.exc.OptionError` (or `InvalidOption`) from `show_option` as
   `owner = None`. Then drop the xfail marker and correct the mock in `_session(None)` (`side_effect` raising
   `InvalidOption`) so the mocked tests match tmux.

## Medium

2. **Plan §6 "Docs" not implemented: no planner-branch section in `docs/source/workflow.rst`.** The plan requires a
   section summarising the rule (pointing to the AGENTS.md block), the `todo` guard, and branch deletion.
   `rg -i "planner branch" docs/source` finds nothing; only `HISTORY.rst` mentions it.
3. **HISTORY.rst is incomplete.**
   - The Risks section explicitly asks for the one-off cleanup hint for pre-existing merged `plan/*` branches
     (`git branch --merged main 'plan/*'`). It is missing.
   - The changes that users can see are not listed: the check rename `noqa-nosec` → `suppressions` (anyone
     grepping validate output or audit logs for the old name is affected), the stricter in-band `init`
     preconditions (committed hook, `pyproject.toml` or bootstrap script — this breaks projects that used to init
     with uncommitted files), collected pre-flight errors, and dry-run `(expected)` branches.
4. **`_resolve_planner_plan` best-effort fallback only covers `CalledProcessError`** (`workflow/commands/workflow.py:1359`).
   `git_current_branch` goes through `subprocess.run(cwd=planner_repo)`, which raises `FileNotFoundError` when the
   planner clone is missing (not initialised yet, or deleted). This replaces the original `PlanNotFoundError`
   with an unhandled traceback, which contradicts the plan ("if that git call fails, re-raise the original").
   Fix: `suppress(subprocess.CalledProcessError, OSError)`. Regression test committed (xfail).
5. **Wrapping `SourcesDisagreeError` loses its rich rendering.** `print_workflow_error` renders exceptions that
   define `__rich__` as a coloured, aligned table. `_resolve_planner_plan` re-raises a plain `WorkflowError` built
   from `str(exc)`, so `todo`/`implement` now print the plain-text fallback in red. Options: append the hint to
   the original exception (e.g. a `hint` attribute rendered by `__rich__`), or print the original via
   `print_workflow_error` and raise the hint separately. Low effort; cosmetic but a regression.

## Low

6. **Explicit `tmux.session_name` is not validated.** libtmux rejects names containing `.` (`BadSessionName`), and
   tmux rewrites `.`/`:`. If a user sets `session_name: my.proj`, `start` fails every time ("failed to create
   tmux session"), and lookups never match. The derived name is sanitised but the explicit one is not. A
   `field_validator` on `TmuxConfig.session_name` rejecting `.`/`:` (or applying the same translation) would
   close this.
7. **`_check_suppressions` header detection depends on the `b/` prefix.** With `diff.noprefix=true` or
   `diff.mnemonicPrefix=true` in the user's gitconfig, `git diff`/`git log -p` print `+++ docs/plans/...` or
   `+++ w/...`. `removeprefix("+++ b/")` then leaves the `+++ ` in the path, plan files are no longer skipped,
   and their lines raise false warnings. Paths that git quotes (non-ASCII under `core.quotePath`) behave the
   same way. An added line whose content starts with `++ ` is also taken as a file header. All of these only
   produce warnings. A robust fix is to pass `--src-prefix=a/ --dst-prefix=b/` in both diff helpers, or to key
   off `diff --git` lines.
8. **The `delete_merged_branch` dry-run message now prints absolute repo paths** ("would delete feat/x in
   /home/…/reviewer, /home/…/builder and on bare"). Before, it said "reviewer, builder". It is noisier than the
   rest of dry-run output, which uses `_display_cwd`-style relative paths. Consider `repo.name` or `_display_cwd`.
9. **When parking fails, the bare branch is still deleted.** `_sync_repos_best_effort(..., park=planner)` is best
   effort. If parking fails (e.g. the planner has uncommitted changes), the planner stays on `plan/x`, the local
   `-d` fails with a warning, and `git_delete_remote_branch` still deletes `bare/plan/x`. The planner is left on
   a branch whose upstream is gone. Nothing is lost, because the work is merged, but a skip-if-still-checked-out
   guard would avoid the half-cleanup.
10. **The `implement` not-found hint can mislead.** `_resolve_planner_plan` is used for `implement` even when the
    plan is expected in `todo` (canon). A typo then prints "implement only sees plans on that branch", which
    is irrelevant to that case. This is acceptable per plan Decision 19, but the wording could say "plans in
    planning state".
11. **The `docs/example_run_agent_in_docker.sh:212` comment still says "Out-of-band:"** above the
    `docs/bootstrap_venv.sh` branch. That branch now also serves in-band projects. `example_bootstrap_venv.sh`
    was updated; the docker script comment was not.
12. **Naming: the in-band bootstrap path is built from `DOCS_REPO`** (`f"{DOCS_REPO}/{BOOTSTRAP_VENV_FILENAME}"`).
    `DOCS_REPO` means the oob docs repo name; in-band, `docs/` is a directory in the project repo, so the two
    only coincide. A dedicated `IN_BAND_BOOTSTRAP_PATH` constant (or `layout`-derived path) would state the
    intent.

## Testing gaps

13. The tmux tests mock libtmux entirely, which is how finding 1 shipped. The real-tmux fixture added by this
    review (private socket, skipped without tmux) should be used for at least the tag round-trip and the
    untagged case.
14. Plan Verification lists "stateless prompt keeps `<dry-run-branch>`". `test_dry_run_renders_the_expected_branch`
    covers builder, reviewer, and planner, but no stateless prompt.
15. No test that `start` (the CLI) shows the friendly foreign-session error end to end. A CLI-level test with a
    foreign/untagged session would have caught finding 1 at the command boundary as well.
16. The `_check_suppressions` tests have no case for an oob joined diff (code repo + docs repo, `plans` prefix).
    It works by inspection, but it is the less-exercised call site.

## Spec conformance (checked, OK)

- §1: session name accessor, sanitising, `tmux_session.py` (no import cycle), tag set on creation plus dry-run
  preview, all lookups routed through `find_workflow_session`, doctor config note.
  `rg tmux.session_name workflow` is clean apart from the property and doctor.
- §2: marker list sorted/lowercase, plan files skipped, rename propagated to docs/tests/docstrings.
- §3: dry-run `(expected)` branch for builder/reviewer only; `_setup_stateless` passes `None`.
- §4: in-band preconditions (commit, committed hook, pyproject or executable committed bootstrap), shared
  `_check_executable_bootstrap`.
- §5: issues collected; name checks fail fast; one per-repo helper shared by oob init and add-repo.
- §6: template and golden/AGENTS.md regenerated, prompt reminders, `todo`/`implement` guard,
  `delete_merged_branch` generalised, deletion only after parking and never for `main` or a skipped transfer,
  doctor note restricted to the docs repo.
- vulture: nothing new.

## AGENTS.md

No new entry point; no AGENTS.md change proposed.
