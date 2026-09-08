# 023: Out-of-band mode for plan version control

While keeping specs together with the source code is a desirable property for my own projects,
it is not always acceptable when collaborating.
Also, automatically added plan transition commits mixed with code changes can be considered noisy.

To support contributions to arbitrary projects,
we need a usage mode where plans are kept out-of-band, i.e. in a separate repository from the source code.

The mode should be selected during `workflow init` (flag `--oob`) and remembered for all subsequent commands.
There is another upcoming feature that requires persistent project-level metadata,
so the method for storing it should not be limited to a single value.
Let's use a single `.workflow.json` in the project root, with a locking mechanism for writes.
It can replace the existing `.workflow_root` marker as the indicator for the project root.


## Multiple code repos

For some projects, it is necessary to
- perform changes in lockstep, adding functionality or fixing bugs in a dependency in order to enable a change,
- or at least check details of and test against a feature branch of a dependency.
The current monorepo assumption doesn't fit this kind of work.


## Example

Let's consider a project called `mypackage`. Here is a subset of files in the project directory:

Normal (in-band) mode.
```
+ bare/
| + HEAD
+ builder/
| + (same items as canon)
+ canon/
| + AGENTS.md
| + docs/
| | + plans/
| |   + intent/
| |     + 001_idea.md
| + .git/
| + pyproject.toml
| + mypackage/
| | + mypackage.py
| + .venv/
+ env.list
+ planner/
| + (same items as canon)
+ reviewer/
| + (same items as canon)
+ run_agent_in_docker.sh
+ workflow.log.jsonl
```

Out-of-band mode.
In addition to `mypackage`, the project includes the source of `dep1` and `dep2`.
```
+ bare/
| + docs/
|   + HEAD
| + mypackage/
|   + HEAD
| + dep1/
|   + HEAD
| + dep2/
|   + HEAD
+ builder/
| + (same items as canon)
+ canon/
| + AGENTS.md
| + docs/
| | + .git/
| | + plans/
| |   + intent/
| |     + 001_idea.md
| + mypackage/
| | + .git/
| | + pyproject.toml
| | + mypackage/
| |   + mypackage.py
| + dep1/
| | + .git/
| | + pyproject.toml
| | + dep1/
| |   + dep1.py
| + dep2/
| | + .git/
| | + pyproject.toml
| | + dep2/
| |   + dep2.py
| + .venv/
+ env.list
+ planner/
| + (same items as canon)
+ reviewer/
| + (same items as canon)
+ run_agent_in_docker.sh
+ workflow.log.jsonl
```

Note the following:
- Multiple git repos
    - `docs` is a separate git repo, instead of being a part of the package repo.
    - There are multiple code repos (`mypackage`, `dep1`, `dep2`).
    - `bare` and each agent become wrapper directories, having a copy of all repos.
- The main package moves deeper
  (e.g. from `project_root/canon/pyproject.toml` to `project_root/canon/mypackage/pyproject.toml`)
- The path to plans is not changed, e.g. `project_root/canon/docs/plans/intent/001_idea.md`


## Git branch model

The `workflow` tool only manages the branch lifecycle of `docs`.
The agents manage most of the git state of the code repos without script automation.
Plans must include a section detailing which branch of each code repo should be worked on,
and on which commit they should be based.

Pushing is still prohibited for the agents.
Planner should not add commits to the code repos.
The `reject` transition only affects plans: the code is intentionally left unmerged.
Thus the only transitions that push multiple repos are `check` and `accept`.
Remaining transitions (`todo`, `implement`, `reject`) only push docs.

All repos should be refreshed with fetch.
This is particulary important, as the agent forgetting to fetch could cause it to start work from stale code.

## Agent interaction

Planner needs copies of all repos for exploration.
Typically planner will check out branches in the code repos, but will not modify files or commit anything.
Planner writes and commits the plan document in the `docs` repo, which is pushed by `workflow`.

When builder receives the plan, it will check out (possibly creating) the branch(es) in the code repo(s).
It will then make changes in one or more of the repos, and commit each.
`check` pushes all repos to `bare`.

Reviewer receives the plan, pulls the changes, and writes its review in `docs`.
The review either gets handed to builder, or then the reviewer directly makes code changes, and commits.
`accept` pushes all repos to `bare`.
On acceptance, `docs` are automatically merged to main, but code branches are not:
typically the next step is pushing the branch upstream and opening a PR there.


## run_agent_in_docker.sh

