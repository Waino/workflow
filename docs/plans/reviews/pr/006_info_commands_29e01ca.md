# PR Review: 006_info_commands @ 29e01ca

Plan: `docs/plans/checking/006_info_commands.md`
Branch: `feat/006_info_commands`
Commits under review:
- `c94efff` feat: implement info commands (status, log) and improve new command
- `f3ccdb3` feat: enhance status command with last-action and next-step columns from audit log
- `29e01ca` transition: 006 -> checking

Reviewer: Augment Agent
Date: 2026-05-09

## Verdict

**Approve with minor comments.**

The branch lands the four commands the plan calls out (`new` template extraction +
`git add`, `status`, `log`, and the already-done `cheat`), refactors the next-step
resolver into `workflow/next_step.py` so the cheat sheet, post-command reminders, and
the new status table all share one rendering path, and ships a focused test suite that
covers the happy paths plus several edge cases. Output is consistent with the
cheat-sheet markers as required.

There are no blocking defects. The notes below are mostly small DRY/readability and
test-coverage suggestions, plus one input-validation hardening recommendation for
`workflow new`.

## Validation performed

- `uv run pytest -q` → **164 passed**, 3 warnings (pre-existing pydantic v1 `class Config` warnings in `workflow/commands/cheat.py`, not introduced here).
- `uv run mypy workflow tests` → **clean** (32 source files, no issues).
- `uv run pytest --cov=workflow.commands.info --cov=workflow.next_step --cov=workflow.commands.workflow tests/test_workflow_commands.py` →
  - `workflow/next_step.py`: **89%**
  - `workflow/commands/info.py`: **84%**
  - `workflow/commands/workflow.py`: **69%** (most misses are unrelated transition commands)
- `git diff --stat main..feat/006_info_commands` → 11 files changed, 759 insertions, 132 deletions.

## Plan compliance

| Requirement (from `docs/plans/checking/006_info_commands.md`) | Status |
|---|---|
| `workflow new {slug}` exists and finds the next free number across all repos and the audit log | ✅ via `next_intent_number` (now in `plan_resolver.py`) |
| `new` renders intent file from a packaged jinja2 template via `importlib.resources` | ✅ `workflow/templates/intent.md.j2`, loaded with `importlib.resources.files(...)` |
| `new` opens the file in `$EDITOR` (fallback `vi`) | ✅ `_open_in_editor` |
| `new` runs `git add` on the created file in `master`; does not commit | ✅ `git_add` helper added to `git_helpers.py` and called unconditionally |
| `workflow status` Table with columns: #, name, state, last action, next step | ✅ |
| `status` omits `done`/`rejected` unless `--all` | ✅ |
| `status`: last logged action via audit log; next step via the same logic as `_print_command_reminder` (DRY refactor) | ✅ extracted to `workflow/next_step.py` |
| `status`: empty last-action and next-step columns when no audit entry exists | ✅, covered by `test_status_no_audit_entry_leaves_columns_empty` |
| `status`: render next step via `WorkflowStep.render()` (cheatsheet markers/colors) | ✅ |
| `status`: footer prints lowest free sequence number via the existing helper | ✅ uses `next_intent_number` |
| `workflow log` Table with timestamp, actor, command, input plan, output plans | ✅ |
| `log` filter options: `--agent`, `--plan`, `--command`, `--since`, `--until` | ✅ |
| `log --plan` routed through `resolve_plan` with case-insensitive substring fallback | ✅ `_resolve_plan_filter` |
| `workflow cheat` already done, no work expected | ✅ untouched |

Nothing in the plan is missing.

## Architecture & fit with conventions

- The extraction of the next-step resolver into `workflow/next_step.py` is a clean DRY
  win and is the right level of abstraction: a small, dependency-light pure-function
  module that both `commands/workflow.py` and `commands/info.py` import. The naming is
  consistent with the rest of the codebase (`find_command`, `find_next_step`,
  `format_next_step_invocation`).
