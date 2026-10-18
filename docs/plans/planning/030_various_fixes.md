# 030: Various fixes (planner branch, suppressions, dry-run branch, parallel sessions, init pre-flight)

Input: `intent/030_various_fixes.md` (Decisions 1–31)
Completeness: complete

## Goal

- Projects other than this one never see the planner branch convention (it only lives in intents), so planners
  commit on `main` or on unrelated branches, and `todo` then merges the wrong work or fails confusingly.
- `validate` only warns on `noqa`/`nosec`, missing the other ways of silencing tools.
- Dry-run shows `<dry-run-branch>` even when the branch is known from the plan.
- Every project uses the tmux session `workflow`, so only one project can be open at a time.
- In-band `init` dies late (inside the docker venv step) on a missing `pyproject.toml`, and pre-flight checks fail
  one at a time.

## Scope

In:
- `workflow/templates/agents_md_workflow.md.j2`, `docs/default_config.yaml` (prompts, example config)
- `workflow/commands/workflow.py` (`todo`, `implement`, `accept`, `init`, `add-repo`)
- `workflow/git_helpers.py` (`delete_merged_branch`)
- `workflow/validate.py`, `workflow/tmux_dispatch.py`, `workflow/plan_resolver.py`, `workflow/config.py`,
  `workflow/commands/doctor.py`, new `workflow/tmux_session.py`
- `docs/example_bootstrap_venv.sh`, `docs/source/*.rst`, `HISTORY.rst`, tests, golden fixture

Out:
- Finding the branch that holds a plan (intent/012) — `todo` keeps using the planner's checked-out branch
  (intent/030_various_fixes Decision 2).
- Fixing doctor's `plan/{plan.name}` guess in `_active_branches` for subdivided plans.
- Builder `ask` (implementing state) — not a planner prompt.
- Per-project configurable suppression lists.

Commit per section below (intent/030_various_fixes Decision 1), in this order; each commit passes the hook.

## 1. Parallel projects: session name and session tag (§4)

### Session-name accessor (Decisions 10, 25)
- `TmuxConfig.session_name: str | None = None` ("explicit value; `None` = derive").
- `WorkflowConfig.session_name` property: explicit value, else `sanitize(project_name)`, else `"workflow"`
  (outside a project: `cheat`, `init`). Sanitize = `str.translate` replacing `.` and `:` with `_`
  (tmux does the same, so `sessions.get(session_name=...)` would otherwise miss).
- Replace every `config.tmux.session_name` read (`tmux_dispatch.py` ~15 sites, `plan_resolver.py:797`,
  `tmux_dispatch.py:389`) with `config.session_name`. `rg -n 'tmux.session_name' workflow` must end empty
  except in the property and doctor.
- `docs/default_config.yaml`: drop `session_name: workflow`; keep the `tmux:` block with `prompt_send_delay`
  (or drop the block if it becomes empty) and add a comment line that `session_name` defaults to the project name.

