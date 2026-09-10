# 025: Stacked plans (`implement --onto`)

Input: `intent/025_stacked_plans`
Completeness: complete

## Goal

Fine-grained plans often build on each other (`subdivide` output, small fix intents). Building them
one at a time is right; reviewing and merging each one separately is wasteful. Let several plans
share one feature branch, so the whole stack is reviewed once and merged once.

## Scope

In scope: `implement --onto`, plan frontmatter recording the stack, the branch-name helper, the
`check`/`accept` cascades, mid-stack guards, `reject`'s rollback hint, stack-aware `build`/`pr`
prompts, stack surfacing in `status`/`log`/reminders, a `doctor` stack check, docs, tests, and a
one-paragraph amendment to plan 023.6.

Out of scope: per-plan `validate` scoping (a stacked branch is validated as a whole — accepted), any
oob-mode behaviour (023.x), rejecting a mid-stack plan automatically (manual rollback only), a stack
depth limit as a UX rule (only a runaway guard, see §2).

## Design

### 1. Frontmatter (`workflow/frontmatter.py`, new)

Optional YAML frontmatter at the top of a plan file. Written by `implement --onto` to the *stacked*
plan only (never to the base):

```yaml
---
onto: 024_base_plan
branches:
  docs:
    branch: feat/024_base_plan
    base: 7f3a91c
---
```

Model (pydantic, `extra="allow"` so 023.6 can add keys without breaking older files):

```python
class RepoBranch(BaseModel):
    branch: str
    base: str          # branch tip the plan's own work starts from -- the rollback anchor

class PlanFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")
    onto: str | None = None
    branches: dict[str, RepoBranch] = {}
```

Functions, all pure except the last:

- `parse_frontmatter(text) -> PlanFrontmatter | None` — `None` when the text does not start with
  `---`; `WorkflowError` (naming the file) on malformed YAML or a failed model validation.
- `render_frontmatter(fm) -> str` — `yaml.safe_dump(fm.model_dump(exclude_none=True, mode="json"))`
  wrapped in `---` fences. Never hand-built strings.
- `strip_frontmatter(text) -> tuple[PlanFrontmatter | None, str]` — parsed block plus the body.
- `write_frontmatter(path, fm) -> None` — replace or insert the block, leave the body byte-identical.

The repo key is the docs repo name. Define `DOCS_REPO = "docs"` in `workflow/names.py` and use it as
the only key in in-band mode; 023.2 must import that constant instead of redefining it (see
Dependencies).

### 2. Stack resolution (`workflow/stack.py`, new)

Single home for everything stack-shaped. `plan_resolver` and `frontmatter` are imported here, never
the reverse.

```python
BRANCH_PREFIX = "feat/"
_MAX_STACK_DEPTH = 10      # runaway guard against a hand-edited onto cycle

def read_plan_frontmatter(project_dir, plan) -> PlanFrontmatter | None
def branch_for_plan(project_dir, plan) -> str          # THE branch-name helper
def resolve_stack(project_dir, plan, config) -> list[Plan]        # base-first, includes plan
def stack_child(project_dir, plan) -> Plan | None      # non-rejected plan whose onto == plan.name
def tip_plan_for_branch(project_dir, branch) -> Plan | None
def stack_entries(project_dir, plan, config) -> list[StackEntry]  # prompt rendering, see §7
```

- `branch_for_plan` returns `branches[DOCS_REPO].branch` when present, else
  `f"{BRANCH_PREFIX}{plan.name}"`. One frontmatter read, no chain walk — this is why the resolved
  branch is stored rather than derived (a broken `onto` must not make a plan unroutable).
- `resolve_stack` walks `onto` upward, `resolve_plan`-ing each name, with a `seen` set (cycle →
  `WorkflowError` naming both plans) and `_MAX_STACK_DEPTH`. Members that fail to resolve raise a
  `WorkflowError` that names the offending `onto` value and the plan carrying it.
- `stack_child`/`tip_plan_for_branch` scan `find_all_plans_in_repo(project_dir, RepoName.canon)` and
  read each candidate's frontmatter. Plans in `rejected` are ignored, which is what makes
  "reject the tip, then accept the base" work.
