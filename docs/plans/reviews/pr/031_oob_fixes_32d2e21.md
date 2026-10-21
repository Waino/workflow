# PR review: 031_oob_fixes (at 32d2e21)

Branch `feat/031_oob_fixes`, 7 commits over `bare/main`. Plan: `docs/plans/checking/031_oob_fixes.md`.

Gates: full `pytest` green (878 passed), mypy and ruff clean, vulture shows only the usual click/pydantic false
positives. The new tests below are committed as strict xfails in `tests/test_init_oob.py::TestHookPreflight`.

## Summary

All five in-scope sections (§1–§5) are implemented, including Decision 4's post-rebase verify and
`canon_pre_commit` fixes. The plan lists its deviations, and they are reasonable. The code is small and fits the existing
structure. I found three confirmed defects, all in the §5 pre-flight messages/predicate, plus one trust-model concern
with the optional docs hook. Nothing blocks the happy path.

## Findings

### Confirmed defects (xfail tests committed)

1. **`add-repo` hint names a flag syntax that `add-repo` rejects (Medium, UX).**
   `_check_code_repo_init` always appends ` (or pass --skip-hook {repo})`. That form is right for `init --oob`
   (repeatable `--skip-hook NAME`), but `add-repo`'s `--skip-hook` is a boolean flag (`workflow.py:978`). Taken
   literally, the hint (`add-repo dep2 --skip-hook dep2`) exits 2 with click's "unexpected extra argument".
   Test: `test_add_repo_skip_hook_hint_can_be_followed_literally`.
   Fix: give `_check_code_repo_init` a `skip_hint` parameter, so `add-repo` passes `" (or pass --skip-hook)"`
   and `init` passes the `NAME` form.

2. **The `init` rerun hint is always rejected (Low–Medium).** After a successful init, `.workflow.json` is persisted.
   If a `run_hook` code repo's canon HEAD later loses the hook (the operator switches branches or drops it), rerunning
   `init` fails the pre-flight and suggests `--skip-hook dep1`. Following that hint fails with
   "--skip-hook cannot be changed after init" (`_resolve_init_metadata`). The real recovery is the `.workflow.json` edit
   documented for `check`. Test: `test_init_rerun_skip_hook_hint_is_not_rejected`.
   Fix: when metadata is already persisted, use the `check` recovery wording instead
   ("set run_hook: false for NAME in .workflow.json and run workflow agents_md"). Alternatively, suggest `--skip-hook`
   only when `.workflow.json` does not exist yet.

3. **The pre-flight accepts a committed but non-executable hook (Low).** `_check_hook_committed` checks only
   `git_path_exists_at_ref`. Agents then clone a mode-100644 `.git-pre-commit-hook`, `pre_commit_hook_present` is
   false, and `check`/`accept` refuse late. Catching that early is exactly what Decision 9's first layer is for, and
   `_check_executable_bootstrap` already does the mode check for the bootstrap script. Test:
   `test_init_rejects_a_committed_non_executable_hook`.
   Fix: reuse `git_file_mode_at_ref(..., ".git-pre-commit-hook") == "100755"`, as `_check_executable_bootstrap` does.
   This also tightens the in-band path, which shares the helper and has the same pre-existing gap.

### Design / risk

4. **Agents can switch off the docs hook (Medium, trust model).** `_hook_enabled(layout, worktree, DOCS_REPO)` checks
   for the hook in the *agent's own* working tree. A builder or reviewer that deletes `docs/.git-pre-commit-hook` on its
   feature branch makes `check` (and the post-rebase verify in `accept`) skip the docs gate silently, apart from a
   dim line. Before this PR the gate failed loudly. Code repos don't have this problem, because operator-owned
   `run_hook` plus the `_require_pushable` presence check force a failure. This follows intent/031_oob_fixes Decision 8
   and plan Decision 1 as written, so it is not a spec violation. Still, it is a hole an agent can step into. Suggested
   hardening (small): decide docs-hook presence from `bare/main` (`git_path_exists_at_ref(<repo>, "bare/main",
   ".git-pre-commit-hook")`) instead of the working tree. Then a branch that removes the hook still runs (and fails)
   the gate. Needs an operator decision.

