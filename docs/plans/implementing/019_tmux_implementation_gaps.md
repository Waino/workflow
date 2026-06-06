# 019 — Tmux implementation gaps

## Input plans

- `docs/plans/intent/011_tmux_pane_rotation.md` — manually-verified proof-of-concept for the
  control-pane split and `join-pane` rotation.
- `docs/plans/intent/001_idea.md` — specs for `start`, `implement`, `check`, and `go`.
- `docs/plans/intent/013_example_walkthrough.md` — the end-to-end handoff narrative that pins down
  the rotation behaviour of `implement`, `check`, and `go`.
- `docs/plans/reviews/followthrough/bf55b86.md` — gaps 1–4, which this ticket closes.

## Goal

Implement the side-by-side "control pane" tmux layout and the pane-rotation behaviour so the human
operator's workflow shell follows the active agent. Concretely, close follow-through gaps 1–4:

1. `workflow go` — rotate the control pane to a chosen window and print the target repo's git status.
2. `workflow start` — create the docked control pane so `go` (and the handoff rotations) have a pane
   to move.
3. `workflow implement` — rotate to the builder window and leave the builder ready on a feature branch.
4. `workflow check` — rotate to the reviewer window after the builder→reviewer handoff.

## Scope

- In scope: control-pane creation in `start` (with its id recorded in a session option); a reusable
  rotation helper; wiring it into `go`, `implement`, and `check`; the builder feature-branch setup in
  `implement` with preconditions; a `manual_shell` `AgentConfig` field; tests.
- Out of scope: review-path fuzzy resolution (gap 6), the `full review` prompt (gap 7, already done),
  automatic agent dispatch on every command (intent/001 "stretch goal"), and any multi-control-pane or
  resilient pane-id recovery beyond the precondition checks described below.

## Background — current state

- `start` (`workflow/commands/workflow.py:211-270`) creates the session and one window per agent plus a
  `builder-shell` window using raw `tmux` subprocess calls via `run_command(...)`. There is no split and
  therefore no control pane.
- `implement` (`workflow/commands/workflow.py:361-406`) transitions the plan to `implementing`, commits,
  and pushes `master`. It then sleeps and stops at `# TODO: switch to the builder tmux window.`
  (line 395). No fetch, branch checkout, or branch creation is performed in the builder repo.
- `check` (`workflow/commands/workflow.py:498-535`) runs the pre-commit hook and `validate`, transitions
  to `checking`, commits, pushes the builder branch, and `git_fetch`es the reviewer. No pane rotation.
- `tmux_dispatch.send_prompt_to_agent` (`workflow/tmux_dispatch.py`) already resolves
  `libtmux.Server() → session → window`, calls `window.select()`, waits `tmux.prompt_send_delay`, and
  `send_keys`. It raises `WorkflowError` (with a "run workflow start" hint) when the server/session/window
  is missing.
- `plan_resolver._active_tmux_repo` inspects the live session tolerantly, catching `OSError` and
  `libtmux.exc.LibTmuxException` and returning `None`.
- The POC in intent/011 establishes the base mechanism (control pane created with a horizontal split
  inserted *before* an agent pane; rotation via `tmux join-pane -h -b`). The refined POC
  `tmux_pane_rotation_with_user_option.py` (repo root) records the control pane's id in a session option
  at setup and locates it by id during rotation, removing the index-0 / active-pane / pane-count
  assumptions (critique A1–A3). This ticket productionises the refined approach.

## Behaviour pinned by the walkthrough (013)

- `start`: "Planner is visible in the tmux split window." → the control pane is docked in the first
  agent window after `start`.
- `implement`: "Rotate the tmux panes so that builder is visible. Pause for a moment."
- `check`: "Rotate the tmux panes so that reviewer is visible."
- `go builder`: "The `go` command should not just rotate the tmux, but also print the git status of the
  target repo." → `go` prints `git status` of the target repo after rotating.

## Design decisions

### D1. Where the layout/rotation code lives

Add the layout/rotation operations to `workflow/tmux_dispatch.py`, next to `send_prompt_to_agent`, and
extract a shared session resolver so the new code and `send_prompt_to_agent` stop duplicating the
`Server → session → window` lookup.

