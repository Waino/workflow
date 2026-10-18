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