The mount layout carries over: the script already mounts the agent's subdirectory
(called `repo`, but in oob mode a wrapper directory containing the repos) and the entire `bare`.

The argument contract does change, however. The `hook` and `venv` subcommands use the mount root as
their working directory, but in oob mode a pre-commit hook must run inside a specific code repo.
The script therefore needs an additional argument selecting the repo/workdir within the wrapper.
`docs/example_run_agent_in_docker.sh` is updated as part of this work; updating existing customized
copies in real projects is a post-completion task for the operator.


## Risks

This is a major change, and a change of direction from the original intent, which was to keep plans as documentation in-repo.
As the change affects the expected shape of the project directories, and the number of involved repos,
it requires a thorough and comprehensive analysis during planning.


## Open questions

Should the in-band structure be modified as well, to minimize differences between the two modes?
This would mean adding redundant subdirs to bare:
- Moving the bare git repo from `project_root/bare` to `project_root/bare/mypackage`
But perhaps not moving the main package deeper, as that would change the path to plans.

Resolved: no. `bare` could be forced into the same shape, but the plans path cannot,
so a path-resolution layer is needed either way. Unifying `bare` would force migration of old
projects without simplifying much.


## Clarified decisions

From the planner Q&A round (2024 session, `ask` in intent state):

1. `.workflow.json` stores the code-repo list explicitly; no discovery by scanning `canon/*/.git`.
2. The file is tool-owned *state*, not config: JSON (not YAML), never hand-edited, all writes
   CLI-mediated. The in-band/oob mode is fixed at `init` and cannot be changed afterwards.
3. Atomic write (tmp file + `os.replace`) instead of a lock.
4. `.workflow_root` is replaced by `.workflow.json` in a hard cutover: no fallback, existing
   projects fail with a clear error telling the operator what to do.
5. The workflow `AGENTS.md` must live in the agent wrapper directory so it is always discoverable,
   and stays covered by the existing hash file (which lives outside the wrapper). In oob mode the
   wrapper is not inside any repo, so the file would be unversioned. Symlinking a same-named file
   from the docs repo risks double inclusion in the agent context. See "AGENTS.md in oob mode" below.
   Each code repo may additionally supply its own `AGENTS.md`, scoped to that directory.
6. One `.venv` at the wrapper level, covering all code repos.
7. `bare` keeps its in-band shape (see resolved open question above).
8. The docs-repo directory name is fixed as `docs`, so it need not be stored in `.workflow.json`
   (it is implied by the mode).
9. Projects always have at least one code repo; a docs-only project is out of scope.
10. `init --oob` expects the operator to have created `canon/` with the code repos already cloned in
    (each with its own upstream remotes) and the `docs` repo created/cloned. `init` then creates
    `bare/{repo}` for each, clones the agent wrappers, and writes `.workflow.json`.
11. Remote naming is unified across both modes: the bare repo's remote is always called `bare`,
    leaving `origin` and `upstream` their conventional meanings. This is a breaking change requiring
    migration/re-init of existing projects.
12. `check`/`accept` push every repo that has commits ahead of `bare`, printing a summary.
13. On `check`, code repos are only fetched; branch checkout is left to the reviewer agent.
14. In oob mode `validate` checks only that the plan's per-repo branch/base section exists; no deeper
    validation. Branch naming follows the upstream project's conventions and is not validated.
15. In oob mode the pre-commit check falls back to running `.git/hooks/pre-commit` if it exists, with
    an opt-out available at `init` time.
16. This intent is to be subdivided into multiple planning documents.


## AGENTS.md in oob mode

The symlink idea (decision 5) is a workaround for a problem the existing design already solves:
`AGENTS.md` is *generated* — `workflow agents_md` splices a managed section into a hand-written body.
The preferred resolution is therefore to treat the whole wrapper-level file as a build artifact:

- the hand-written body is version controlled in the docs repo under a *different* name
  (e.g. `docs/agents_md_body.md`),
- `workflow agents_md` renders wrapper `AGENTS.md` = body + managed section,
- no symlink, no double inclusion, hash check unchanged, body under version control.

Side benefit: an agent editing the body file changes nothing until the next `agents_md` run, and the
edit shows up in the docs-repo diff, where `validate`'s file-scope check can reject it. A symlink,
by contrast, takes effect immediately.


## Issues raised in critique

All of the below are resolved by the decisions above and planned in `planning/023.1`–`023.6`;
kept here as the rationale trail.