- Reasoning: `tmux_dispatch.py` already owns "talk to the live tmux session", including the
  server-not-running / session-missing `WorkflowError` hints. A separate module would fragment that and
  duplicate session resolution. This keeps it DRY without adding a module.
- **All** layout operations — session/window creation in `start`, the control-pane split, and rotation —
  go through `libtmux`, not raw `tmux` subprocess calls (B1). This gives one access stack to maintain and
  test. Dry-run is handled uniformly via a small shared helper that renders the intended tmux command
  (e.g. `would run: tmux join-pane …`) and returns without touching the live server, so dry-run output is
  consistent regardless of which layout operation is being previewed.

### D2. The control pane and how it is located

There is exactly one control pane in the session. At `start`, after the control pane is split off, its
stable tmux pane id (`%N`) is recorded in a **session user option** `@workflow_control_pane`
(`session.set_option("@workflow_control_pane", pane.pane_id)`). Rotation locates the control pane by that
recorded id (`session.show_option("@workflow_control_pane")` → `server.panes.get(pane_id=…)`) and moves
*that* pane, regardless of focus, `pane-base-index`, or how many panes the host window has.

This replaces the POC's "control pane is active pane 0 of the active window" assumption, which broke for
operators with `pane-base-index 1` set and for any manually-split window (critique A1–A3). Pane ids are
stable across `join-pane`, so the recorded id stays valid across rotations. `join-pane -h -b -s
<control-pane-id> -t :{target_window}` inserts the control pane to the left of the target window's pane.
The refined approach is captured in `tmux_pane_rotation_with_user_option.py` (repo root), to be confirmed
manually before implementation.

### D3. The `manual` target ↔ the manual-shell window

intent/001 names the manual shell window `builder-shell` in `start`, but specifies `go manual` for the
same window. Decision: **keep the window name `builder-shell`** and map the `go manual` choice to it.
Rather than hardcoding which agent backs the manual shell, add a boolean `manual_shell` field to
`AgentConfig` (default `False`) marking the agent whose repo the manual shell execs into; validate at
config load that **at most one** agent sets it (B4). `go manual` resolves its git-status repo from that
agent (the builder in the default config). If no agent sets `manual_shell`, `go manual` rotates but skips
the repo status.

### D4. Rotation failure policy — fatal in `go`, non-fatal (but loud) in `implement`/`check`

- In `go`, rotation is the entire purpose, so a `WorkflowError` is fatal (printed via
  `print_workflow_error`, `ctx.exit(1)`), consistent with the other script commands.
- In `implement`/`check`, rotation is cosmetic and happens *after* the irreversible cross-repo git
  handoff has already committed/pushed. A tmux failure there must **not** fail the command. These call a
  best-effort wrapper that catches `WorkflowError` and prints a clearly-visible warning to
  `error_console` — non-fatal, but never silently swallowed (A5). No config knob is added to suppress
  this: it is a personal tool and the code can be changed if different behaviour is wanted.

### D5. Builder feature-branch setup in `implement` (decided)

Decision: **`implement` owns the builder feature branch.** It fetches the builder, checks out `main`,
pulls, then creates `feat/{plan_name}` from the freshly-pulled `main`, leaving the builder ready on its
branch. This satisfies intent/001's `implement` step literally and the walkthrough's "check out builder
to main and pull", and keeps all cross-repo git inside a script command (intent/001) rather than inside
the long-running `build` prompt.

Preconditions (A4), checked **before** the plan transition (so a failure leaves no state change), each
failing with a clear message:

- The builder working tree must be **clean** (`git_status_porcelain` returns empty). Otherwise raise
  `WorkflowError("builder working tree is dirty; commit or discard changes before implement")`.
- `feat/{plan_name}` must **not already exist** in the builder. Otherwise raise
  `WorkflowError("branch feat/{plan_name} already exists in builder")`.

Discarded alternative: `implement` would have only synced the builder's `main` and left the `build`
prompt/agent to create `feat/{plan_name}` itself. Rejected because it leaves the builder not "ready on a
feature branch" after `implement` and pushes git mechanics into a prompt step.

