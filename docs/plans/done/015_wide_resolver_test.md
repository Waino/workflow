# 015: Wide plan-resolver test

Input: chat discussion (no upstream intent file).

## Goal

Add a single parametrized test that drives `resolve_plan` through a wide
matrix of (plan, command) pairs, covering both happy-path resolutions and
the three error classes the resolver can raise. The matrix is intentionally
broader than any existing test in `tests/test_plan_resolver.py`: it
exercises the subcommand, audit-log, git-history, and (mocked-silent) tmux
sources together, and uses **sequence-number-only fuzzy input** in every
row so the non-path sources do the actual disambiguation.

## Scope

In scope:
- All seven plan states (`intent`, `planning`, `todo`, `implementing`,
  `checking`, `done`, `rejected`).
- Synced plans (present in every clone the workflow would naturally reach)
  and WIP plans (present in one clone only, with optional stale copies).
- Prompt and script commands; commands with `repo_override`,
  `also_valid_in_states`, and `transition`.
- All three resolver-error classes: `PlanNotFoundError`,
  `AmbiguousPlanError`, `SourcesDisagreeError`.
- The `_from_audit_log` widening safety-net (one dedicated row).

Out of scope (covered by existing focused tests):
- Hierarchical sequence numbers (`012.1`, `012.2`).
- Intent vs non-intent shadow disambiguation.
- Review documents (`reviews/critique/`, `reviews/pr/`).
- The `git_provides_repo=True` commit-timestamp tie-break across two repos
  (covered by `TestGitProvidesRepoFlag::test_ties_break_to_more_progressed_state`).

In scope (added per review):
- Ambiguous `ask` resolved by adding a repo hint to the fuzzy input (row 1b).
- The `git_provides_repo=True` code path exercised via the `push` command (row 15).

## Resolver-source quick reference

For each row the test author should be able to reason about the outcome from
this table without re-reading `plan_resolver.py`:

| Source | Contributes to repos | Contributes to states |
|--------|----------------------|-----------------------|
| path (`seq` only) | nothing | nothing |
| subcommand — prompt | agent's repo(s) at every occurrence | (nothing) |
| subcommand — script | agent (or `repo_override`); also `also_valid_in_states`' agents | every state of every occurrence, plus `transition`, plus `also_valid_in_states` |
| audit (latest matching entry, by `build_audit_command_repo_state`) | recorded repo (strict) | recorded state, **widened** to every later state in `_STATE_PROGRESSION` |
| git history (newest `%ct` across clones) | silent unless `git_provides_repo=True` | state of the chosen commit's plan path |
| tmux | active window's repo (+ master) | (nothing) |

## Fixture model

One pydantic dataclass `PlanScenario` per matrix row, plus a single
fixture builder `_build_world(tmp_path, scenarios)` that materialises the
filesystem, audit log, and git commits:

```python
class PlanScenario(BaseModel):
    plan_id: str                                # "001_idea"
    on_disk: list[tuple[RepoName, PlanState]]   # all (repo, state) cells that hold this plan
    audit_entries: list[AuditLogEntry]          # appended in order; timestamps assigned monotonically
    commit_in: list[RepoName]                   # subset of on_disk repos where the file is git-committed
```

Per-row case (driven by `pytest.mark.parametrize`):

```python
class ResolverCase(BaseModel):
    case_id: str                                # parametrize id, e.g. "synced-intent-001-critique"
    plan_id: str                                # which PlanScenario to query
    command: str | None                         # e.g. "ask", "pr", "implement"; None = no subcommand
    fuzzy_input: str | None = None              # override fuzzy input; None → use seq number only
    git_provides_repo: bool = False             # True for "push" rows to exercise that code path
    expected: tuple[RepoName, PlanState] | type[Exception]
```

The fixture builds the world once per test invocation from a shared
`_SCENARIOS` list, then each parametrized row calls
`resolve_plan(fuzzy, tmp_path, config=config, command_name=cmd, git_provides_repo=...)` and
asserts either the resulting `(repo, state)` or `pytest.raises(...)`.