- Plan files are located by name, reusing `state._locate_source` — promote it to a public
  `locate_plan_file(project_dir, repo, file_name)` in `workflow/state.py` and call it from both
  places. Frontmatter is read from the plan's resolved path, falling back to canon by file name,
  because during a stack's life canon's `main` and the branch disagree about state directories.

**Hard requirement:** no `"feat/"` string literal anywhere outside `workflow/stack.py`. Replace the
existing five derivations (`commands/workflow.py:929, 1070, 1143, 1285`, `commands/doctor.py:76`)
with `branch_for_plan`; the prefix assertion at `commands/workflow.py:1114` uses `BRANCH_PREFIX`.
A test enforces this by grepping the package (§10).

### 3. `implement --onto`

New option, argument optional:

```python
@click.option("--onto", "onto", is_flag=False, flag_value="", default=None,
              help="Continue on the feature branch of PLAN (default: the branch builder is on).")
```

`--onto` with no value infers the base from `git_current_branch(builder)` via
`tip_plan_for_branch`; a builder not on a `feat/*` branch is an error.

Preconditions (all `WorkflowError`, all before any mutation):

1. Base plan resolves and is in `implementing`, or in `checking` **and** the reviewer worktree has no
   commits ahead of bare on that branch. New helper
   `git_commits_ahead_of_remote(repo_path, branch) -> int` (`git rev-list --count origin/<b>..<b>`)
   in `git_helpers.py`; a non-zero count errors, naming the count and the reason (the reviewer's `pr`
   document / `fix` commits would be silently excluded).
2. `stack_child(base)` is `None` — otherwise error naming the tip to use instead. Keeps a stack a
   list, not a tree.
3. `branch_for_plan(base)` exists in builder (`git_branch_exists`), and builder is clean.
4. The stacked plan has no `onto` yet, or the same one (idempotent rerun).

Step order (each step idempotent; classified into `ResumePlan` like the existing steps):

| step constant | action |
|---|---|
| `_STEP_IMPLEMENT_TRANSFER` / `_REBASE` | unchanged (only when coming from `planning`) |
| `_STEP_IMPLEMENT_ONTO_CHECKOUT` | fetch builder, checkout the stack branch, `git_pull` ff-only |
| `_STEP_IMPLEMENT_ONTO_FRONTMATTER` | write `onto` + `branches` into the plan file in canon |
| `_STEP_IMPLEMENT_TRANSITION_IMPLEMENTING` | unchanged (`git mv` in canon, same commit as above) |
| `_STEP_IMPLEMENT_ONTO_MERGE_MAIN` | in builder: fetch, `git_merge("origin/main")` |

`base` recorded in the frontmatter is the branch tip read **after** `_ONTO_CHECKOUT` and **before**
`_ONTO_MERGE_MAIN`, i.e. the tip as the previous plan left it. That ordering is deliberate: the merge
pulls in the very commit that carries the frontmatter, so the anchor cannot include it, and rolling
back to the anchor correctly discards both the stacked plan's commits and the merge.

The merge of `origin/main` is what puts the stacked plan file (in `implementing/`) on the branch so
`check` can move it. Steps `_STEP_IMPLEMENT_FETCH_BUILDER`, `_STEP_IMPLEMENT_CHECKOUT_MAIN_BUILDER`
and `create <branch>` are not classified at all in `--onto` mode.

Resume classification: `_ONTO_CHECKOUT` satisfied when builder is already on the branch and not
behind `origin/<branch>`; `_ONTO_FRONTMATTER` satisfied when the file already carries the right
`onto`; `_ONTO_MERGE_MAIN` satisfied when `git_is_ancestor(builder, "origin/main", branch)`.

Audit entry gains `stacked_on: list[str] = []` on `AuditLogEntry` — the full resolved stack,
base-first, excluding the plan itself. Plan files stay the source of truth; the log is the copy that
`status`/`log` can read without parsing plan files.

**Guard for unstacked `implement`** (no `--onto`): refuse when builder is on a `feat/*` branch whose
stack still has a member in `implementing`/`checking`, with a message offering `--onto`. Prevents
silently stranding a stack because the plain path checks out `main`.

### 4. `check` cascades to the whole stack

