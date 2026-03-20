# PR Review: 002_models_and_config

**Plan:** `docs/plans/checking/002_models_and_config.md`
**Commit:** `0e6baa9c624e6df9cb1a7f86c0579cf53c6b4aa4`
**Reviewer:** Augment Agent
**Date:** 2026-03-20

## Summary

The commit implements Plan 002 (Pydantic Models and Configuration) and is largely
well-executed. The models are clean, the discriminated union pattern for `Command`
is correct, config loading follows the intended resolution order, and all 21 tests
pass. Several issues were found during review — one confirmed bug (IndexError), a
few missing validators, and several test coverage gaps.

---

## Issues

### Bug: `Plan.sequence_number` can be empty, causing IndexError

**Severity: Medium**

`Plan.sequence_number` is typed `list[int]` with no minimum-length constraint.
When `sequence_number = []`, calling `plan.sequence_number_str` or `plan.name`
raises `IndexError: list index out of range` at `parts[0]` (confirmed at runtime).
This is a latent crash that will surface whenever plan-parsing logic encounters an
invalid plan file.

**Fix:** Add `sequence_number: list[int] = Field(min_length=1)`.

---

### Missing validator: `review_sub_state` consistency

**Severity: Low–Medium**

The plan specifies that `review_sub_state` must be set when `state == REVIEW`, and
should be `None` otherwise. No `model_validator` enforces this, so both:
- `Plan(state=REVIEW, review_sub_state=None, ...)` — silently accepted
- `Plan(state=INTENT, review_sub_state=ReviewSubState.PR, ...)` — silently accepted

Any code consuming `review_sub_state` would have to defensively check state, which
is error-prone.

**Fix:** Add a `@model_validator(mode="after")` that raises `ValueError` on
inconsistent `state` / `review_sub_state` combinations.

---

### `AuditLogEntry` accepts timezone-naive datetimes

**Severity: Low–Medium**

The `timestamp` field is typed as `datetime`, which accepts both naive and aware
values. The plan's example uses ISO 8601 (implying UTC), and the spec requires
interoperability of JSONL logs across agents. A naive datetime in a log entry would
be ambiguous and could cause subtle bugs during log merging (the `log` command) or
time-range filtering.

**Fix:** Use `from pydantic import AwareDatetime` and type the field as
`timestamp: AwareDatetime`. The `types-pyyaml` stub is already a dev dependency,
so `pydantic`'s extra types are available.

---

### Duplicate agent names silently accepted

**Severity: Low**

`WorkflowConfig.agents` is `list[AgentConfig]` with no uniqueness validation.
If two agents share the same name (confirmed: accepted without error),
`validate_agent_references` would still work (it builds a set), but the duplicate
entry would be confusing and inconsistent with the intent.

**Fix:** Add a `model_validator` checking `len(names) == len(set(names))` for agents.

---

### Malformed YAML surfaces a raw `yaml.ParserError`

**Severity: Low**

`load_config` does not catch `yaml.YAMLError`. A malformed config file raises a
low-level `yaml.scanner.ScannerError` or `yaml.parser.ParserError` with an opaque
stacktrace instead of a friendly "Config file at X could not be parsed" message.

**Fix:** Wrap the `yaml.safe_load` call in a `try/except yaml.YAMLError` and
re-raise as a `ValueError` or custom exception with a clear message.

---

### `load_config` silently uses `cwd` when no project root is found

**Severity: Low**

When `find_project_root()` returns `None` and no explicit `project_dir` is
provided, `load_config` falls back to `Path.cwd().resolve()` for `project_dir`.
This means the config can still be loaded from `~/.config/workflow/config.yaml`
with a silently incorrect `project_dir`. The plan says only `init` and `cheat`
should work without a project root, but enforcement is deferred to the CLI layer.

This is acceptable as a deferral, but the fallback behavior (using cwd as
`project_dir`) should at minimum log a warning so the operator is not confused
when `config.project_dir` points somewhere unexpected.

---

### `docs/default_config.yaml` not included in package distribution

**Severity: Low (future)**

`flit` only includes files under the package directory by default. Since the
default config lives in `docs/`, it will not be bundled when the package is
installed. This will become a problem when `workflow init` tries to copy it.

**Fix:** Either move the default config into `workflow/` (e.g.,
`workflow/data/default_config.yaml`) and use `importlib.resources` to access it,
or add explicit `include` directives in `pyproject.toml`.

---

### Builder modifies an intent file

**Severity: Low (process)**

The commit modifies `docs/plans/intent/001_idea.md` (adding the `.workflow_root`
marker to the directory tree, and rewording the critique prompt). Per `001_idea.md`:
> "The agent is only allowed to change intent files if explicitly commanded,
> and changes should be minimal."

The changes are factually correct and minimal, but touching the intent file during
`build` goes against the workflow rules. These edits should have originated from
the planner or human operator.

---

## Test Coverage Gaps

The 21 tests cover the happy path well but are missing the following:

| Missing test | Related issue |
|---|---|
| `Plan(sequence_number=[])` → `IndexError` on `.name` | Bug #1 |
| `Plan(state=REVIEW, review_sub_state=None)` not validated | Missing validator #2 |
| `Plan(state=INTENT, review_sub_state=PR)` not validated | Missing validator #2 |
| `AuditLogEntry` with naive datetime accepted | `AwareDatetime` gap #3 |
| `WorkflowConfig` with duplicate agent names | Missing validator #4 |
| `load_config` with malformed YAML → friendly error | Error handling #5 |
| `find_project_root(start=None)` uses `cwd` (no argument) | Basic branch |
| `load_config` when YAML contains extra unknown keys | Robustness |
| Verify `_resolve_config_path` tries candidates in order | Internal logic |

---

## Positive Aspects

- The discriminated union `Command` is correctly implemented with
  `Field(discriminator="command_type")`.
- `find_project_root` correctly terminates at the filesystem root.
- `yaml.safe_load` (not `yaml.load`) is used — no YAML deserialization
  vulnerability.
- `dict[PlanState, StateConfig]` with StrEnum keys is confirmed to work with
  Pydantic v2 string-key YAML input (explicitly tested).
- The `validate_agent_references` validator provides fail-fast validation.
- The `noqa: TC001/TC003` hints on imports used at Pydantic runtime are correct.
- The plan document (`002_models_and_config.md`) was correctly updated to reflect
  implementation decisions made during the build.
- All 21 tests pass; enabling pytest in the pre-commit hook is the correct action
  now that tests exist.

---

## Integration Tests

Not worth implementing at this stage. This plan introduces pure models and config
loading; end-to-end integration requires at minimum the CLI command dispatch (plan
003/004) and file-system operations (plan 005+). Revisit after those plans land.