`_active_tmux_repo` is monkeypatched to `lambda _s: None` for the whole
test module to guarantee silent tmux regardless of the developer's
environment.

## Coverage matrix

Older plans — present in every clone the pipeline would naturally reach
(audit reflects the pipeline so far, files committed in every clone that
has them):

| # | Plan | On-disk | Audit (most recent) | Command / fuzzy input | Expected |
|---|------|---------|---------------------|-----------------------|----------|
| 1 | `intent/001_idea` | master, planner, builder, reviewer | `new`, `push` (no audit mapping → silent) | `ask` / `"001"` | `AmbiguousPlanError` (planner+builder both have it, both `intent`) |
| 1b | same | same | same | `ask` / `"planner/001"` | (planner, intent) — repo hint breaks the tie |
| 2 | same | same | same | `critique` / `"001"` | (planner, intent) |
| 3 | same | same | same | `plan` / `"001"` | (planner, intent) |
| 4 | same | same | same | `subdivide` / `"001"` | (planner, intent) |
| 5 | `rejected/002_bad_idea` | all 4 | `reject` → (master, rejected) | `ask` | `SourcesDisagreeError` (audit master, ask planner+builder) |
| 6 | `done/003_early` | all 4 | `accept` → (master, done) | `ask` | `SourcesDisagreeError` |
| 7 | `checking/004_flawed` | builder, reviewer (feat branch) | `check` → (reviewer, checking) | `ask` | `SourcesDisagreeError` |
| 8 | same | same | same | `pr` | (reviewer, checking) |
| 9 | same | same | same | `fix` | (reviewer, checking) |
| 10 | `todo/005_next_in_line` | master | `todo` → (master, todo) | `ask` | `SourcesDisagreeError` |
| 11 | same | same | same | `critique` | `SourcesDisagreeError` |
| 12 | `planning/006_draft` | planner | `plan` → (planner, planning) | `ask` | (planner, planning) |
| 13 | same | same | same | `critique` | (planner, planning) |
| 14 | same | same | same | `subdivide` | (planner, planning) |

WIP plans — exist in one repo, optional stale copies in others:

| # | Plan | On-disk | Audit (most recent) | Command / git_provides_repo | Expected |
|---|------|---------|---------------------|-----------------------------|----------|
| 15 | `intent/007_newly_created` | master (committed newer); planner (committed older, stale copy at intent) | (none — `new` has no mapping) | `push` / `True` | (master, intent) — git ties repos to master (newer `%ct`); subcommand's `repo_override=master` confirms |
| 16 | `planning/008_newly_planned` | planner (committed on plan/008 branch) | `plan` → (planner, planning) | `critique` | (planner, planning) |
| 17 | `todo/009_not_pulled` | master (committed on main) | `todo` → (master, todo) | `implement` | (master, todo) — exercises `also_valid_in_states` |
| 18 | `implementing/010_active_build` | builder; stale `master/implementing/010` from `implement` propagation | `implement` → (builder, implementing) | `build` | (builder, implementing) |
| 19 | `checking/011_under_review` | reviewer (feat branch); stale builder/checking/011 | `check` → (reviewer, checking) | `pr` | (reviewer, checking) |
| 20 | `done/012_merged_without_review` | master | `accept` → (master, done) | `pr` | `SourcesDisagreeError` (audit master, pr reviewer) |
| 21 | `rejected/013_another_bad_idea` | master | `reject` → (master, rejected) | `ask` | `SourcesDisagreeError` |