`check TIP` resolves the stack and transitions **every** member that is not already in `checking`,
in the target repo on the branch (`commit_repo=target_repo`, as today — `transition_plan` locates the
source file by name, so a base plan sitting in `implementing/` on the branch moves correctly). One
commit, one push, as now.

Cascading here rather than at `accept` is what keeps every transition a declared edge of the state
machine (`implementing -> checking`, `checking -> done`) and keeps `status` honest: no plan sits in
`implementing` while its branch is under review.

The cascade also passes `repo_override=target_repo` (previously the `git mv` repo came from the
resolved `plan.path`, while the commit already went to `target_repo` — the two could disagree when
the plan resolved from canon).

Classification lists one `transition to checking` line per member. Audit: `input_plan=TIP`,
`output_plans=[other members]` — the resolver's audit constraint (`plan_resolver` §`_Constraint`)
then knows the cascaded plans are in `checking` too, which is exactly right.

### 5. `accept` cascades and deletes one branch

- `branch = branch_for_plan(plan)`.
- Mid-stack guard: `stack_child(plan)` not `None` → error naming the tip.
- Transition every stack member `checking -> done` in canon (`repo_override=canon`), one commit;
  classification lists one line per member.
- `delete_merged_branch` is called once, unchanged.
- Audit: `input_plan=TIP`, `output_plans=[other members]`.

The same guard applies to `check` (a base plan cannot be checked while a child is stacked on it) and
to the `pr`/`fix` prompt dispatch (`tmux_dispatch._setup_has_plan`): a mid-stack plan errors with the
tip's name. Once the child is `rejected`, `stack_child` returns `None` and the base becomes
actionable again with no extra machinery.

### 6. `reject`

`reject` transitions only the named plan (no cascade). When the plan has frontmatter, print the
concrete recovery instead of a vague warning:

```
024_base_plan and this plan share the branch feat/024_base_plan.
To drop this plan's commits from the branch:
    git -C <project>/builder reset --hard 7f3a91c
```

using the `base` anchor from the plan's own frontmatter. Also warn when the rejected plan is a
mid-stack member (a child exists), since its commits stay under the child's work.

### 7. Prompts

`_setup_has_plan` always passes a new `stack` variable to `_render_prompt` (Jinja2 uses
`StrictUndefined`, so it must be unconditional): `stack_entries(...)`, base-first, **including** the
plan itself as the last element, and empty for an unstacked plan.

```python
class StackEntry(BaseModel):
    name: str
    path: Path            # as resolved, worktree-prefixed, matching the existing {{ plan.path }} convention
    commit_range: str     # "origin/main..7f3a91c" for the root, "7f3a91c..HEAD" for the tip
```

`commit_range` is assembled from the frontmatter anchors only (no git calls): member *i* spans
`base(i)..base(i+1)`, the root starts at `origin/main`, the tip ends at `HEAD`.

Template changes in `docs/default_config.yaml`:

- `build`: separate finished work from the current task, e.g. "The branch already carries finished
  work for: … (commit range …). Read those plans for context only; do not re-implement or modify
  them. Implement only `{{ plan.path }}`." Guarded by `{% if stack %}`, listing
  `{% for e in stack %}{% if not loop.last %}…`.
- `pr`: list every stack member with its plan path and commit range, and state that the review
  covers the whole branch but should be organised per plan.

