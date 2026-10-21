# 031: OOB fixes from first use

Input: `intent/031_oob_fixes` (Decisions 1–10), `docs/reports/research/031_oob_name_resolution.md`

Completeness: complete

## Goal

The first real out-of-band (oob) project hit several rough edges. Issues:

- `new` doesn't print the path of the file it creates.
- New intents don't remind anyone to declare per-repo branches.
- AGENTS.md doesn't explain the oob directory layout, so the planner tried to apply its branch rule to the worktree
  root, which isn't a git repo.
- `check` requires a pre-commit hook in `docs`.
- A code repo with `run_hook: true` but no hook only failed at hook time.

This plan fixes all of these.

## Scope

In scope: intent items §1–§5, plus the oob
hook invocations that run at the wrapper root (post-rebase verify, `canon_pre_commit`; Decision 4).

Out of scope:
- §6 (`{number}_{slug}` failing to resolve) is postponed until diagnostics have been run on the real project
  (intent/031_oob_fixes Decision 10).
- Changing `--skip-hook` after init. The recovery path is a manual `.workflow.json` edit (see §5).

Suggested commit order: one commit per section below (§1 … §5).

## §1 `new` prints the path

In `new` (`workflow/commands/workflow.py`, `@register_script("new")`), print `Created: {intent_path}` after
`_open_in_editor` returns, so it is the last thing on screen before the next-step line. Use the absolute path so it can
be copy-pasted. Dry-run already prints `Would create: …`, so it is unchanged.

Test: extend `test_new_renders_template_and_calls_git_add` (`tests/test_workflow_commands.py`) to assert the printed
path.

## §2 `new` adds `branches` frontmatter in oob

In oob mode only, prepend a `branches` frontmatter block to the rendered intent. The block has one entry per
registered code repo, in `.workflow.json` order. Every entry is
`{branch: feat/{intent stem}, base: <default branch>}` (intent/031_oob_fixes Decisions 2, 3, 4, 6). The block has no
`docs` entry. In-band output stays byte-identical.

```yaml
---
branches:
  foo:
    branch: feat/031_oob_fixes
    base: main
  bar:
    branch: feat/031_oob_fixes
    base: master
---
# 031: Oob Fixes
...
```

Implementation:
- Build a `PlanFrontmatter(branches={repo: RepoBranch(...)})` and serialize it with `render_frontmatter`
  (`workflow/frontmatter.py`). No hand-built YAML, and the template `intent.md.j2` stays unchanged. Concatenate
  `render_frontmatter(fm) + content`. Put the frontmatter construction in a small pure-ish helper (e.g.
  `_intent_frontmatter(layout, stem) -> PlanFrontmatter | None`, returning `None` in-band) so it can be unit-tested.
- Branch name: `f"{BRANCH_PREFIX}{intent_path.stem}"`, with `BRANCH_PREFIX` imported from `workflow/stack.py`.
  `test_no_feat_branch_literal_outside_stack_module` forbids a `"feat/"` literal anywhere else.
- Base: `resolve_default_branch(layout.git_repo_root(WorktreeName.planner, repo), BARE_REMOTE)`
  (`workflow/git_helpers.py`). This is the same lookup `_require_pushable` uses for `check`'s default-branch
  protection, so the two always agree. See Decision 2 for why the planner clone is used rather than canon.
  If the lookup raises `WorkflowError`, `new` fails before writing the file (fail fast; it means a broken clone).
- Dry-run: the lookup is a read-only query and runs for real (intent/020_robustness Decision 9).

AGENTS.md wording: see §3.

Tests (`tests/test_workflow_commands.py` or `tests/test_info_oob.py`, whichever already has an oob project fixture):
- In an oob project with two code repos, `new` writes the frontmatter above. `parse_frontmatter` round-trips it,
  and the keys are the code repos in `.workflow.json` order.
- A repo whose bare default branch is `master` gets `base: master`.
- In-band output has no frontmatter. The existing in-band test already covers this; make sure it still asserts the
  exact content.

## §3 AGENTS.md: oob worktree map and branch-rule wording

All changes go in `workflow/templates/agents_md_workflow.md.j2`, inside `{% if oob %}` blocks, so the in-band
rendering, and with it `tests/fixtures/agents_md_default_section.txt`, stays byte-identical.

