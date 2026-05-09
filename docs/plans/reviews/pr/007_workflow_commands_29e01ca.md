# PR Review: 007_workflow_commands @ 29e01ca

Plan: `docs/plans/checking/007_workflow_commands.md`
Snapshot reviewed: `29e01ca` (post-merge; the 007 work landed across the
`ad0e2bc` "feat: add workflow script commands" series and was subsequently
modified by plan 012; this review captures the *current* state of the affected
modules)
Reviewer: Augment Agent
Date: 2026-05-09

## Verdict

**Approve with minor comments.**

All nine commands the plan calls for (`init`, `start`, `new`, `todo`,
`implement`, `push`, `accept`, `reject`, `doublecheck`) are present in
`workflow/commands/workflow.py`. They follow the Click + `@register_script`
pattern established by 005.2, write `actor="human"` audit log entries, derive
reminders dynamically from the configured workflow graph via
`workflow/next_step.py`, and respect `--dry-run`. The `init` and `start`
clone/tmux command sequences are unit-tested, the pre-commit rerun in
`check` (formerly `push`) is exercised, and there is end-to-end coverage of
`accept` and `reject`.

Two scope-shifting decisions from plan 012 affect 007 directly and are noted
inline below: `doublecheck` was renamed to `validate`, and the original `push`
(builder→reviewer handoff) was renamed to `check` while a new generic `push`
took over the name. The 007 plan document should ideally have been refreshed
to match — the implementation is correct, but a reader of 007 alone now sees
an out-of-date spec.

The notable substantive findings are (a) `start` is a placeholder that emits
`echo` commands instead of `docker exec` / claude invocations, (b) the
`PlanState.PLANNING` branch in `implement` is unreachable in practice given
the merged resolver's subcommand constraint, and (c) `accept` reads the
reviewer's current branch unconditionally and would happily try to merge
`main` into `main` if the reviewer happens to be on `main`.

## Validation performed

- `uv run pytest -q` → **164 passed**, 3 pre-existing pydantic v1 `class
  Config` warnings in `workflow/commands/cheat.py`.
- `uv run mypy workflow tests` → clean (32 source files).
- `uv run pytest --cov=workflow.commands.workflow --cov=workflow.git_helpers
  --cov=workflow.state --cov=workflow.audit --cov-report=term-missing` →
  - `workflow/commands/workflow.py`: **77%**
  - `workflow/state.py`: **94%**
  - `workflow/git_helpers.py`: **44%** (the helpers are exercised indirectly
    via the command tests; only `git_add` and `git_commit` see direct calls)
  - `workflow/audit.py`: **100%**
- Branch comparison was not used: the work is on `main`. The review uses the
  current snapshot of the affected files.

## Plan compliance