1. **Wrapper-level venv has no manifest.** With one `.venv` at the wrapper root there is no
   `pyproject.toml` there (the wrapper is in no repo), so `uv sync` has nothing to act on, and the
   existing `venv` subcommand of `run_agent_in_docker.sh` (which mounts `canon` and syncs at the
   mount root) breaks. Needs a decision: a versioned install script/command in the docs repo, an
   install command stored in `.workflow.json`, or a generated workspace `pyproject.toml`.
2. **`run_agent_in_docker.sh` does need changes.** The `hook` and `venv` subcommands run with the
   mounted directory as workdir; in oob mode the hook must run inside a specific code repo, so the
   script contract needs a repo/workdir argument.
3. **Repo paths relative to git repo vs. relative to wrapper diverge.** A plan is
   `{agent}/docs/plans/...` from the wrapper but `plans/...` inside the docs repo. Every consumer of
   `git mv`/`git add`/`git diff --name-only` must use the right one.
   `validate._is_allowed` (which matches `("docs", "plans")`) and `_check_agents_md` (which looks for
   a changed `AGENTS.md`) both need oob-specific behaviour.
4. **`bare` staleness for code repos.** Pushing a feature branch based on fresh upstream commits to a
   stale `bare/{repo}` drags the whole upstream delta along, and agents rebasing against `bare`
   silently work from stale code. There is no command that syncs `bare/{repo}` from canon's upstream;
   one is needed.
5. **Adding a code repo after `init`.** Since `.workflow.json` is not hand-editable, adding `dep3`
   later requires a CLI command (clone into canon, create bare, clone into each wrapper) or a full
   re-init. Decide whether that command is in scope or explicitly deferred.
6. **Terminology collision.** `RepoName` currently means "agent wrapper dir or canon"; in oob mode
   "repo" means an actual git repo. The vocabulary needs to be split (e.g. worktree/wrapper name vs.
   repo name) before the concepts multiply.
7. **Presence-only validation of the per-repo branch section** (decision 14) is a check that cannot
   fail usefully but still requires a documented format. Either drop it or make the block
   machine-readable.
8. **Does the docs repo have an upstream?** If it is local-only, all plan history lives solely in
   `bare/docs` with no off-machine backup.


## Second round decisions

The greenfield assumption is retired: this change requires a **migration script**. General policy
adopted here: prefer single-use migration scripts over backwards-compatibility code that has to be
maintained. Every breaking change below (root marker, remote rename, layout, AGENTS.md handling)
is handled by migration plus a `doctor` check that detects the old shape, not by a compatibility
shim.

1. **AGENTS.md**: the hand-written body lives in the docs repo as `docs/body_of_agents.md`;
   `workflow agents_md` generates the wrapper-level `AGENTS.md` from it. The generated file is not
   under version control. No symlink.
2. **Wrapper venv bootstrap**: option B — a bootstrap script version controlled in the docs repo
   (`docs/bootstrap_venv.sh`), executed by `run_agent_in_docker.sh`. See the option comparison below.
3. **Docker script**: the "no major changes" claim is removed (see section above). Plan the argument
   change and update `docs/example_run_agent_in_docker.sh`; fixing customized copies is a
   post-completion task for the operator.
4. **Vocabulary**: replace the overloaded `project_dir / repo` idiom and the double meaning of
   `RepoName` with five explicit accessors, and rename each usage site to whichever it means:
   `worktree_root`, `git_repo_root`, `plans_root`, `venv_root`, `hook_root`.
5. **`workflow sync`**: new command that refreshes `bare/{repo}` from canon's upstream; `doctor` is
   extended to report bare/upstream divergence.
6. **Per-repo branch metadata** moves into the plan's YAML frontmatter under a `branches` key, so it
   can coexist with other frontmatter metadata. For each repo, either a mapping with `branch` and
   `base`, or `null` meaning "not touched". `branch` must be a syntactically valid branch name but
   need not exist — the agent creates it if missing. `base` may be a branch name or a commit.
7. **`workflow add-repo`** is added for registering a code repo after `init` (clone into canon,
   create bare, clone into each wrapper, update `.workflow.json`). Repo removal is out of scope.
8. **Docs repo upstream** is optional: it may have one, but is not required to.
9. **`.workflow_root` → `.workflow.json`** cutover is handled by the migration script plus a
   `doctor` check for the old marker.