Consequence: the `build` prompt and the generated CLAUDE.md must **not** create `feat/{plan_name}` (a
second `git checkout -b` on the existing branch would fail). `013_example_walkthrough.md` is updated to
match, and any branch-creation instruction in `build` is removed as part of this ticket.

## Detailed implementation

### 1. `workflow/tmux_dispatch.py` — shared resolver + rotation

Extract the existing lookup into a helper and refactor `send_prompt_to_agent` to use it:

```python
def _resolve_session(config: WorkflowConfig) -> libtmux.Session:
    """Return the live workflow tmux session, or raise WorkflowError with a 'run start' hint."""
```

Add the rotation operation used by `go`, `implement`, and `check`:

```python
def rotate_control_pane(config: WorkflowConfig, target_window: str, *, dry_run: bool = False) -> None:
    """Move the recorded control pane next to the target window's pane."""
```

Behaviour of `rotate_control_pane`:

- Dry-run: print the intended `tmux join-pane …` command via the shared dry-run renderer (B1) and return
  without touching the live session.
- Resolve the session via `_resolve_session`.
- Read the recorded control-pane id from the session option `@workflow_control_pane`
  (`session.show_option(...)`); raise `WorkflowError("no control pane recorded; run workflow start")` if
  it is unset.
- Look up the pane by id (`server.panes.get(pane_id=…, default=None)`); raise `WorkflowError` if it no
  longer exists (stale session).
- `source_window = control_pane.window`. If `source_window.name == target_window`: return (already there
  — a no-op, important for `go <current>` and re-runs).
- `target = session.windows.get(window_name=target_window, default=None)`; raise `WorkflowError` if
  missing.
- Run `res = session.cmd("join-pane", "-h", "-b", "-s", control_pane_id, "-t", f":{target_window}")` and
  raise `WorkflowError(res.stderr)` if the command reported an error.
- Select the target window so the human's focus follows the control pane.
- Catch `OSError`/`libtmux.exc.LibTmuxException` around live calls and re-raise as `WorkflowError`, so
  callers only handle one exception type (consistent with `send_prompt_to_agent`).

Locating the pane by recorded id (D2) removes the POC's pane-count / index-0 / active-pane preconditions
entirely — rotation no longer cares how many panes a window has or which is focused.

### 2. `workflow/commands/workflow.py` — `start` control pane

`start` is converted to drive session/window creation through `libtmux` (B1) instead of raw `tmux`
subprocess calls. After the agent windows and the `builder-shell` window are created, split the **first
agent** window to create the docked control pane, then record its id (walkthrough: the planner is visible
in the split after `start`):

```python
control_pane = first_agent_window.split(direction=libtmux.pane.PaneDirection.Left, attach=True)
session.set_option("@workflow_control_pane", control_pane.pane_id)
session.select_window(first_agent_window.name)
```

- The split with no command launches the operator's default shell — this *is* the control pane.
- Its pane id is persisted in the `@workflow_control_pane` session option for rotation (D2).
- In dry-run, `start` prints the full sequence of intended tmux commands via the shared renderer and makes
  no libtmux calls (B1), keeping dry-run output consistent with rotation.

### 3. `workflow/commands/workflow.py` — `implement` branch setup + rotation

Per D5, check the builder preconditions **before** transitioning the plan (so a failure leaves no state
change), then replace the `# TODO: switch to the builder tmux window.` with the branch setup and rotation:

```python
# preconditions (A4) — before the plan transition
if git_status_porcelain(project_dir / RepoName.builder, dry_run=dry_run):
    raise WorkflowError("builder working tree is dirty; commit or discard changes before implement")
if git_branch_exists(project_dir / RepoName.builder, f"feat/{new_plan.name}", dry_run=dry_run):
    raise WorkflowError(f"branch feat/{new_plan.name} already exists in builder")

# … existing transition + push of master …

git_fetch(project_dir / RepoName.builder, dry_run=dry_run)
git_checkout(project_dir / RepoName.builder, "main", dry_run=dry_run)
git_pull(project_dir / RepoName.builder, dry_run=dry_run)
git_checkout(project_dir / RepoName.builder, f"feat/{new_plan.name}", create=True, dry_run=dry_run)
_rotate_best_effort(config, AgentName.builder, dry_run=dry_run)
```