| Requirement (from `docs/plans/checking/007_workflow_commands.md`) | Status |
|---|---|
| `init` creates bare clone + per-agent clone + `.workflow_root` | ✅ `workflow/commands/workflow.py::init` |
| `init`: tests verify shell command order | ✅ `test_init_runs_clone_commands_and_creates_marker` |
| `start` opens tmux windows for each agent + `builder-shell` window | ✅ structurally; **command bodies are placeholders** (see findings) |
| `start`: tests verify intended tmux/docker commands attempted | ✅ `test_start_runs_tmux_commands` |
| `new` finds first free top-level seq across plans + audit log | ✅ via `next_intent_number` (now in `plan_resolver.py`) |
| `new` opens intent in `$EDITOR`; bare-bones template | ✅ Jinja2 template under `workflow/templates/intent.md.j2` (added by plan 006) |
| `todo`: planner→bare→master, transition, commit, push, reminder | ✅ `todo()`; reminder via `_print_command_reminder` → `next_step.find_reminder_name` |
| `implement`: state must be `planning` or `todo`; if planning, do todo's git dance first; transition; commit; push; reminder; pause | ⚠️ The `if plan.state == PlanState.PLANNING:` branch is unreachable given the merged resolver's subcommand constraint — see findings |
| `push` (builder→reviewer): pre-commit rerun + doublecheck + git push + reviewer fetch | ✅ now spelled `check` (plan 012 phase 2) |
| `--target-repo` on `push` and `reject` | ✅ on `check` (`AgentName` choice) and `reject` (`RepoName` choice). The new generic `push` does not take `--target-repo` (it derives it from the resolved plan) |
| `accept`: push reviewer → pull master → transition done → merge → commit → push | ✅ `accept()` |
| `reject`: transition rejected; commit; default repo master, override allowed | ✅ `reject()` |
| `doublecheck` CLI wrapper: resolve plan, call doublecheck, print results, print reminder | ✅ now spelled `validate` (plan 012 phase 1); see `validate()` and `_print_results` / `_print_next_step` |
| `git_commit` with `no_verify=True` for state-transition commits | ✅ All four call sites (`todo`, `implement`, `accept`, `reject`) pass `no_verify=True`. The `check` command does **not** — it relies on the explicit `_run_pre_commit_in_sandbox` step instead. Matches the plan's note. |
| Audit log entries with `actor="human"` for script commands | ✅ via `_write_script_audit_log` |
| Reminder lookup rules (1) next command in same state, (2) first command in transition target, (3) none | ✅ implemented in `workflow/next_step.py::find_reminder_name` |

The plan's "Expected reminders" table:

| Plan says | Implementation says | OK |
|---|---|---|
| `todo` → `implement` | `next_step.find_reminder_name` returns `implement` (next in `planning` state) | ✅ |
| `implement` → `ask` | `implement` in `todo` has `reminder: ask` | ✅ explicit override |
| `build` → `push` | `build` in `implementing` has `reminder: check` (renamed by 012) | ✅ semantically the same |
| `push` → `review` | `check` in `implementing` has `reminder: review` | ✅ |
| `accept` → none | `accept` in `checking` has no reminder and `transition: done` whose state has `commands: []` → returns `None` | ✅ |
| `reject` → none | Same as accept (`transition: done` in config, see note below) | ✅ |

## Architecture & fit with conventions

- Each command is registered with `@register_script("name")` and wrapped by
  `cli._build_script_command`. This keeps the discovery path identical to
  prompt commands (registered via `register_prompt_commands`) and matches
  the cheat-sheet renderer's expectations.
- Every command except `start` (which has a hand-rolled list) goes through
  `_require_project_dir(config)` for the not-in-a-project guard. The
  pattern is consistent.
- Error handling uses one shared `except` tuple
  `(subprocess.CalledProcessError, PlanNotFoundError, AmbiguousPlanError,
  PlanCollisionError, WorkflowError)` followed by `error_console.print(...)`
  and `ctx.exit(1)`. Repeated in five places (`todo`, `implement`, `check`,
  `push`, `accept`, `reject`) — a small `@_workflow_command` decorator or
  `_handle_workflow_errors` context manager would DRY this out, but the
  current shape is readable and consistent with `validate()` which has its
  own slightly different version. Minor.
- `_transition_commit_message`, `_write_script_audit_log`,
  `_run_pre_commit_in_sandbox`, `_open_in_editor`, and `_print_results` are
  all small focused helpers — the 547-line module would otherwise be hard
  to keep mentally. The split is sensible.
- `_EXPECTED_STATES_FOR` is a module-level constant near the top of the
  file. After the merged resolver landed (plan 012), the strict-state
  enforcement happens *after* resolution rather than as a kwarg into
  `resolve_plan`. That matches plan 012's §Decisions and the right
  responsibility lives in the script command.

## Logic notes

### Solid

- `transfer_branch_to_main` (in `git_helpers.py`) refuses to run if the
  source repo is on `main`, which is the right safeguard for `todo`.
- `reject` is idempotent: if the plan is already in `REJECTED`, it returns
  with exit code 0 without a no-op `git mv`. Good.