10. See 4.
11. The config-vs-state rule (`config.yaml` hand-authored, `.workflow.json` tool-owned, never
    hand-edited) is written down in the user documentation. `.workflow.json` carries a `version`
    field from the start.
12. `run_hook` is stored **per repo**. For code repos in oob mode, unexpected extra executable hooks
    are a warning, not a failure.
13. Mode immutability is enforced: `init --oob` on an existing in-band project and plain `init` on an
    oob project both fail loudly.
14. The path/vocabulary work lands first as a pure in-band refactor with the existing test suite
    green, before any oob behaviour is added.
15. `info` output becomes a repo × worktree matrix (branch, ahead/behind `bare`, dirty), in oob mode
    only.
16. `doctor` requires every agent-side code repo to have exactly one remote, named `bare`.
17. `check` and `doctor` check for uncommitted changes across all repos, not just one.
18. The generated AGENTS.md managed section states that workflow rules override repo-local
    `AGENTS.md` instructions.
19. Test fixtures are parameterised over both modes; the high-value suites (plan resolution,
    transitions, validate, doctor) run in both.
20. A separate docs repo is the chosen mechanism. The orphan-branch alternative is rejected.


## Wrapper venv bootstrap options

In oob mode the single `.venv` sits at the wrapper root, which belongs to no repo and has no
`pyproject.toml`. Something must tell the tooling how to populate it. Three options:

**A. Install command stored in `.workflow.json`**
(e.g. `uv venv --seed && uv pip install -e mypackage -e dep1 -e dep2`)

- Pro: no new file or convention; captured at `init`/`add-repo` time; trivially per-project.
- Pro: `workflow` can render it into the docker script invocation directly.
- Con: contradicts the "state, not config" rule just established for `.workflow.json` — this is
  configuration, and it is the one thing an operator will want to tweak repeatedly.
- Con: not hand-editable by decree, so tweaking it needs yet another CLI command.
- Con: a shell string in a JSON file passed to a shell — quoting and injection hazards, no room for
  conditionals or non-Python steps (npm, submodules, codegen).
- Con: not version controlled, so its history and rationale are lost.

**B. Bootstrap script versioned in the docs repo** (e.g. `docs/bootstrap_venv.sh`)

- Pro: version controlled and reviewable; its evolution is visible alongside the plans.
- Pro: arbitrary complexity is expressible (multiple package managers, extra tooling, per-repo
  steps), and it is a natural home for future project bootstrapping needs.
- Pro: identical for every worktree; the docker script just executes it inside the container.
- Con: one more file convention to document, and one more path that `validate`'s file-scope rules
  must account for (the docs repo is no longer plans-only).
- Con: an executable script agents can edit; harmless in practice since it only ever runs inside the
  sandbox the agent already controls, but it must never be executed on the host.
- Con: a missing or broken script fails late, at `venv` time.

**C. Generated workspace `pyproject.toml` at the wrapper root**

- Pro: idiomatic uv — `uv sync` works with no custom plumbing; the docker script stays as it is.
- Pro: editors and type checkers see one coherent workspace.
- Con: the wrapper root belongs to no repo, so both the generated `pyproject.toml` and the resulting
  `uv.lock` are unversioned, and each of the four worktrees grows its own drifting lock file.
- Con: fragile against third-party repos, which is the entire point of oob mode: a member may itself
  declare `[tool.uv.workspace]`, use poetry/setuptools, pin conflicting requirements, or not be a
  Python package at all.
- Con: pulls `workflow` into the business of generating build manifests, a job it has no business
  owning.

Chosen: **B**.


## Subdivision

1. `023.1_project_metadata_file` — `.workflow.json`, hard cutover from `.workflow_root`, migration
   script skeleton.
2. `023.2_layout_and_vocabulary` — the path layer and the worktree/repo rename, as a pure refactor.
3. `023.3_bare_remote_rename` — `origin` → `bare` in both modes.
4. `023.4_init_oob_and_bootstrap` — `init --oob`, `add-repo`, AGENTS.md generation, venv bootstrap,
   docker script arguments.
5. `023.5_multi_repo_git_orchestration` — fetch-all, push-ahead-of-bare, `workflow sync`, dirty
   preconditions.
6. `023.6_validate_doctor_and_reporting` — per-repo validate, `branches` frontmatter, doctor checks,
   `info` matrix, mode-parameterised fixtures.

Post-completion tasks for the operator (not automated): run `scripts/migrate_023.py` on every
existing project, and update each project's customized `run_agent_in_docker.sh`.