- Requires importing `git_checkout` (exists with a `create` parameter) plus the two new helpers
  `git_status_porcelain` and `git_branch_exists` (section 6).
- The post-rotation pause uses the single delay knob `config.tmux.prompt_send_delay` (B5) rather than a
  literal `1.0`.

### 4. `workflow/commands/workflow.py` — `check` reviewer rotation

After `git_fetch(project_dir / RepoName.reviewer, ...)`:

```python
_rotate_best_effort(config, AgentName.reviewer, dry_run=dry_run)
```

with a `config.tmux.prompt_send_delay` pause (B5), mirroring `implement`.

### 5. `workflow/commands/workflow.py` — the `go` command and the best-effort wrapper

```python
@register_script("go")
@click.argument("target")
@click.pass_context
def go(ctx: click.Context, target: str) -> None:
    """Rotate the control pane to a window and print the target repo's git status."""
```

- **Session guard (D2)**: `go` must be run from inside the workflow tmux session. Verify this (e.g.
  `$TMUX` is set and the resolved session matches `config.tmux.session_name`) and raise a clear
  `WorkflowError` if not, so the operator is told to attach rather than silently moving a pane in a
  detached session.
- Valid targets are derived from config: `{agent.name for agent in config.agents} | {"manual"}`. Reject
  others with a `WorkflowError` listing the valid choices. (Deriving from config keeps `go` correct if
  the agent set is customised, rather than hardcoding a `click.Choice`.)
- Map target → window and repo: `"manual"` → window `"builder-shell"`, repo = the `manual_shell` agent's
  repo (D3); any agent name → window = target, repo = `RepoName(target)`.
- Call `rotate_control_pane(config, window, dry_run=dry_run)` (fatal on error, D4), then print the target
  repo's `git status` (see helper below) and `ctx.exit(0)`.
- **`go` is not audit-logged** (B3): it is pure navigation with no plan and no state change, so it writes
  no JSONL entry.
- Wrap in the same `except (WorkflowError, ...): print_workflow_error; ctx.exit(1)` block as the other
  script commands.

Best-effort wrapper (private, used by `implement`/`check`, D4):

```python
def _rotate_best_effort(config: WorkflowConfig, target_window: str, *, dry_run: bool) -> None:
    """Rotate the control pane, downgrading tmux failures to a loud warning (handoff already committed)."""
    try:
        rotate_control_pane(config, target_window, dry_run=dry_run)
    except WorkflowError as exc:
        error_console.print(f"[yellow]tmux rotation skipped:[/yellow] {exc}")
```

### 6. `workflow/git_helpers.py` — status + branch helpers

Add thin helpers so `go`/`implement` go through the same dry-run plumbing as everything else and are
mockable in tests:

```python
def git_status(repo_path: Path, *, dry_run: bool = False) -> None:
    """Print ``git status`` (short, with branch) for *repo_path* (used by `go`)."""

def git_status_porcelain(repo_path: Path, *, dry_run: bool = False) -> str:
    """Return ``git status --porcelain`` output (empty == clean working tree) for preconditions."""

def git_branch_exists(repo_path: Path, branch: str, *, dry_run: bool = False) -> bool:
    """Return whether *branch* exists locally (``git rev-parse --verify``)."""
```

- `git_status` prints via `run_command([... "status", "--short", "--branch"], dry_run=dry_run)`.
- `git_status_porcelain` / `git_branch_exists` capture output (they inform control flow), so in dry-run
  they print the command they would run and return a safe default (empty / `False`).

### Configuration

- `AgentConfig` gains `manual_shell: bool = False`, with a validator ensuring **at most one** agent sets
  it (B4). `docs/default_config.yaml` sets `manual_shell: true` on the builder agent.
- The post-rotation pause reuses the existing single delay knob `config.tmux.prompt_send_delay` (B5); no
  new delay field is added (1 s is likely too short and can be tuned later).
- `go` becomes a CLI subcommand automatically via the `@register_script` registry (like
  `start`/`status`/`log`).

## Testing

Follow the existing conventions: mock `libtmux.Server` the way `TestTmuxSource` does in
`tests/test_plan_resolver.py`; mock `run_command`/git helpers with `MagicMock`; invoke via
`CliRunner().invoke(main, [...])`. No real-tmux integration test is added at this point (B2) — all tmux
interaction is mocked.