- The split of `next_intent_number` and `_top_level_number_from_name` into
  `workflow/plan_resolver.py` makes sense — it sits next to `find_all_plans` and
  `parse_plan_filename`, and the `commands/workflow.py` `new` command is no longer the
  only owner of "what plan numbers are reserved".
- Adding `git_add` to `workflow/git_helpers.py` matches the existing helper style
  (thin wrapper around `run_command`, `dry_run` kwarg, sentence-style docstring).
- `workflow/templates/__init__.py` is a one-line module that exists only to make the
  package importable for `importlib.resources.files("workflow.templates")`. Correct.
- `info.py` uses the same error-handling pattern as `workflow.py` (write a red
  message to `error_console`, then `ctx.exit(1)`). Consistent.
- Both new Click commands accept `ctx.exit(0)` at the end. Matches the existing
  convention in `workflow/commands/workflow.py`.
- The status table renderer correctly accepts a `Text` cell (from
  `WorkflowStep.render()`) interleaved with plain-string cells, which is supported by
  rich and is the path the cheat sheet already uses.

## Logic notes

### Solid

- `_last_audit_entry_for_plan` walks `reversed(entries)` once and stops at the first
  hit. Since `read_audit_log` returns entries sorted ascending by timestamp, this is
  correct and O(n) in audit-log size per plan.
- `find_source_state_for_command` documents and implements the right policy: prefer a
  state whose command's `transition` equals the plan's current state (the command moved
  the plan into its state), and otherwise fall back to the plan's current state (the
  command did not transition). This correctly handles the
  `test_status_cross_state_next_step_via_transition` case (`todo` defined in PLANNING,
  plan now in TODO) and the same-state case (`build` in IMPLEMENTING with no
  transition).
- `_parse_datetime` correctly normalizes naive datetimes to UTC and accepts both ISO
  datetimes and dates. The fallback to `date.fromisoformat` is largely redundant on
  Python 3.11+ (where `datetime.fromisoformat` already accepts dates), but it is
  harmless and keeps the contract explicit.
- The `--since`/`--until` filters use `<`/`>` (i.e. inclusive bounds). The plan does
  not pin inclusivity down; inclusive matches what most users expect for date filters,
  and the existing test exercises this behavior.
- `next_intent_number` correctly unions plan numbers from `find_all_plans` with audit
  log `output_plans` and `input_plan`, then returns the first gap from 1.

### Minor concerns

1. **`find_source_state_for_command` ambiguity for prompt commands.** When a
   command name appears in multiple states with no transition (e.g. `ask` in `intent`
   and `implementing`, `critique` in `intent` and `planning`), iteration order through
   `config.states.items()` decides which state wins. In practice the same-state branch
   will pick the unique state where the plan currently is, so this is fine for the
   `status` use case. But it is silently order-dependent — worth a one-line comment
   noting that ties are broken by config-iteration order.

2. **`find_step_by_name` returns the first match across any state.** If the next-step
   reminder name is shared by commands in multiple states (e.g. `ask`, `push`,
   `critique`), the rendered marker comes from the first state in the config that
   defines it. For the cheat-sheet-style `WorkflowStep.render()` output (just the name
   + marker + color) this is fine, but if the rendering ever became state-aware (e.g.
   showing transitions) the ambiguity would surface. Not blocking.

3. **`_entry_matches` parameter list is wide enough to need `# noqa: PLR0913,
   PLR0911`.** The function is straightforward but the call site (`_entry_matches(e, agent=..., cmd_filter=..., since_dt=..., until_dt=..., plan_filter=..., plan_name=..., plan_seq_str=..., use_substring=...)`)
   essentially threads a small bag of "compiled filter state". A tiny `_LogFilters`
   dataclass / pydantic model (matching the project's pydantic-for-internal-types
   convention, see `_Constraint` in `plan_resolver.py`) would shrink the signature
   and remove the `noqa`. Stylistic, not blocking.