- `accept` records the reviewer's current branch *before* it gets
  overwritten by `transition_plan`'s `git mv`, then merges that branch into
  `main`. The dry-run path substitutes `"<dry-run-branch>"` so the dry-run
  doesn't hit git for the branch lookup. Good.
- Reminders for state-transition commands always pass the *source* state
  explicitly (e.g. `_print_command_reminder(config, PlanState.IMPLEMENTING,
  "check", plan.name)` in `check`). `find_command` then walks
  `state_config.commands` directly. No `find_source_state_for_command`
  ambiguity here, because the source state is known to the command.

### Concerns

1. **`implement`'s `PlanState.PLANNING` branch is unreachable.** The plan
   says: "`implement` … (must be in state: planning or todo, repo: master);
   If planning: do the same push-pull-merge as todo first". The current code
   honors this in `_EXPECTED_STATES_FOR["implement"] = {PlanState.PLANNING,
   PlanState.TODO}` and has the `if plan.state == PlanState.PLANNING:`
   branch. **But** `resolve_plan(plan_path, project_dir, config=config,
   command_name="implement")` runs the subcommand source, which derives
   `states={todo, implementing}` from the config (`implement` is defined
   under the `todo` state with `transition: implementing`). The resolver
   intersects `{todo, implementing}` with the file's actual state and
   excludes any plan in `planning`. So a planner→master push-pull-merge
   from `implement` will never run; the user has to call `todo` first.

   This is a real spec/implementation disagreement. Either:
   - drop the PLANNING branch and update the plan, or
   - have `_from_subcommand` for `implement` add `planning` to the states
     set (and add the missing planner→master step inside the `if PLANNING`
     branch using `transfer_branch_to_main`).

   Lean toward the first (simpler workflow: always go through `todo`).

2. **`accept` does not refuse to run when the reviewer is on `main`.**
   `git_current_branch(reviewer)` returns whatever HEAD points at; if the
   reviewer has accidentally checked out `main`, the subsequent
   `git_merge(master, "main")` is a noop and the plan is still moved to
   `done`. Compare with `transfer_branch_to_main`, which raises
   `WorkflowError` on `main`. Adding the same guard to `accept` would catch
   the operator error early.

3. **`start` is a placeholder.** Every `tmux new-window` call uses
   `f"echo {project_name} {agent.name} {agent.model}"` as the inner command,
   not the actual `claude --model …` / `docker exec …` invocation. The
   `builder-shell` window similarly echoes `bash` instead of running it.
   The plan accepts that "docker and tmux commands will be written but
   cannot be tested in the sandbox", but currently they *aren't* written —
   only the tmux scaffolding is. A follow-on plan should fill in the
   actual commands; until then, `workflow start` won't produce a usable
   workflow session.