1. **Branch rule.** Right after the "Planner branches" list, add an oob-only paragraph:
   > This project is out-of-band: the worktree root is not a git repository. The planner branch rule applies to the
   > `docs` repo (run git inside `docs/`). The planner never creates or switches branches in code repos.
2. **Worktree map.** Replace the current one-sentence repo list at the start of the oob block with a generic map. All
   four worktree copies of AGENTS.md are identical and hashed, so the map uses `<worktree>/`. It is rendered from the
   code-repo list, including each repo's `run_hook`:
   ```
   <worktree>/              not a git repo
     AGENTS.md              generated; do not edit
     .venv/                 shared virtual env for all repos
     docs/                  git repo: plans in docs/plans/, body_of_agents.md, bootstrap_venv.sh
     foo/                   git repo: code (pre-commit hook runs on check)
     bar/                   git repo: code (no pre-commit hook)
   ```
   Pass `code_repos` to the template as a list of `{name, run_hook}`, not just names. Update `render_workflow_section`
   (`workflow/agents_md.py`) accordingly. The docs repo's hook status is deliberately not rendered (Decision 3).
3. **Frontmatter instructions.** Keep the existing `branches` example. Make the `null` placeholders in it match the new
   default: show `{branch: …, base: …}`, mention `null` for untouched repos, and add:
   > New intents start with a `branches` block that defaults every code repo to `feat/{intent name}`. When writing
   > tickets from an intent, verify that the block is consistent with the intent; carry the block over if it is,
   > else ask the user to confirm. When subdividing, rename each `feat/{intent name}` branch to the ticket's own name.

The `.workflow.json` hash already feeds `.agents_md_hash` (`HashFileV2.workflow_json_sha`), and `run_hook` lives in
`.workflow.json`, so no hash change is needed.

Tests (`tests/test_agents_md.py`):
- The oob rendering contains the map lines, with the correct hook marker per repo (one `run_hook: false` repo).
- The oob rendering contains the branch-rule paragraph.
- The in-band fixture comparison still passes unchanged.

## §4 Docs repo hook is optional

In oob, the docs repo runs the pre-commit hook only if a runnable hook is present (intent/031_oob_fixes Decision 8).
`.workflow.json` is unchanged.

- New helper in `workflow/git_helpers.py` (or next to `_hook_symlink_ok`, whichever doctor and workflow can both
  import without a cycle; `doctor.py` already imports from `git_helpers`):
  ```python
  def pre_commit_hook_present(repo_path: Path) -> bool:
      """Whether the docker script's `hook` subcommand would find a hook to run in *repo_path*."""
      return any(os.access(repo_path / rel, os.X_OK) for rel in (".git-pre-commit-hook", ".git/hooks/pre-commit"))
  ```
  This mirrors the lookup in `docs/example_run_agent_in_docker.sh` (`hook` subcommand) exactly (Decision 1).
- New helper in `workflow/commands/workflow.py`:
  `_hook_enabled(layout, worktree, repo) -> bool`. For `DOCS_REPO` it returns `pre_commit_hook_present(path)`;
  for a code repo it returns `layout.metadata.runs_hook(repo)`. Use it in `_run_repo_push_set_hooks` in place of the
  bare `runs_hook` check. Print the existing dim "skipping" line with a reason that fits the repo ("no hook" for docs,
  "run_hook: false" for code).
- **Post-rebase verify (pre-existing oob bug).** `todo`, `implement` and `accept` pass
  `post_rebase_verify=lambda: _run_pre_commit_hook(config, <worktree>, dry_run=...)` with no `repo`. In oob, the
  docker script then runs at the wrapper root, which has no hook, and fails. Those rebases are docs-repo rebases, so
  route all three call sites through one helper, e.g. `_run_docs_hook(config, layout, worktree, dry_run)`:
  - in-band: unchanged call;
  - oob: skip when `_hook_enabled(layout, worktree, DOCS_REPO)` is false, else call with `repo=DOCS_REPO`.
- **`canon_pre_commit` (pre-existing oob bug, same cause).** It calls `_run_pre_commit_hook(config, canon)` without
  `repo`, so in oob it also runs at the wrapper root and fails. In oob, loop over `layout.repos()` and run the hook
  with `repo=<name>` for every repo where `_hook_enabled(layout, WorktreeName.canon, repo)` is true. Print the same
  dim "skipping" line for the others. In-band is unchanged. A `run_hook: true` code repo without a hook fails loudly
  here (the docker script errors), which is correct. Write the audit entry once, after all repos pass.