Safety-net widening (new row beyond user's spec, per critique §2):

| # | Plan | On-disk | Audit (most recent) | Command | Expected |
|---|------|---------|---------------------|---------|----------|
| 22 | `checking/014_post_build_mv` | builder only (agent did `git mv implementing→checking` outside `check`) | `implement` → (builder, implementing) | `check` | (builder, checking) — exercises `_from_audit_log` widening |

## Implementation notes

- New file: `tests/test_plan_resolver_wide.py`. Keep the existing
  `tests/test_plan_resolver.py` focused on per-source unit tests; the wide
  test is end-to-end across all sources.
- Reuse `_make_plan`, `_make_config` patterns from `tests/test_plan_resolver.py`
  by importing them or re-implementing the two-liners locally; do not move
  the helpers (out of scope).
- `_build_world` is the only non-trivial helper. It iterates `_SCENARIOS`:
  for each `(repo, state)` in `on_disk`, write the markdown file; if `repo`
  is in `commit_in`, lazily `git init` the clone, `git add`, `git commit -m`.
  Audit entries are written with `write_audit_log` using monotonically
  increasing timestamps starting at `datetime(2026, 1, 1, tzinfo=UTC)`.
- The single parametrized test is:

```python
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.case_id)
def test_wide_resolver_matrix(case: ResolverCase, world: Path, config: WorkflowConfig) -> None:
    seq = case.plan_id.split("_", 1)[0]   # "001_idea" → "001"
    fuzzy = case.fuzzy_input if case.fuzzy_input is not None else seq
    if isinstance(case.expected, type):
        with pytest.raises(case.expected):
            resolve_plan(fuzzy, world, config=config, command_name=case.command,
                         git_provides_repo=case.git_provides_repo)
        return
    expected_repo, expected_state = case.expected
    plan = resolve_plan(fuzzy, world, config=config, command_name=case.command,
                        git_provides_repo=case.git_provides_repo)
    assert plan.path.parts[0] == expected_repo.value
    assert plan.state == expected_state
```

- `world` and `config` are session-scoped per test module: built once,
  reused across parametrize rows. `_SCENARIOS` is the source of truth for
  on-disk state, audit log, and git commits.

## Risks and caveats

- **Tie-break stability**: rows 18 and 19 include stale copies. If the
  stale repo has a newer git commit timestamp than the canonical repo,
  `_from_git_history`'s state choice may flip the outcome. Mitigation: in
  `_build_world`, commit the canonical copy *last* so its `%ct` is newer.
- **Audit mapping coupling**: rows depend on `build_audit_command_repo_state`
  for the default config. If a new transition is added in the config the
  matrix may need updating. Acceptable — the matrix is intentionally
  driven by config semantics.
- **Ambiguity row (1) is fragile**: any change that makes `find_all_plans`
  dedup at filesystem level would silently turn row 1 from `AmbiguousPlanError`
  into a successful resolution. Mitigation: a comment in the test noting
  why ambiguity is the asserted outcome, pointing to this plan.
- **Test runtime**: ~25 git commits across 5 clones. On Linux this is
  sub-second; flagged here so the file isn't accidentally marked `slow`.

## Files touched

- `tests/test_plan_resolver_wide.py` — new file.
- `tests/test_plan_resolver.py` — add autouse tmux-silencing fixture (see below).
- No production code changes.

## Tmux mock in existing tests

All tests in `tests/test_plan_resolver.py` that call `resolve_plan` (or any
helper that internally calls `_from_tmux`) currently rely implicitly on "no
tmux server alive in CI". That assumption is fragile on developer machines
that happen to have a `workflow` tmux session open.

Add a module-level autouse fixture that makes `libtmux.Server` return a
dead-server mock:

```python
@pytest.fixture(autouse=True)
def _silence_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tmux silent for every test; TestTmuxSource overrides per-method."""
    dead = MagicMock()
    dead.is_alive.return_value = False
    monkeypatch.setattr("workflow.plan_resolver.libtmux.Server", lambda: dead)
```

`TestTmuxSource` tests are unaffected: each method uses `with patch(
"workflow.plan_resolver.libtmux.Server", ...)` which overrides the
monkeypatched value within that context, restoring it on exit.

The same pattern (`monkeypatch.setattr("workflow.plan_resolver.libtmux.Server",
lambda: dead)`) should be used in `tests/test_plan_resolver_wide.py`.

## Commit message

`test: add wide parametrized plan-resolver matrix`
