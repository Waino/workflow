# 030: Various Fixes

## 1) Tighten planner branch

The `plan/{{ plan.name }}` convention is only specified in intents in this project (001, 012, 013).
Other projects do not see these intents, and are unaware of the convention.
The `todo` command doesn't care what the branch is called, it queries git to find the branch with the plan file.

Let's tighten the plan branch naming by specifying the name in planner prompts: ask, critique, plan, subdivide.
If planner is on main, it should create the branch already during ask and record decisions on that branch.
If planner is already on a plan branch, it should stay on it.

## 2) Warn about ignored types

Add `type: ignore` to validate checks, in addition to noqa

## 3) More informative dry-run

- Let's amend (`004.4_review_fixes` D– "Dry-run renders placeholders for prompt branch/commit")
- Sometimes the branch is known without running git,
  e.g. stacked plans carry onto in the frontmatter: let's then render the actual branch instead of a placeholder.

## 4) Enable multiple projects in parallel

`config.tmux.session_name` should default to the project name, so that multiple projects can stay open simultaneously.
The docs example config should leave the value to this default.

## 5) workflow init ergonomics

### 5.1) pyproject.toml

Workflow init currently dies late with "error: No `pyproject.toml` found in current directory or any parent directory".
This comes from the `run_agent_in_docker.sh` script.
We need to distinguish two cases:
- Python projects
    - Should validate the existence of `pyproject.toml` as a precondition.
    - Should use a `run_agent_in_docker.sh` that requires `pyproject.toml`, e.g. the default script.
      (Probably not feasible to check)
- Non-python projects
    - Should NOT validate the existence of `pyproject.toml` as a precondition.
    - Should NOT use the default script.
      A project-specific non-venv script is mandatory, and its existence should be a precondition.

Let's add a flag, either a boolean `--non-python` or a more generic `--language [str]`. Which one?

### 5.2) list all issues

Preconditions errored one by one, requiring many fix and repeat cycles.
All preconditions should be checked first, and all found issues should be printed.

## Decisions

1. One plan for all items, one commit per item.
2. Correction to §1: `todo` does not query git for the branch holding the plan; it pushes whatever branch the
   planner has checked out (`git_current_branch(planner_repo)`), and fails with a confusing resolver error when the
   plan file lives on another branch. `todo` gets an early guard: fail with a clear message naming the planner's
   current branch when the plan file is not on it. Implementing intent/012's "find the branch holding the plan"
   is out of scope.
3. The planner branch rule lives in the generated AGENTS.md workflow block (single source, also covers ad-hoc
   chat), plus a one-line reminder in each covered prompt.
4. Covered prompts: `ask`, `critique` (intent and planning states), `plan`, `subdivide`, `architecture`, `mvp`.
   `architecture` and `mvp` use `plan/002_architecture`.
5. Branch selection refined: stay on the current `plan/*` branch only if it is related to the input plan, i.e.
   it is the input plan's own branch, or the branch's sequence number is a prefix of the input plan's
   (`005.2` stays on `plan/005_foo`). Otherwise cut `plan/{input_plan}` from `main`. Prevents unrelated work
   (e.g. `ask 007` while on `plan/005_foo`) from being merged into `main` by `todo 005`.
   On `main`, the branch is created already during `ask`, and decisions are recorded on it.
6. The suppression check covers `noqa`, `nosec`, `type: ignore`, `pyright: ignore`, `pragma: no cover`,
   `pylint: disable`, `@ts-ignore`, `eslint-disable`, and `nolint`.
7. The `noqa-nosec` validate check is renamed to `suppressions`.
8. Dry-run renders the branch from `stack.branch_for_plan` (frontmatter branch, else `feat/{name}`) for all
   builder/reviewer prompts. Planner prompts keep the branch placeholder; `{{ commit }}` stays a placeholder.
   Amends `004.4_review_fixes` D– "Dry-run renders placeholders for prompt branch/commit".
9. The real (non-dry-run) dispatch keeps reporting git's current branch.
10. `tmux.session_name` defaults to the project name, sanitized (`.` and `:` replaced with `_`, as tmux itself
    does) so `sessions.get(session_name=...)` matches. The example config drops the `session_name` line.
11. `doctor` flags a config that still pins the old default `session_name: workflow` (e.g. a user-level config
    copied from the old example) when the project name differs, since that prevents parallel projects.
12. §5.1 concerns in-band projects only; oob already bootstraps via `docs/bootstrap_venv.sh`.
13. No `--non-python`/`--language` flag. In-band precondition: `pyproject.toml` or `docs/bootstrap_venv.sh`
    must exist (a non-python project commits its own, possibly no-op, bootstrap script; the example
    `run_agent_in_docker.sh` already prefers it). Would have been: a flag persisted in `.workflow.json` plus a
    mandatory project-local docker script.
14. `.git-pre-commit-hook` stays mandatory in-band, for non-python projects too.
15. `init` collects all pre-flight issues (including `_resolve_init_metadata`, the `--skip-hook` check, and bare
    validity) and prints them together; `add-repo` gets the same treatment. Checks depending on a failed one are
    skipped (e.g. no `.git` skips that repo's commit/branch/lock checks).