5. **`doctor` flags canon code repos too.** `_check_hook_present` runs for every worktree, canon included. The
   anomaly is right for canon (`canon_pre_commit` would fail there). But the tool otherwise deliberately leaves canon
   code-repo branches unmanaged, so an operator checking out an old branch without the hook gets an anomaly (and exit 1).
   Acceptable; mention it in the `doctor` docs or leave canon out.

### Spec conformance

6. §2 asks that the in-band `new` test "still asserts the exact content". It now asserts
   `content.startswith("# 001: Flux Capacitor")`, which proves there is no frontmatter but is not an exact-content
   check. Good enough in practice. Note it, or compare against the rendered template.
7. §4 asks for an integration test of the post-rebase verify "if cheap". Only `_run_docs_hook` is unit-tested, so the
   three wiring sites (`todo`, `_run_transfer_and_todo_steps`, `accept`) have no oob coverage. A typo in a worktree
   argument there would go unnoticed. Acceptable under the plan's fallback clause.
8. §5.2 is covered for `check` only. Nothing tests `accept` (reviewer worktree, `protect_default_branch=False`)
   rejecting a hook-less `run_hook` repo. The shared `_require_pushable` makes this low risk.

### Code quality

9. `_add_repo_metadata` builds `layout.model_copy(update={"metadata": new_metadata})` only so that
   `_check_code_repo_init` can call `layout.metadata.runs_hook(name)`. Passing `run_hook: bool` into
   `_check_code_repo_init` (init passes `layout.metadata.runs_hook(repo)`) is simpler. It also makes room for the
   per-command `skip_hint` that finding 1 needs.
10. `_hook_enabled` uses `layout.git_repo_root(...)`, while `layout.hook_root(...)` exists for exactly "the directory
    the hook runs in". The two are currently identical, but `hook_root` is the right name to depend on.
11. `_check_hook_committed(..., skip_hint: str = "")` uses an empty-string sentinel inside a sentence fragment.
    Fine for now; finding 1's fix should replace it with the full hint text passed by the caller.
12. `new` in oob calls `resolve_default_branch` with `cwd=<planner clone>`. If a code repo's planner clone is missing
    (e.g. partial init), `subprocess.run` raises `FileNotFoundError`, which `new`'s `except` does not catch, so the
    user gets a traceback instead of a `WorkflowError`. Edge case; a `require(path.is_dir(), …)` would cover it.
13. AGENTS.md map: "pre-commit hook runs on check" is incomplete, because `accept` also runs it for reviewer commits.
    Consider "runs on check/accept". This is a template-only change that alters the hash, so make it now rather than
    later.

### Tests

14. `test_code_repos_own_pre_commit_hook_only_warns` now uses a `post-commit` hook. That keeps the test passing but
    drops the case it was written for: a third-party repo whose own `.git/hooks/pre-commit` (e.g. the pre-commit
    framework) must only warn. That case still exists for `run_hook: false` repos. Use
    `_setup_project(..., hook_repos=("dep1",))` (mypackage gets `--skip-hook`) and keep the `pre-commit` hook on
    mypackage.
15. `test_render_workflow_section_oob_has_worktree_map_and_branch_rule` normalises whitespace, so it would not catch a
    broken column alignment (e.g. `format` padding). Low value to fix.
16. The other new tests are good: real git, assertions on `docker_calls.log`, and the "nothing transitioned, nothing
    pushed" assertions on the `check` pre-flight test.

### Docs

17. `docs/example_run_agent_in_docker.sh` line 13 still lists `builder/reviewer/canon (hook)`, but the post-rebase
    verify in `todo`/`implement` runs `hook` with `planner`. Pre-existing, but this PR adds another planner call path.
    One-word fix.
18. The `cli.rst`/`workflow.rst` updates cover every item in the plan's Documentation section.

## Proposed AGENTS.md entry-point edit

None; no new entry point.

## Verdict

Mergeable after findings 1–3 (small, local fixes; the xfails flip to passing). Finding 4 needs an operator decision.
Everything else is optional polish.