Existing projects carry their own `config.yaml`; note in the docs that these template edits must be
copied over manually (the tool never rewrites a project's config).

### 8. Surfacing

- `status` (`commands/info.py`): new `stack` column showing the base plan name for stacked plans
  (from frontmatter, blank otherwise).
- `log`: render `stacked_on`, e.g. the command cell becomes `implement (onto 024_base_plan)`.
- Reminders: after `implement --onto`, `check` and `accept`, print one line naming the branch and the
  stack members touched. One helper (`_print_stack_note`) used by all three.

### 9. `doctor`

- `_active_branches` uses `branch_for_plan`.
- New project-level `_stack_anomalies(project_dir) -> list[str]`, rendered like
  `_duplicate_plan_anomalies` and folded into the exit code: a plan whose `onto` target does not
  resolve; a `feat/*` branch (in any worktree or in bare) whose plans are all `done`/`rejected`; a
  plan with `branches[DOCS_REPO].branch` pointing at a branch that exists nowhere.

## Verification

Unit tests:

- `tests/test_frontmatter.py`: parse/render/strip round-trip, no-frontmatter file, malformed YAML,
  unknown extra key preserved, body preserved byte-for-byte by `write_frontmatter`.
- `tests/test_stack.py`: `branch_for_plan` with and without frontmatter; `resolve_stack` for depth 1,
  2, 3; `onto` cycle → `WorkflowError`; depth cap; unresolvable `onto`; `stack_child` ignoring a
  rejected child; `tip_plan_for_branch`; `stack_entries` commit ranges for a 3-plan stack.
- A regression test asserting no `"feat/"` literal in `workflow/**.py` outside `stack.py`.

Integration (`tests/test_integration_workflow.py`, real git repos, existing fixtures):

- Full loop: `implement A` → commit on the branch → `implement B --onto A` → commit → `check B` →
  `accept B`. Assert: both plan files in `canon/docs/plans/done/`, one branch deleted everywhere, no
  `feat/B` ever created, `main` contains both commits, B's plan file carries `onto`.
- `main` advances between `implement B --onto A` and `accept B` → exercises the ff-then-rebase path
  of `transfer_branch_to_main`.
- `check A` and `accept A` refused while B is stacked; `reject B` then `accept A` succeeds.
- `implement B --onto A` refused when A is in `checking` with a reviewer commit ahead of bare, and
  when A already has a child.
- Unstacked `implement C` refused while builder sits on an unfinished stack branch.
- `--onto` with no value infers the base from builder's branch.
- `--dry-run` for `implement --onto` mutates nothing.

Manual: `workflow status`, `workflow log`, `workflow doctor` against a project with a live stack.

Docs: `docs/source/cli.rst` (`implement --onto`, cascade behaviour of `check`/`accept`, `reject`'s
rollback hint) and a "Stacked plans" subsection in `docs/source/workflow.rst`.

## Risks

1. **Branch-helper rollout is all-or-nothing.** A missed site produces a command that operates on a
   `feat/<stacked plan>` branch that does not exist. The grep test plus the five known sites are the
   mitigation; reviewers should re-grep.
2. **Cascade × resume.** `check`/`accept` classification and execution must iterate the same member
   list, or a resumed run transitions a subset. Compute the stack once per command invocation and
   pass the list to both phases.
3. **Frontmatter writing is a new mutation of plan file content.** A planner editing the same plan
   concurrently can conflict; the write happens in canon on `main` immediately before the transition
   commit, so the window is small but real.
4. **State disagreement between canon and the branch.** During a stack's life the same plan sits in
   `implementing/` on canon `main` and in `checking/` on the branch. Anything that reads plan state
   by scanning directories can see either; `resolve_stack` must not depend on state, only on names
   and frontmatter.
5. **Merge commits on the feature branch** (one per `--onto`). Harmless for ff-merge, but a rebase
   fallback in `transfer_branch_to_main` flattens them; the integration test for an advancing `main`
   exists to catch surprises there.
6. **`stack` prompt variable is mandatory.** `StrictUndefined` means any project `config.yaml` that
   uses `{{ stack }}` while running an older binary — or a new binary against a template that omits
   the guard — fails at render time. Always pass the variable, and keep template use guarded.

## Dependencies

- No blocking dependencies; builds on current `main`.
- Lands **before** the 023.x out-of-band series and is written against the current API
  (`RepoName`, `project_dir / repo`); the 023.2 refactor will sweep it along.
- Requires a text amendment to `docs/plans/todo/023.6_validate_doctor_and_reporting.md`:
  `workflow/frontmatter.py`, `PlanFrontmatter`, `RepoBranch` and the `branches` key already exist;
  023.6 extends the model (per-code-repo entries, the `branches-frontmatter` check) instead of
  creating the module.
- `docs/plans/todo/023.2_layout_and_vocabulary.md` declares `DOCS_REPO`; after this plan the constant
  already exists in `workflow/names.py` and 023.2 should move/import it rather than redefine it.
  Flagged here rather than edited, since 023.2's own text is otherwise unaffected.
