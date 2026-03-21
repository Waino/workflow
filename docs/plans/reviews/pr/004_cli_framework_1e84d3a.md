# PR Review: 004_cli_framework @ 1e84d3a

Plan: `docs/plans/checking/004.1_cli_and_registry.md`, `docs/plans/checking/004.2_prompt_dispatch.md`, `docs/plans/checking/004.3_audit_logging.md`
Commit: `1e84d3aa25f25942a6bb5dab10536a17acc1e7fe`
Reviewer: Augment Agent
Date: 2026-03-21

## Verdict

**Request changes.**

The branch adds a solid amount of scaffolding: the config model grew in the right
direction, `libtmux` was added cleanly, audit logging remains intact, and the code
passes `pytest`, `ruff`, and `mypy`. However, the core CLI framework is not yet
usable in practice. The most important regressions are that the CLI exposes no
subcommands at runtime, the `pr` prompt template cannot be rendered, and prompt
plan resolution is pinned to `master` in a way that conflicts with the intended
reviewer/builder workflow.

## Validation performed

- `pytest -q tests/test_config.py tests/test_audit.py tests/test_plan_resolver.py tests/test_models.py` ✅
- `ruff check workflow tests` ✅
- `mypy workflow tests` ✅
- `CliRunner().invoke(main, ['--config', 'docs/default_config.yaml', 'ask', '001'])` ❌
  - result: `Error: No such command 'ask'.`
- `CliRunner().invoke(main, ['--config', 'docs/default_config.yaml', 'todo'])` ❌
  - result: `Error: No such command 'todo'.`
- `_setup_has_plan(config, 'pr', '004')` against a temp project with a checking plan in
  `master/docs/plans/checking/004_cli_framework.md` ❌
  - result: `Prompt template variable not found: 'plan_name' is undefined`
- `_setup_has_plan(config, 'pr', '004')` against a temp project where the checking plan
  exists only in `reviewer/docs/plans/checking/004_cli_framework.md` ❌
  - result: `No plan found for '004' in repos: ['master']`

## Findings

### 1. The CLI currently exposes none of the new commands

**Severity: High**

The branch introduces a `click.Group` in `workflow/cli.py`, but it never wires the
configured commands into Click early enough for parsing.

- Prompt commands are added inside the group callback via
  `register_prompt_commands(main, config)`.
- Click resolves the subcommand before invoking the callback, so commands like
  `ask`, `pr`, and `full-review` do not exist when parsing starts.
- Script commands are registered in `_SCRIPT_REGISTRY`, but they are never exposed
  as Click subcommands at all.

This leaves the main advertised deliverable of 004.1/004.2 non-functional: `workflow`
has no usable subcommands.

Confirmed behavior:

- `workflow --help` lists no commands.
- `workflow --config docs/default_config.yaml ask 001` fails with `No such command 'ask'.`
- `workflow --config docs/default_config.yaml todo` fails with `No such command 'todo'.`

Files involved:

- `workflow/cli.py`
- `workflow/tmux_dispatch.py`
- `workflow/commands/info.py`
- `workflow/commands/workflow.py`

**Fix direction:** register both script and prompt commands before Click begins command
resolution. A common pattern is to build the group at import time or to use a custom
group class / command factory rather than mutating the command table inside the group
callback.

### 2. The default `pr` prompt cannot render because required template variables are missing

**Severity: High**

`workflow/tmux_dispatch.py` renders prompt templates with only `path_to_plan`, but the
default checking-state `pr` template in `docs/default_config.yaml` requires `plan_name`
and `commit` as well:

- `Perform a thorough code review of the changes on the feat/{{ plan_name }} branch.`
- `Write a report into docs/plans/reviews/pr/{{ plan_name }}_{{ commit }}.md.`

Because `_render_prompt` uses `StrictUndefined`, `workflow pr ...` fails before any tmux
dispatch with `Prompt template variable not found: 'plan_name' is undefined`.

This is not just a config typo: the implementation never computes the reviewed branch
commit or any `plan_name`/`commit` context for prompt rendering, so the PR review flow is
only partially implemented relative to the checking plan and the feature intent.

