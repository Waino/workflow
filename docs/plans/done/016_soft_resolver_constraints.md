# 015 — Soft Resolver Constraints

## Problem

Users must type `planner/rejected/002` or `reviewer/checking/004` when resolving a plan
with the `ask` prompt command, even though specifying a repo alone (`planner/002` or
`reviewer/004`) should be sufficient.  The state is already unambiguous from the git
history and the audit log; only the target agent is unknown.

The root cause is that two sources treat their **repo** contribution as a hard constraint
when it should merely be a routing hint, overridable by an explicit path.

### Tracing `planner/002 ask` (plan 002 at REJECTED in every repo)

| source     | repos              | states             |
|------------|--------------------|--------------------|
| path       | {planner}          | —                  |
| subcommand | {planner, builder} | — (prompt)         |
| git        | —                  | {REJECTED}         |
| audit      | {master}¹          | {REJECTED}         |
| tmux       | silent             | —                  |

¹ `reject` transitions to `rejected`; `rejected` has `agent: null` → `master`.

Intersection of repos: `{planner} ∩ {planner,builder} ∩ {master}` = **∅** → `SourcesDisagreeError`.

### Tracing `reviewer/004 ask` (plan 004 at CHECKING in builder + reviewer)

`ask` is only defined in `intent` (planner) and `implementing` (builder); it is **not**
in `checking` (reviewer).  Subcommand therefore yields `repos={planner, builder}`.

| source     | repos              | states                      |
|------------|--------------------|-----------------------------|
| path       | {reviewer}         | —                           |
| subcommand | {planner, builder} | — (prompt)                  |
| git        | —                  | {CHECKING}                  |
| audit      | {reviewer}         | {CHECKING, DONE, REJECTED}  |
| tmux       | silent             | —                           |

Intersection of repos: `{reviewer} ∩ {planner,builder} ∩ {reviewer}` = **∅** → `SourcesDisagreeError`.

## Root-cause classification

| source            | repos meaning                      | correctness |
|-------------------|------------------------------------|-------------|
| path              | user's explicit target             | **hard fact** |
| script subcommand | agent that owns the script file    | **hard fact** |
| git (provides_repo=True) | clone with the newest commit | **hard fact** |
| tmux              | safety guard (watched window)      | **hard fact** (user requirement) |
| prompt subcommand | default routing to agents          | **soft hint** — any agent can receive any prompt |
| audit             | agent that *ran* the last command  | **soft hint** — the file has since propagated to every clone |

Soft hints are useful when the user provides no repo, but they should be **suppressed
when the path already pins the repo**.

## Proposed fix

Introduce a boolean `path_provides_repos` flag (computed once in `resolve_plan` as
`hints.constraint.repos is not None`) and pass it to the two soft-repo sources:

1. **`_from_subcommand`** — when `path_provides_repos` and the command is a `PromptCommand`,
   return `repos=None` instead of the default-routing set.
2. **`_from_audit_log`** — when `path_provides_repos`, return `repos=None` instead of the
   agent-derived repo.

Script subcommand, tmux, and git(provides_repo) are unaffected: they remain hard and
participate in the intersection as today.

### After the fix

**`planner/002 ask`:**

| source     | repos     | states     |
|------------|-----------|------------|
| path       | {planner} | —          |
| subcommand | suppressed| —          |
| git        | —         | {REJECTED} |
| audit      | suppressed| {REJECTED} |
| tmux       | silent    | —          |

Repos = `{planner}`, States = `{REJECTED}` → finds `planner/rejected/002` ✓

**`reviewer/004 ask`:**

Repos = `{reviewer}`, States = `{CHECKING}` → finds `reviewer/checking/004` ✓

### Existing error cases are preserved (no path → no suppression)

| case                    | without path → repos intersection | outcome |
|-------------------------|-----------------------------------|---------|
| `002 ask` (seq only)   | `{planner,builder} ∩ {master}`    | ∅ → SourcesDisagreeError ✓ |
| `004 ask` (seq only)   | `{planner,builder} ∩ {reviewer}`  | ∅ → SourcesDisagreeError ✓ |
| `006 ask` (seq only)   | `{planner,builder} ∩ {planner}`   | `{planner}` → resolves ✓ |
| `001 ask` (seq only)   | `{planner,builder}` (audit silent)| two filesystem hits → AmbiguousPlanError ✓ |

## Implementation steps

### 1. Red the tests first

In `tests/test_plan_resolver_wide.py`, remove the state segment from the two hint cases:

```python
# before
fuzzy_input="planner/rejected/002"
fuzzy_input="reviewer/checking/004"

# after
fuzzy_input="planner/002"
fuzzy_input="reviewer/004"
```

Run `pytest tests/test_plan_resolver_wide.py` and confirm exactly two failures.

### 2. Thread `path_provides_repos` through the soft sources

**`_accumulate_subcommand_contribution`** — add `path_provides_repos: bool` parameter.
For `PromptCommand` branches, skip the `repos.add(...)` call when `path_provides_repos`.

**`_from_subcommand`** — add `path_provides_repos: bool = False` parameter; pass it
through to every `_accumulate_subcommand_contribution` call.

**`_from_audit_log`** — add `path_provides_repos: bool = False` parameter; guard the
`repos=frozenset({repo})` line with `if not path_provides_repos`.

### 3. Wire the flag in `resolve_plan`

After computing `hints`, add:

```python
path_provides_repos = hints.constraint.repos is not None
```

Pass it to the two source calls:

```python
_Source(label="subcommand",
        constraint=_from_subcommand(config, command_name,
                                    path_provides_repos=path_provides_repos)),
_Source(label="audit",
        constraint=_from_audit_log(project_dir, hints.seq, audit_mapping,
                                   path_provides_repos=path_provides_repos)),
```

### 4. Run the full suite

```
pytest tests/
```

All tests must pass, including the previously red hint cases.

### 5. Commit

Two commits in the usual order:

1. `test: expose soft-repo false-ambiguity with shorter path hints` — the test change only.
   As this is a red commit, and the sandbox prohibits `--no-verify`, print the command that the user should run to commit, then stop.
2. `fix: suppress soft repo hints (audit + prompt) when path provides a repo` — the resolver change.

## Risks and non-issues

- **`_union_report` display**: when soft repos are suppressed, the disagree table will
  no longer list audit or prompt rows for those sources; the remaining rows (script,
  tmux, path) are still shown, keeping the error informative.
- **`git_provides_repo=True`** (`push`): git's hard repo is unaffected by the flag; the
  `push` command uses a script subcommand (hard), so this path is not impacted.
- **Tmux**: stays hard as requested; sending to an unwatched agent requires a full path
  that triggers the existing fast-path bypass (both repo and state pinned).
- **No behaviour change** for users who provide no repo in the path — the flag is
  `False` and all sources behave exactly as before.