4. **`init` clones unconditionally even if `master/` is not a git repo.**
   The first failure surfaces as a `subprocess.CalledProcessError`, which
   is caught and printed in red — so the UX is correct — but the message is
   the raw subprocess error. A `WorkflowError` pre-check ("`master` does
   not look like a git repo: no `.git` directory") would give a nicer
   message and avoid the half-finished `bare/` if the repo path is wrong.

5. **`reject` does not constrain the resolver via `command_name="reject"`**
   (line 523 calls `resolve_plan(plan_path, project_dir, config=config)`).
   This is intentional — `reject` may be invoked from any non-REVIEW state,
   and the strict-state check happens explicitly afterwards. But it also
   means `--target-repo` is the only signal for which repo to operate in,
   and a plan that lives in `planner/planning/` will not be reachable via
   the default `--target-repo master`. The user must pass
   `--target-repo planner`. Acceptable but worth a docstring sentence.

6. **`config['states']['checking']['commands']['reject'].transition` is
   `done`, not `rejected`.** The actual state transition is hardcoded
   (`transition_plan(plan, PlanState.REJECTED, …)` at line 528), so the
   bug is cosmetic — the cheat sheet shows a `>` arrow next to `reject`,
   but the next step is none. Not a regression introduced by 007 (the
   default config was authored alongside it), and not exercised by any
   test. Worth a one-line fix to `docs/default_config.yaml` (`transition:
   rejected`) at some point.

7. **`_run_pre_commit_in_sandbox` builds the container name as
   `f"auggie-{project_name}-{repo_name}"`.** This hard-codes the user prefix
   (`auggie`). Convention-over-configuration is fine for a single-operator
   tool, but a `config.docker.container_prefix` field (with `auggie` as the
   default) would make this overridable without editing source. Worth
   considering when sandbox configuration becomes its own plan.

8. **`time.sleep(0.1)` after the `implement` reminder** (line 302) is too
   short to be operationally meaningful as the "switch to builder window"
   pause the plan envisions. The plan defers the actual switch — leaving
   the sleep in is harmless, but if it's a placeholder for the future
   pane-rotation work, a comment to that effect would help.

## Security

- Every shelled-out command goes through `workflow.shell.run_command` with
  a list argv (no `shell=True`); no command-injection surfaces in this
  module.
- `_open_in_editor` reads `$EDITOR` and passes it as the first element of
  the argv list. `$EDITOR` is operator-controlled, so this is fine.
- `_run_pre_commit_in_sandbox` builds a `bash -lc` payload from
  `project_name`. `project_name` comes from `config.project_name` or the
  project directory's basename. Both are operator-controlled at this point
  in the workflow's life, but the construction is a soft sharp edge: a
  project directory containing `;` or backtick would inject into the
  bash payload. Recommend either (a) shell-quoting `repo_path` with
  `shlex.quote`, or (b) restricting `project_name` at config-load time to
  `[a-zA-Z0-9_-]+`. Not exploitable today.
- `init` uses `RepoName.master` (a fixed enum value) as the source of the
  bare clone, so an attacker cannot influence which directory is cloned.
- `reject --target-repo X` validates `X` against `RepoName` via
  `click.Choice(RepoName)`. Same for `check --target-repo X`
  (`AgentName`). Good.
- `accept`'s `git_current_branch` reads the reviewer's HEAD without
  validating the result before passing to `git_merge`. A branch name
  containing whitespace or shell metacharacters is fine because
  `run_command` doesn't use a shell, but a branch name beginning with `-`
  could be interpreted as a flag by `git merge`. Real-world risk is
  negligible (operators name their branches), but `git merge --` would
  defend against it.

## Test quality and coverage

- `test_workflow_commands.py` adds 9 command-orchestration tests and 1
  `next_intent_number` test (the latter relocated to `plan_resolver`).
  All exercise the CLI through `CliRunner().invoke(main, …)` rather than
  calling the script function directly, which catches the click decorator
  wiring.
- The recording helpers (`_record_command`, `_record_path`, `_record_args`,
  `_record_repo`, `_return`, `_noop`) are local to the file. They overlap
  with similar helpers in `test_cli.py`. Could be shared via a `conftest.py`
  in a future cleanup.
- Coverage of `workflow/commands/workflow.py` is **77%**; the remaining
  misses are concentrated in the per-command `except` blocks (no negative
  test exercises the resolver-error → red-message → exit-1 path) and in
  the `validate()` CLI command's failure / `--target-repo` paths
  (test_cli.py covers some, but not the
  `WorkflowError`-during-`run_validate` branch).
- `_run_pre_commit_in_sandbox` is exercised end-to-end by
  `test_check_runs_pre_commit_and_prints_review_reminder`, which only
  asserts on the first two argv elements (`["docker", "exec"]`) and the
  container name suffix. The exact `bash -lc` payload is not asserted —
  acceptable per the plan's "do not try to prove docker/tmux command
  correctness" note, but a tighter assertion on the full argv (or a
  separate unit test of `_run_pre_commit_in_sandbox`) would catch
  accidental changes to the container-name convention.