Files involved:

- `docs/default_config.yaml`
- `workflow/tmux_dispatch.py`

**Fix direction:** either (a) supply the promised render context (`path_to_plan`,
`plan_name`, reviewed commit hash, and any other variables the default config needs), or
`(b)` simplify the default template so it uses only variables the dispatcher actually
provides.

### 3. Prompt plan resolution is hard-wired to `master`, which breaks the intended review flow

**Severity: Medium**

`_setup_has_plan()` calls `resolve_plan(plan_path_str, config.project_dir)` without a
repo hint. `resolve_plan()` defaults to searching only `master`.

That conflicts with the workflow intent:

- builder work happens on `feat/{plan_name}` in the builder repo;
- PR review and fixes happen on the same feature branch in the reviewer repo;
- loose path parsing is supposed to let the operator omit the repo when the command
  makes the target repo obvious.

In the current implementation, `workflow pr 004_cli_framework` fails if the checking
plan only exists in `reviewer/docs/plans/checking/`, even though that is the natural
place for the reviewer to operate after `workflow push`.

This makes the command semantics depend on the user spelling an explicit repo prefix,
which is contrary to the loose-path requirement in `001_idea.md`.

Files involved:

- `workflow/tmux_dispatch.py`
- `workflow/plan_resolver.py`
- `docs/plans/intent/001_idea.md`

**Fix direction:** pass an explicit repo hint derived from the command/workflow stage,
or resolve across the relevant repo set before selecting the canonical plan.

### 4. Test coverage missed the feature's critical execution paths

**Severity: Medium**

The branch adds no tests for the new CLI and prompt-dispatch behavior. The current test
suite exercises config parsing and existing modules well, but it does not cover:

- Click command registration for script commands
- dynamic prompt command registration
- `workflow --help` / subcommand discoverability
- prompt rendering with the default config templates
- `--edit` behavior
- `git_pull` pre-step invocation
- tmux error handling and dry-run output

Because of that gap, the branch can report a clean `pytest` run while the main feature is
still unusable from the CLI.

**Fix direction:** add focused command-level tests with `click.testing.CliRunner` plus
mocked `git_pull`, `send_prompt_to_agent`, and editor/tmux interactions. These tests are
cheap, deterministic, and should catch the failures above without needing real tmux or git
repositories.

## Positive observations

- `workflow.registry.register_script()` now rejects duplicate registrations instead of
  silently overwriting them.
- `workflow.shell.run_command()` uses argument lists rather than shell strings, which is a
  good safety baseline.
- `workflow.tmux_dispatch._render_prompt()` uses Jinja2 `StrictUndefined`, which is the
  right defensive choice for prompt templates.
- The new `TmuxConfig` model and config validation fit the existing Pydantic-based
  architecture well.
- Audit logging, config loading, linting, and typing all remain green on this branch.

## Integration-style tests

Integration-style coverage is worth adding for this feature, but the scope should stay
modest for now.

### Worth the cost now

Yes, for **CLI-level integration tests with mocks/fakes**.

This feature is mostly orchestration logic: Click parsing, config loading, plan
resolution, prompt rendering, audit logging, and calls into git/tmux/editor helpers.
Those pieces interact in ways that are hard to validate with isolated unit tests alone,
and the two major regressions above are exactly the kind of issues that a `CliRunner`
test would have caught quickly.

Recommended near-term integration coverage:

- `workflow --help` shows registered script/prompt commands
- `workflow ask 004` resolves the plan, runs `git_pull`, and calls `send_prompt_to_agent`
- `workflow pr 004` renders the expected review prompt and audit entry
- `workflow full-review` works without a plan argument
- `--edit` cancellation skips dispatch and audit logging
- tmux/session/window failures are surfaced as friendly messages

### Probably postpone

Full end-to-end tests that require a real tmux server, real git clone network, or real
interactive editors should be postponed until later phases. The codebase is not yet set up
for stable, low-maintenance true end-to-end automation of those external tools.

So the right next step is **not** heavyweight E2E; it is **thin integration tests around
the CLI boundary with mocked external dependencies**.