4. **`_resolve_plan_filter` swallows `WorkflowError`.** This is the documented
   behavior (resolution failure → substring fallback), but the user gets no signal
   that resolution failed, which means a typo in `--plan some/intentional/path` is
   indistinguishable from a misspelled plan slug. Consider printing a one-line
   `dim`/`yellow` notice on stderr that resolution fell back to substring; the plan
   explicitly says "a wider match is more useful than a hard failure" so the behavior
   is right, but visibility into *which* mode was used would help debugging.

5. **`status` next-step rendering depends on the `command` having a `.render()`
   that produces a `rich.text.Text`.** All three subclasses of `WorkflowStep` do, so
   this works today. The type annotation in `info.status` is `str | Text`, which is
   accurate.

6. **`new` uses `intent_path.relative_to(project_dir / RepoName.master)` even in
   dry-run.** This is a pure path operation and works without the file existing, so
   it is fine — but it bears mentioning because the surrounding branch is gated on
   `if dry_run` for the file-write side. The current placement (`git_add` *outside*
   the `if dry_run / else`) is intentional: `git_add` itself respects `dry_run` and
   prints the would-be command. Correct.

## Security

- **`workflow new {slug}` does not validate the slug.** The slug is interpolated
  directly into a filesystem path:
  ```python
  intent_path = project_dir / RepoName.master / "docs" / "plans" / "intent" / f"{number:03d}_{slug}.md"
  intent_path.parent.mkdir(parents=True, exist_ok=True)
  intent_path.write_text(content, encoding="utf-8")
  ```
  A slug containing `/` (e.g. `foo/bar`) creates an intermediate directory, and a
  slug containing `..` (e.g. `../../../tmp/pwn`) escapes the intent directory and
  writes anywhere the user can write. The created file is then opened by
  `subprocess.run([editor, str(path)])`, so an attacker-controlled slug becomes an
  attacker-controlled path passed to `$EDITOR`. The threat model is "single human
  operator on their own machine", so the practical risk is very low — but this is
  exactly the kind of latent bug that bites later (e.g. when a slug comes from a
  shell snippet or a script). Recommend a small validation step (e.g. require
  `re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", slug)`) and a `WorkflowError` on
  mismatch. Not a blocker.
- `_open_in_editor` uses `subprocess.run([editor, str(path)], check=True)` with a
  list argv (no shell), so `$EDITOR` is not interpreted as a shell command. The
  `# noqa: S603` is appropriate.
- `read_audit_log` already guards against malformed JSONL lines and prints a yellow
  warning per skipped line; `log` therefore degrades gracefully on a corrupt log.
- `--since` / `--until` failures are caught and produce a red error + non-zero exit
  rather than a stack trace.

## Test quality and coverage

The new test module adds **17 tests** and the suite still completes in <2 s:

- `_parse_datetime`: 4 tests (full ISO 8601, date-only, naive→UTC, garbage rejection). Good.
- `new`: 2 tests (numbering against plans+audit log; template + `git_add` invocation).
- `status`: 4 tests (active-only listing + free number, `--all`, audit-driven last action + next step, no-audit-entry blank columns, cross-state next step via transition).
- `log`: 6 tests (no filter, by agent, by command, by since, by plan substring, invalid `--since`).

Coverage of the new modules is solid (`info.py` 84%, `next_step.py` 89%). The
remaining gaps and test-quality observations:

1. **`_resolve_plan_filter` success path is not exercised.** Every `--plan` test
   currently exercises the substring fallback (because the audit-log entries refer to
   plan names that have no on-disk file). A test that creates an actual plan file
   matching a `--plan` value and asserts that the strict equality branch
   (`_plan_matches_filter`) is taken would round out coverage of the documented
   behavior.

2. **`--until` is not exercised.** Symmetrical to `--since`; one extra test would
   suffice.

3. **`--all` does not assert that `rejected` plans appear.** The current test only
   asserts `done`. A second seeded plan in `rejected` would tighten the contract.