### Session tag (Decision 24)
- New module `workflow/tmux_session.py` (avoids the `tmux_dispatch` → `plan_resolver` import cycle) with:
  - `PROJECT_DIR_OPTION = "@workflow_project_dir"`.
  - `find_workflow_session(config) -> libtmux.Session | None`: `None` if the server is down or no session by that
    name; raises `WorkflowError` if the session's `PROJECT_DIR_OPTION` differs from `str(config.project_dir)`
    (missing tag counts as different). Message names the session, the other project dir (or "an unknown
    project"), and both remedies: set `tmux.session_name` in `config.yaml`, or `tmux kill-session -t NAME`.
    Skip the comparison when `config.project_dir is None`.
  - `active_tmux_repo(config)` moved here from `plan_resolver.py`; it is a soft source, so it catches the
    mismatch `WorkflowError` and returns `None`.
- `create_workflow_session`: `session.set_option(PROJECT_DIR_OPTION, str(config.project_dir))` right after
  `new_session`; add the matching `_preview_tmux` line in dry-run.
- Route `_resolve_session`, `workflow_session_exists`, `is_inside_workflow_session`, `kill_workflow_session`
  through `find_workflow_session`. `attach_workflow_session` calls it before `switch-client`/`attach-session`
  (not in dry-run). Net effect: `start`, `start --reset`, `go`, prompt dispatch and rotation all refuse a foreign
  session; in particular `start --reset` can no longer kill another project's session.

### Doctor note (Decision 11)
- New "Config notes" section (notes, do not affect exit code): when `config.tmux.session_name == "workflow"`
  and `config.session_name` would otherwise differ, note that `tmux.session_name` pins the old default and
  should be removed from `config.yaml` to allow parallel projects. Decision 11 only says "flags"; a note was
  chosen because a pinned name is not a clone-health problem.

### Docs
- `docs/source/cli.rst` (`start`) and the config section of `workflow.rst`: default session name, sanitizing,
  and the refusal on a foreign session. `HISTORY.rst` entry: existing sessions lack the tag, so restart them once
  (`tmux kill-session -t workflow`, then `workflow start`).

## 2. Suppressions check (§2)

`workflow/validate.py` (Decisions 6, 7, 21, 22):
- `SUPPRESSION_MARKERS: tuple[str, ...]`, one sorted, lowercase module constant:
  `#[allow(`, `@suppresswarnings`, `@ts-expect-error`, `@ts-ignore`, `@ts-nocheck`, `eslint-disable`,
  `fmt: off`, `mypy: ignore-errors`, `nolint`, `noqa`, `nosec`, `pragma: no cover`, `pylint: disable`,
  `pyright: ignore`, `shellcheck disable`, `type: ignore`.
- Rename `_check_noqa_nosec` → `_check_suppressions(diff_text, plans_prefix)`, check name `suppressions` in
  `CHECK_NAMES_IN_BAND`/`CHECK_NAMES_OOB`. Match `marker in line.lower()` on added lines.
- Track the current file from `+++ b/<path>` headers and skip added lines of files under `layout.plans_prefix`
  (both call sites have `layout`). Oob joins code-repo diffs with the docs diff; a code repo containing a
  `plans/` dir would also be skipped — acceptable.
- Message: `"{n} suppression comment(s) added"`; details unchanged (the stripped lines).
- Update the name in the module docstring, `git_helpers.py:242` docstring, `docs/source/workflow.rst`
  (×3), `docs/source/caveats.rst:19`, and `tests/test_cli.py`.

## 3. Dry-run renders the expected branch (§3)

`workflow/tmux_dispatch.py` (Decisions 8, 9, 23; amends `004.4_review_fixes` "Dry-run renders placeholders for
prompt branch/commit"):
- `_git_metadata(target, config, plan, *, dry_run)`: in dry-run, if `plan is not None` and
  `target.worktree_name in (builder, reviewer)`, branch = `f"{branch_for_plan(layout, plan)} (expected)"`
  (`workflow/stack.py:82`); otherwise `<dry-run-branch>`. Commit stays `<dry-run-commit>`.
- `_setup_stateless` passes `plan=None`. Real runs unchanged.
- Docs: wherever `cli.rst`/`workflow.rst` describe dry-run placeholders (grep `dry-run-branch`).

## 4. In-band `init` precondition: `pyproject.toml` or bootstrap script (§5.1)

`workflow/commands/workflow.py` (Decisions 12, 13, 14, 26, 27):
- `_check_in_band_init_preconditions`: besides `.git` and lock, require at `HEAD` (via
  `git_path_exists_at_ref`, `git_helpers.py:413`):
  - at least one commit (`git_rev_parse(canon_repo, "HEAD")`, as oob does);
  - `.git-pre-commit-hook` committed (agents clone `HEAD`, so a working-tree-only file is the same late failure;
    extends Decision 26 to the hook);
  - `pyproject.toml` or `docs/bootstrap_venv.sh` committed; if the bootstrap script is the one present, it must be
    committed as `100755`.
- Extract the oob bootstrap-script checks (`is_file`, `os.access`, `git_file_mode_at_ref == _GIT_EXECUTABLE_MODE`)
  into one helper taking `(repo, rel_path)`, used by both modes (oob: `bootstrap_venv.sh` in the docs repo; in-band:
  `docs/bootstrap_venv.sh`). Constant: derive the in-band path from `BOOTSTRAP_VENV_FILENAME`.
- `docs/example_bootstrap_venv.sh` header: drop "Out-of-band only"; describe both locations
  (`canon/docs/bootstrap_venv.sh` oob, `docs/bootstrap_venv.sh` in-band) and that non-python projects commit their
  own, possibly no-op, script.
- `docs/source/docker.rst:81` and `workflow.rst` (in-band setup): document the precondition and that a custom
  `run_agent_in_docker.sh` must run `docs/bootstrap_venv.sh` when present, or the late failure returns.

## 5. Collected pre-flight issues (§5.2)

`workflow/commands/workflow.py` (Decisions 15, 28, 31):
- `_resolve_init_metadata` and the `--skip-hook`-requires-`--oob` check stay fail-fast (they decide mode and
  repo list). Everything in `_check_init_preconditions` is collected.
- Pattern: the per-mode/per-repo check functions take `issues: list[str]` and append instead of `require(...)`;
  each returns early on its first fatal issue (no `.git` → skip that repo's commit/branch/lock/file checks;
  no commit → skip `HEAD`-based checks). `_check_init_preconditions` covers `env.list`, the mode checks and bare
  validity, then raises one `WorkflowError` listing all issues (one per line, `- ` prefix) if any. No new classes.
- `add-repo` (`_add_repo_metadata`): name checks (mode, reserved, duplicate, model validation) stay fail-fast,
  since the name becomes a path; the canon repo checks and bare validity are collected the same way. Reuse one
  per-repo helper for "git repo with a commit and no lock" across oob init and add-repo.

## 6. Planner branch rule (§1)

### Rule text (Decisions 3, 4, 5, 16, 18, 30)
Add a "Planner branches" paragraph to `workflow/templates/agents_md_workflow.md.j2`, after the Decisions list and
before the oob block. Content (wording may be tightened, semantics not):

```
Planner branches: before writing anything for a prompt about plan `{input_plan}` (e.g. `005_foo`):
- Stay on the current `plan/*` branch if it is related to the input plan: it is `plan/{input_plan}`,
  its sequence number is a dotted-numbering ancestor of the input plan's (`005.2` on `plan/005_foo`),
  or the input plan's file exists on this branch but not on `main` (it was created here, e.g. by `subdivide`).
- Otherwise commit pending changes on the current branch (stop and ask if it is unclear where they belong),
  then check out `plan/{input_plan}` if it exists, else create it from `main`.
- On `main`, create the branch already for `ask`, so decisions are recorded on it.
- `architecture` and `mvp` use `plan/002_architecture`.
- `todo` merges whatever branch the planner has checked out; never leave unrelated work on it.
```

- One-line reminder at the top of each covered prompt in `docs/default_config.yaml`: `ask` (intent state),
  `critique` (intent and planning), `plan`, `subdivide`: "First apply the planner branch rule from AGENTS.md."
  `architecture`, `mvp`: "Work on branch `plan/002_architecture` (planner branch rule in AGENTS.md)."
  Use a YAML anchor for the shared line only if it keeps the block-scalar prompts readable; otherwise repeat it.
- Regenerate `tests/fixtures/agents_md_default_section.txt` (`UPDATE_GOLDEN=1`) and this repo's own
  `AGENTS.md` workflow block (`workflow agents_md`).

### `todo` guard (Decisions 2, 19)
- Wrap `resolve_plan(..., command_name="todo")` (`workflow.py:1266`): on `PlanNotFoundError` or
  `SourcesDisagreeError`, re-raise `WorkflowError` (chained) with the original message plus:
  "The planner clone is on branch '{branch}'; todo only sees plans on that branch. Check out the branch holding
  the plan in {planner_repo} and retry." Read the branch only in the failure path (best effort: if that git call
  fails, re-raise the original).
- Same wrapper for `implement` (its planning→todo leg has the identical failure); one helper, used by both.

### Delete the merged planner branch (Decision 17)
- Generalise `delete_merged_branch(canon_repo, repos: Sequence[Path], branch, *, dry_run)`; its dry-run message
  names the repos instead of "reviewer, builder". `accept` passes `(reviewer_repo, builder_repo)`.
- `todo` and `implement` call it with `(planner_repo,)` and the planner branch captured before the transfer, only
  when the transfer step ran, after `_sync_repos_best_effort(..., park=planner)` (a checked-out branch cannot be
  deleted), and never when the branch is `main`. Failures stay warnings (existing behaviour); `-d` stays the
  safety net.

### Doctor: stray planner branch (Decision 29)
- In `_check_worktree` (docs repo only): planner on a `plan/*` branch that is an ancestor of local `main`
  (`git_is_ancestor(repo_path, branch, "main")`) → note "branch 'X' has no commits ahead of main (merged or
  empty)". A note, not an anomaly: a branch just created for `ask` is legitimately empty until the first commit.

### Docs
- `docs/source/workflow.rst`: planner branch section (rule summary pointing to the AGENTS.md block, the `todo`
  guard, branch deletion).

## Verification

- `test_config.py`: session-name accessor, parametrized: explicit value; derived from project name; sanitized
  (`my.proj:x` → `my_proj_x`); no project → `workflow`.
- New tests for `find_workflow_session` with a mocked libtmux server/session: matching tag, foreign tag, missing
  tag, no session, `project_dir is None`; `active_tmux_repo` returns `None` on mismatch. `start --reset`
  dry-run preview includes the `set-option @workflow_project_dir` line.
- `test_doctor.py`: config note shown for pinned `workflow` with a differently named project, absent otherwise;
  planner stray-branch note (merged branch, fresh empty branch, branch with commits → no note).
- `test_validate.py`: parametrize `_check_suppressions` over every marker, plus uppercase `NOLINT`, a marker in a
  `docs/plans/...` file (skipped), a removed line (ignored), and no markers (pass). Update `test_cli.py` name.
- `tmux_dispatch` dry-run: builder prompt with frontmatter branch renders `feat/x (expected)`; plan without
  frontmatter renders `feat/{name} (expected)`; planner prompt and stateless prompt keep `<dry-run-branch>`.
- `test_workflow_commands.py` / init tests: in-band init with neither `pyproject.toml` nor bootstrap script fails
  pre-flight; uncommitted `pyproject.toml` fails; committed bootstrap script with mode `100644` fails; committed
  `pyproject.toml` passes; several simultaneous problems (no `env.list`, no hook, invalid bare) are all listed in
  one error; a missing `.git` suppresses that repo's dependent messages. Same for `add-repo`.
- Integration (`test_integration_workflow.py`): `todo` with the plan committed on a non-checked-out branch fails
  with the branch-naming message and changes nothing; a successful `todo` from `plan/x` deletes `plan/x` in the
  planner clone and on bare; `todo` from `main` (fast-forward path) deletes nothing; `accept` still deletes
  `feat/*` in builder/reviewer.
- `test_agents_md.py` golden contains the "Planner branches" paragraph; `test_next_step.py` unaffected.
- Manual: two projects with different names open simultaneously via `workflow start`; a same-named second project
  is refused with the remedy message.

## Risks

- **Old sessions break once.** Running sessions have no tag and (for most users) the old name; every tmux command
  refuses until the user restarts. Intentional, but `HISTORY.rst` and the error message must make the fix obvious.
- **Rule compliance is not enforced.** The branch rule is prompt text; the `todo` guard catches only "plan not on
  the checked-out branch", not "unrelated work also on it". The "file exists here but not on `main`" clause
  depends on the agent running git correctly.
- **Pre-existing stale `plan/*` branches.** Branches merged before this change are not deleted; Decision 16's
  "check out if it exists" would revive one. The doctor note surfaces it when checked out; users may want a one-off
  `git branch --merged main 'plan/*'` cleanup (mention in `HISTORY.rst`).
- **Branch deletion timing.** Deleting before parking fails (checked out); deleting when the transfer did not run
  could delete a branch carrying unrelated, unmerged work — `-d` refuses that, keep it that way.
- **Suppression scan breadth.** Case-insensitive substring matching on all non-plan files still flags docs or
  string literals mentioning markers (e.g. this project's own `validate.py` and docs). It is a warning; accept it.
- **Session option name** `@workflow_project_dir` must not clash with the `@workflow_{agent}_pane` pattern (no
  agent may be named `project` with suffix `_dir` — practically impossible, but keep the names distinct).

## Dependencies

- None. Amends `004.4_review_fixes` (dry-run placeholders) and refines intent/001 §Planner, already updated.