- Leave `CodeRepoMeta`/`ProjectMetadata.runs_hook` semantics as they are (docs → `True` means "symlink and validate
  the hook if it ships"). Only touch the docstring so it no longer claims the docs hook always runs.
- `validate` needs no change: oob already only checks hooks whose source ships (`_installed_managed_hooks`).

Tests (`tests/test_oob_handoff.py`; reuse `test_check_skips_the_hook_for_a_repo_with_run_hook_false` as the model):
- With no `.git-pre-commit-hook` in docs, `check` succeeds and runs no docs hook command, while code-repo hooks still
  run.
- With a docs hook present, `check` runs it with `--repo docs`.
- Unit test `pre_commit_hook_present`, parametrized: tracked script executable / not executable /
  `.git/hooks/pre-commit` only / none.
- `canon_pre_commit` in oob runs one hook command per enabled repo (`--repo docs` only if docs has a hook,
  `--repo NAME` per `run_hook: true` code repo) and none for `run_hook: false`. Model it on
  `test_canon_pre_commit_runs_hook`; the in-band variant stays unchanged.
- A post-rebase verify in oob passes `--repo docs`. Use the accept-rebase integration path if an oob variant is
  cheap; otherwise unit-test `_run_docs_hook` with a captured `run_command`.

## §5 Hook pre-flight for code repos

intent/031_oob_fixes Decision 9: three layers.

1. **`init --oob` and `add-repo` pre-flight.** Every code repo with `run_hook: true` must have
   `.git-pre-commit-hook` committed at HEAD in canon. The agents clone HEAD, and an operator-local
   `.git/hooks/pre-commit` is not cloned (Decision 1). Extract the in-band line in `_check_in_band_init_preconditions`
   into a helper `_check_hook_committed(repo, issues)` and call it:
   - in-band: for canon (unchanged message);
   - in `_check_oob_init_preconditions`: for each code repo where `layout.metadata.runs_hook(repo)` is true, and only
     after `_check_repo_with_commit` returned true;
   - in `_add_repo_metadata`: for the new repo when `run_hook`.

   Add a hint to the message for code repos: "…or pass --skip-hook NAME".
2. **`check`/`accept` precondition.** In `_require_pushable` (shared by both, runs before anything is transitioned),
   require `pre_commit_hook_present(repo_path)` for every code-repo entry with `layout.metadata.runs_hook(info.repo)`.
   Message: `"{repo_path} has no pre-commit hook but run_hook is true; commit .git-pre-commit-hook, or set "
   "run_hook: false for {repo} in .workflow.json and run workflow agents_md."`
3. **`doctor` anomaly.** In `_check_worktree` (`workflow/commands/doctor.py`), for code repos (`repo` not in
   `(None, DOCS_REPO)`), add the anomaly `"pre-commit hook missing (run_hook is true)"` when the repo has `run_hook`
   but `pre_commit_hook_present` is false. Add the check before the early `return` for code repos. Pass `run_hook` in
   from `doctor()`, which has the layout, rather than giving `_check_worktree` the whole metadata.

Recovery for an existing broken project (documented, not automated): set `run_hook: false` for the repo in
`.workflow.json`, then run `workflow agents_md`. The map from §3 shows hook status, so the hash changes as intended.

Tests:
- `tests/test_init_oob.py`: oob init fails pre-flight, listing the repo, when a code repo has no committed hook, and
  passes with `--skip-hook NAME`. Parametrize over hook committed / uncommitted (working tree only) / absent.
- `add-repo`: the same pair of cases.
- `tests/test_oob_handoff.py`: `check` fails before any transition (plan still in `implementing`, nothing pushed)
  when a `run_hook: true` code repo with commits has no hook.
- `tests/test_doctor_oob.py`: anomaly reported for a code repo with `run_hook: true` and no hook; nothing reported
  for `run_hook: false`.

## Documentation

Update `docs/source`:
- `cli.rst` `new`: prints the path, and adds `branches` frontmatter in oob.
- `cli.rst` `init`/`add-repo`: a code repo needs a committed `.git-pre-commit-hook` unless `--skip-hook`.
- `cli.rst` `doctor`: the new anomaly.
- `cli.rst` `canon_pre_commit`: in oob it runs per repo (docs only if it has a hook; code repos per `run_hook`).
- `workflow.rst` multi-repo section: the docs hook is optional; the intent frontmatter defaults and the
  planner's job of renaming and nulling them; the `run_hook: false` recovery recipe.

## Verification

- Pre-commit hook (`.git-pre-commit-hook`: black, pytest, mypy) is green for every commit.
- One-off manual check in a scratch oob project (`tests/test_init_oob.py::_make_oob_project` shows the setup):
  - `workflow new foo` prints the path, and the file has the frontmatter;
  - `workflow agents_md` output shows the map;
  - `workflow doctor` flags a hook-less `run_hook: true` repo.

## Decisions

1. "Hook present" means what the docker script's `hook` subcommand would run (`.git-pre-commit-hook` or
   `.git/hooks/pre-commit`, executable). The predicate is used by `check`/`accept`, `doctor` and the docs-hook skip.
   `init`/`add-repo` instead require `.git-pre-commit-hook` committed at HEAD, because agents clone HEAD and never
   receive the operator's local `.git/hooks`.