- `test_accept_and_reject_run_in_expected_repos` runs both commands in one
  test, asserting only that `git_push` / `git_pull` / `git_commit` were
  called with paths under `reviewer/` and `master/`. It does not assert
  that the audit log was written or that the reminder was suppressed for
  `accept`. Both would be quick additions.
- The PLANNING branch of `implement` (finding 1 above) has no test, which
  is consistent with it being unreachable.
- `test_implement_prints_explicit_ask_reminder` patches
  `wc.transition_plan` to return a plan whose state is IMPLEMENTING, then
  asserts the printed reminder mentions `workflow ask`. The test would
  still pass even if the second `transition_plan` (TODO → IMPLEMENTING)
  call were dropped — the reminder is computed from `PlanState.TODO`, not
  from the returned plan's state. A direct assertion that
  `transition_plan` was called with target_state=IMPLEMENTING would tighten
  this.
- `test_new_uses_existing_plans_and_audit_log_for_numbering` doesn't assert
  on the rendered template content (the file just needs to exist). Plan
  006 added a separate `test_new_renders_template_and_calls_git_add` that
  covers the template + `git_add` side. Good split.

## Integration-style tests

Most of the 007 commands are amenable to integration tests using a real
git repo under `tmp_path`:

- `init` is the easiest: `tmp_path / "master"` initialised with `git init`,
  one commit, then `workflow init` and assert the resulting `bare/` is a
  bare repo and the agent clones have the commit.
- `todo`, `implement`, `accept`, `reject` need a 4-repo fixture (master,
  bare, planner-clone-of-bare, and at least one of builder/reviewer). This
  is non-trivial — a `conftest.py` fixture would be a one-time cost shared
  across plans 005, 007, and 012.
- `check` additionally needs `docker exec` to be mocked (since the sandbox
  isn't available in CI). The current monkeypatching of `run_command` is
  the right level.
- `start` is the hardest because it requires a live tmux server. Skip it
  in CI; rely on `--dry-run` plus the existing argv assertions.

Recommend one fixture-based integration test for the **happy path**
(`init` → planner edits a file → `todo` → master → `implement` → builder
edits → `check` → `accept`) using real git for the file moves and audit
log writes. That single test would surface most of the multi-step
ordering bugs that unit tests miss.

## Smaller observations

- `RepoName` is an `enum.StrEnum`-style class (`names.py`) — every
  comparison `repo == RepoName.master` works because of the str inheritance.
  Good.
- `init` uses `_write_script_audit_log(config.model_copy(update={"project_dir":
  project_dir}), …)` because at init time `config.project_dir` is `None`
  (no `.workflow_root` exists yet). Necessary, but a bit of a smell — a
  dedicated `_write_init_audit_log(project_dir, dry_run)` helper would be
  cleaner. Minor.
- The `_print_results` helper renders `[✓]` / `[!]` / `[✗]` markers — same
  glyphs the cheat sheet uses. Consistent.
- `docs/plans/checking/007_workflow_commands.md` was not updated when plans
  012 renamed `doublecheck`→`validate` and split `push`. The plan now
  refers to obsolete names. A one-line note at the top
  (`Superseded by plan 012 for the doublecheck/push renames`) would orient
  future readers.

## Summary

007 has been delivered: every command in the spec is implemented, registered
through the dynamic CLI, audit-logged, and dry-run aware. The core findings
are (a) `start`'s body is still a placeholder and the actual claude/docker
invocations need a follow-up, (b) `implement`'s PLANNING branch is dead code
under the current resolver and the spec's "implement from planning" path is
effectively gone, and (c) `accept` should refuse to merge when the reviewer
is on `main`. The rest are tighten-up suggestions: better test assertions
for `_run_pre_commit_in_sandbox`'s argv, a single end-to-end fixture test
for the `todo`→`accept` chain, fixing the `transition: done` → `rejected`
typo in the default config, and shell-quoting the `bash -lc` payload for
defensive robustness.