Existing tests to update (behaviour changed):

- `tests/test_workflow_commands.py::test_start_runs_tmux_commands` and
  `test_start_reset_flag_threads_through` — `start` now drives libtmux instead of `run_command(["tmux",
  …])` and records `@workflow_control_pane`. Rework these to mock `libtmux.Server` and assert the
  session/window/split calls plus the `set_option` call (or assert the dry-run command sequence).
- `tests/test_workflow_commands.py::test_implement_*` — mock `git_status_porcelain`, `git_branch_exists`,
  `git_fetch`, `git_checkout`, `git_pull`, and `rotate_control_pane`; assert preconditions are checked
  before the transition and the fetch/checkout/branch calls happen in order.
- `tests/test_workflow_commands.py::test_check_runs_pre_commit_and_prints_review_reminder` — mock
  `rotate_control_pane` and assert it is invoked for the reviewer window.

New tests to add (no new test files — extend existing ones):

- A `go` group: target → window/repo mapping for an agent name and for `manual` (resolves the
  `manual_shell` agent's repo); `git status` printed for the resolved repo; invalid target exits non-zero
  with the valid choices listed; the session guard rejects running outside the workflow session; dry-run
  prints the `join-pane` line and touches no live session; **no audit-log entry is written** for `go`.
- `tests/test_cli.py::test_help_lists_dynamic_prompt_and_script_commands` — assert `"go"` is listed.
- A `rotate_control_pane` section mocking `libtmux.Server`: success path locates the pane by recorded id
  and asserts the `join-pane -h -b -s <id> -t :{tgt}` arguments and the follow-up select; no-op when the
  control pane is already in the target window; `WorkflowError` when the option is unset / the pane id is
  stale / the target window is missing / the server is not alive.
- A `_rotate_best_effort` test asserting a `WorkflowError` becomes a warning and the caller still exits 0.
- An `AgentConfig` validation test: two agents with `manual_shell=true` is rejected; zero or one is
  accepted (B4).
- `implement` precondition tests: a dirty builder tree and a pre-existing `feat/{plan}` each raise a clear
  error and leave the plan state unchanged (A4).

All of `black`, `ruff`, `pytest -m 'not slow'`, and `mypy` must pass (pre-commit hook).

## Risks

- **libtmux fragility** — intent/001 flags pane-rotation as adding "fragility (pane IDs change on session
  restart…)". Mitigation: the control pane is located by an id recorded in a session option at `start`
  (D2); if the session is restarted the option/pane is gone and rotation fails loudly with a "run workflow
  start" `WorkflowError` rather than corrupting the layout. Window *names* (stable, set by `start`) are
  used for targets.
- **Plan transitions before the builder branch is ready** — `implement` advances plan state and pushes
  `master` before the branch setup. Preconditions (A4) are checked *first* so the common failures (dirty
  tree, existing branch) abort before any state change; a failure later (e.g. fetch error) surfaces loudly
  and the operator re-runs the branch setup.
- **Double feature-branch creation** (D5) — mitigated by the branch-absent precondition (A4) and by
  removing any branch-creation step from the `build` prompt and the generated CLAUDE.md.
- **Dry-run cannot validate live state** — rotation only prints the intended `join-pane` in dry-run, so
  dry-run will not catch a missing control pane. Acceptable: dry-run is for previewing commands.
- **tmux version differences** in `join-pane` flag handling — the `-h -b` form is the one verified in the
  POC (`tmux_pane_rotation_with_user_option.py`); no broader compatibility shim is attempted.

## Dependencies

- Builds on the existing `tmux_dispatch` and `plan_resolver` tmux handling; no new third-party packages
  (`libtmux` is already a dependency).
- Independent of the review-path resolution work (gap 6).
- Recommended implementation order: (0) manually confirm `tmux_pane_rotation_with_user_option.py`;
  (1) `tmux_dispatch` resolver + `rotate_control_pane`; (2) `start` control pane + recorded id; (3) `go`
  (incl. session guard); (4) wire `implement`/`check`. Steps 3–4 depend on 1; `go` is the easiest way to
  exercise rotation end-to-end before touching the handoff commands.