2. The `base` default comes from the planner clone's `bare/HEAD` via `resolve_default_branch`. Agent clones are made
   from bare, so `refs/remotes/bare/HEAD` is set there. Canon code repos track `origin` and usually lack it
   (023.4_init_oob_and_bootstrap Decision 5). This is the same source `check` uses to refuse commits on the default
   branch.
3. The AGENTS.md map shows `run_hook` per code repo but not the docs hook. Docs-hook presence is a file in the docs
   repo, and rendering it would make the trusted hash depend on docs content.
4. The oob post-rebase verify (`todo`/`implement`/`accept`) runs the hook with `--repo docs`, or skips it when docs
   has none. It is in scope because it is the same docs-hook bug as §4 on a different path.
   Amended: `canon_pre_commit` is in scope too (operator request). In oob it runs the hook once per enabled repo in
   canon, using the same `_hook_enabled` rule.
5. The intent frontmatter is serialized with `render_frontmatter`, not added to `intent.md.j2`, so the YAML
   follows the `PlanFrontmatter` model and in-band output is unchanged.

6. Builder clarifications: the resolver tie-break (formerly intent Decisions 11, 12) was revoked and is out of scope.
   The docs-hook predicate mirrors the docker script (executable `.git-pre-commit-hook` or `.git/hooks/pre-commit`),
   superseding the narrower intent Decision 8 wording. `canon_pre_commit` in oob stops at the first failing repo.

## Risks

- **AGENTS.md drift in existing oob projects.** The template change alters the generated section, so `validate`'s
  `agents-md` check and `doctor` report drift until the operator runs `workflow agents_md`. Mention it in the PR
  description.
- **Stricter pre-flights can block existing setups.** A project inited with a hook-less `run_hook: true` code repo now
  fails `check`/`accept` early instead of at hook time. This is intended, but the message must name the recovery
  path.
- **Branch defaults may be wrong.** Every code repo now defaults to a branch. A planner that forgets to null untouched
  repos produces tickets claiming branches that never exist. `validate` only checks that entries exist
  (intent/023_out_of_band Decision 14), so this shows up only at review. The AGENTS.md wording in §3 is the only
  guard.

## Dependencies

None. §6 of the intent stays open pending diagnostics; a follow-up plan may touch `_git_newest_touch` in
`workflow/plan_resolver.py`, near the §6 change here.

## Deviations

- §1 and §2 landed in one commit (both edit `new`).
- The doctor hook anomaly is added by `_check_hook_present`, called from `doctor()`, instead of a `run_hook` parameter on
  `_check_worktree` (which would have exceeded the linter's complexity/argument limits).
- Test fixtures: `_make_oob_project` now ships a hook in every code repo by default (`hook_repos=()` opts out), because
  the §5 pre-flight rejects hook-less `run_hook` repos. `test_code_repos_own_pre_commit_hook_only_warns` now uses a
  `post-commit` hook, since a `run_hook` repo always ships `pre-commit` now.
- Manual scratch-project verification was not run; the behaviour is covered by the integration tests.