4. **`new` dry-run path is not exercised.** `git_add(..., dry_run=True)` and the
   "Would create / Would open in $EDITOR" output is not asserted anywhere. A
   `--dry-run new flux_capacitor` test that asserts the file is *not* created and
   the printed lines appear would protect against accidental regressions.

5. **No direct test for `_print_command_reminder` formatting via the new
   `format_next_step_invocation`.** It is exercised end-to-end through the `todo` /
   `implement` / `check` / `push` tests, but a unit test of
   `format_next_step_invocation` (manual command vs. script vs. prompt) would isolate
   the contract.

6. **`find_source_state_for_command` and `find_step_by_name` are only exercised
   indirectly.** They are small and pure, which makes them ideal candidates for one
   or two parameterized direct tests (especially the order-dependent fallback noted
   above).

7. **`_make_project` test fixture creates a real `master/` directory but no `.git`
   inside any repo.** This is fine for the current command set because `info.py` and
   the `new` command don't shell out to git themselves (the tests mock `git_add`).
   Worth keeping in mind: any test that exercises `transfer_branch_to_main`, `check`,
   `accept`, `reject`, etc. needs a real `git init`, which would push toward the
   integration-test discussion below.

8. **Test-helper proliferation.** `_noop`, `_return`, `_record_command`,
   `_record_path`, `_record_args`, `_record_repo` are all subtly different recorders
   for `monkeypatch.setattr`. They work and the names are descriptive, but the
   pattern is starting to look like a shadow mocking framework. Consider replacing
   with `unittest.mock.MagicMock` (or `pytest-mock`'s `mocker.patch`) in a
   subsequent cleanup; not a blocker for this PR.

## Integration-style tests

The branch is a reasonable place to start adding **one** integration-style test, but
the codebase is not quite ready for full coverage in that style:

- The clean parts to integrate: `workflow new`, `workflow status`, `workflow log`,
  `workflow cheat`. None of these require a working bare/agent repo layout, only the
  `.workflow_root` marker, a `config.yaml`, and (optionally) a `master/` directory
  with a real `git init` for `git_add`.
- A recommended single integration test: `tmp_path` project → `workflow init` (with
  a real `git init` in `master/`) → `workflow new flux_capacitor` (with `EDITOR=true`
  so the editor exits immediately) → `workflow log` → assert one entry, then
  `workflow status` and assert the new intent is listed with no last action / next
  step. This would exercise the audit-log round-trip, the `git add` round-trip, and
  the runtime config loader together, with no mocks.
- Full integration coverage of the transition commands (`todo`, `implement`,
  `check`, `accept`, `reject`) would need a multi-repo (`master`/`bare`/agent
  clones) fixture; that is a meaningful one-off investment and probably belongs in
  its own plan rather than as a follow-on to this branch.

## Smaller observations

- `info.py` module docstring still says "status, log, claude.md", which is accurate
  (the `claude.md` placeholder remains). Fine.
- The `# noqa: FBT001` on `status(ctx, show_all)` is necessary because the
  positional `bool` is forced by the Click decorator. Standard pattern.
- Plan refresh: the diff to `docs/plans/planning/008_claude_md_generation.md` is
  out of scope for the implementation but is a sensible re-baselining of the
  example output against the current `default_config.yaml`. It does not block this
  review.
- `_top_level_number_from_name` is private (`_`-prefixed) but tested indirectly via
  `next_intent_number`. That is the right balance.
- The audit-log timestamp column is rendered with `strftime("%Y-%m-%d %H:%M:%S")`
  with no timezone shown. All write paths use UTC, so this is unambiguous in
  practice; consider appending `" UTC"` in a future polish pass to make the table
  self-documenting.

## Summary

The branch implements every requirement from the plan, adds a clean shared module
for next-step resolution, and ships a useful test suite. The single hardening worth
considering before merge is slug validation in `workflow new`; everything else is
follow-up polish (extra tests for `--until` / strict `--plan` resolution / dry-run,
a tiny `_LogFilters` dataclass, a dim "fell back to substring" hint, and a single
end-to-end smoke test for `new` + `status` + `log`).
