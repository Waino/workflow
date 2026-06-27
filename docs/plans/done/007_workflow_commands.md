# 007: Workflow Commands

Input: `intent/001_idea`

## Goal

Implement the script commands that perform workflow state transitions and git operations:
`init`, `start`, `new`, `todo`, `implement`, `push`, `accept`, `reject`, and `doublecheck` (as CLI command).

## Scope

- All script-type commands that involve git operations or state transitions
- Docker/tmux commands for `init` and `start`
- Unit tests for command orchestration and dry-run behavior

## Command signatures

- Use the same Click-based approach as `doublecheck` for workflow script commands.
- `todo`, `implement`, `push`, `accept`, and `reject` take a `plan_path` argument.
- Add `--target-repo` where it is safe and useful.
  At minimum this applies to `push` and `reject`.
  Prefer consistency across plan-based script commands when it does not create ambiguity.

## Audit logging and reminders

- Script commands write audit log entries with actor `human`.
- Reminders are looked up dynamically from the configured workflow graph.
- Reminder lookup rules:
    1. If the current command is not last in its state, use the next command in that state.
    2. Otherwise, if the current command transitions to another state, use the first command in that state.
    3. If there is no transition, or the target state has no commands, print no reminder.
- Expected reminders:
    - `todo` -> `implement`
    - `implement` -> `ask`
    - `build` -> `push`
    - `push` -> `review`
    - `accept` -> none
    - `reject` -> none

## workflow init

- Create a bare clone of the canon repo.
- Create one clone per agent (from config: default planner, builder, reviewer).
- Directory structure:
  `{project_dir}/bare/`, `{project_dir}/planner/`, `{project_dir}/builder/`, `{project_dir}/reviewer/`
- Also create the `.workflow_root` marker in the project directory.
- Implement now.
- Cover with unit tests that verify the intended shell commands are issued in the right order.

```python
@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize agent repositories."""
    # git clone --bare canon/ bare/
    # git clone bare/ planner/  (for each agent)
```

## workflow start

- Open tmux windows for each agent, starting sandbox containers.
- Also open a tmux window with a docker exec bash in the builder sandbox.
  See intent/011_tmux_pane_rotation.md for a proof-of-concept.
- Model is passed from config.
- Pane rotation is not implemented yet, but window naming/layout should stay compatible with future rotation work.
- Implement now.
- Cover with unit tests that verify the intended tmux/docker commands are issued, without asserting that the commands themselves are correct.

```python
@cli.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start tmux sessions for all agents."""
    # tmux new-session / new-window for each agent
    # docker run/start for each agent's sandbox (in the correct tmux pane)
```

Note: docker and tmux commands will be written but cannot be tested in the sandbox.
Dry-run mode is essential for development.

## workflow new

- Find the first free top-level sequence number.
- Check all plan states and the audit log for reserved numbers on branches.
- Open `docs/plans/intent/{num}_{slug}.md` in `${EDITOR}`.
- Start with a bare-bones placeholder template.
- Implement now.

## workflow todo

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def todo(ctx: click.Context, plan_path: str) -> None:
    """Approve a plan for implementation."""
    # 1. Resolve plan (must be in state: planning, repo: planner)
    # 2. transfer_branch_to_main(planner → bare → canon, merge to main)
    # 3. transition_plan(plan, PlanState.TODO)
    # 4. git_commit + git_push (canon → bare)
    # 5. Print reminder: "next step is: workflow implement {plan_name}".
    #    Note that the reminder should be dynamic using the steps defined in the config.
```

## workflow implement

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def implement(ctx: click.Context, plan_path: str) -> None:
    """Schedule a plan for implementation."""
    # 1. Resolve plan (must be in state: planning or todo, repo: canon)
    # 2. If planning: do the same push-pull-merge as todo first
    # 3. transition_plan(plan, PlanState.IMPLEMENTING)
    # 4. git_commit + git_push (canon → bare)
    # 5. Print reminder, pause briefly
    # 6. TODO: switch to the builder's tmux window.
    #    Do not implement the window switch in this plan.
```

## workflow push

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def push(ctx: click.Context, plan_path: str, target_repo_override: str) -> None:
    """Push builder's branch after verification."""
    # 1. Resolve plan (must be in state: checking, repo: builder)
    # 2. Rerun pre-commit hook inside sandbox (docker exec)
    # 3. Run doublecheck(plan, ...)
    # 4. git_push(builder → bare)
    # 5. git_fetch(reviewer)
```

- Implement the pre-commit rerun now.
- Human will test correctness manually; unit tests only need to verify that the expected command execution is attempted.

## workflow accept

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def accept(ctx: click.Context, plan_path: str) -> None:
    """Accept implementation and merge to main."""
    # 1. Resolve plan (must be in state: checking, repo: reviewer)
    # 2. git_push(reviewer → bare)
    # 3. git_pull(canon)
    # 4. transition_plan(plan, PlanState.DONE)
    # 5. git_merge + git_commit (in canon)
    # 6. git_push(canon → bare)
```

## workflow reject

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def reject(ctx: click.Context, plan_path: str) -> None:
    """Reject a plan."""
    # 1. Resolve plan (default repo: canon; allow override via --target-repo)
    # 2. transition_plan(plan, PlanState.REJECTED)
    # 3. git_commit
```

## workflow doublecheck (CLI wrapper)

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def doublecheck_cmd(ctx: click.Context, plan_path: str) -> None:
    """Run verification checks on a plan."""
    # 1. Resolve plan
    # 2. Call doublecheck(plan, ...) from plan 005
    # 3. Print results with rich formatting
    # 4. Print reminder based on state
```

## Prompt commands

Prompt commands (`ask`, `critique`, `plan`, `subdivide`, `build`, `pr`, `fix`,
`full-review`, `followthrough`) are dispatched generically by the framework in plan 004.2.
They do not require individual implementations here.

The `implement` script command will in the future rotate panes to the builder window,
but that step is explicitly deferred for now.

## File organization

- `workflow/commands/workflow.py` — all script workflow commands (or split into multiple files if large)
- Each script command is registered with `@cli.command()` and imported in `cli.py`
- Prompt commands are registered dynamically by `register_prompt_commands` in `workflow/tmux_dispatch.py`

## Dependencies

- Plan 002 (models): all models
- Plan 003 (path resolution): resolve_plan
- Plan 004.1 (CLI and registry): cli group, run_command
- Plan 004.2 (prompt dispatch): send_prompt_to_agent, register_prompt_commands
- Plan 004.3 (audit logging): write_audit_log
- Plan 005 (state/git): transition_plan, git helpers, transfer_branch_to_main, doublecheck

## Note on git_commit and no_verify

All `git_commit` calls that follow a `transition_plan` (i.e. in `todo`, `implement`, `accept`,
and `reject`) must pass `no_verify=True`. This skips the pre-commit hook, ensuring that state
transitions succeed even when tests are failing in the working tree.

`push` should not use `no_verify=True`; it explicitly reruns the hook inside the sandbox before push.

## Testing strategy

- Write unit tests for the new command implementations.
- For `init`, `start`, and the `push` pre-commit execution, test that the tool attempts to run the planned commands.
- Do not try to prove docker/tmux command correctness in automated tests.
- The human operator will verify exact docker/tmux behavior manually.

## Risks

- The git sequences in `todo`, `implement`, and `accept` are multi-step and can fail partway.
  Each step should have clear error handling. Consider whether partial completion should be
  rolled back or left for human intervention (lean toward the latter, with clear error messages).
- Docker/tmux commands cannot be tested in the sandbox. Rely on dry-run mode and manual testing.

