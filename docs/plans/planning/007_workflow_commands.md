# 007: Workflow Commands

Input: `intent/001_idea`

## Goal

Implement the script commands that perform workflow state transitions and git operations:
`init`, `start`, `todo`, `implement`, `push`, `accept`, `reject`, and `doublecheck` (as CLI command).

## Scope

- All script-type commands that involve git operations or state transitions
- Docker/tmux commands for `init` and `start`

## workflow init

- Create a bare clone of the master repo.
- Create one clone per agent (from config: default planner, builder, reviewer).
- Directory structure:
  `{project_dir}/bare/`, `{project_dir}/planner/`, `{project_dir}/builder/`, `{project_dir}/reviewer/`

```python
@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize agent repositories."""
    # git clone --bare master/ bare/
    # git clone bare/ planner/  (for each agent)
```

## workflow start

- Open tmux windows for each agent, starting sandbox containers.
- Also open a tmux window with a docker exec bash in the builder sandbox.
- Model is passed from config.

```python
@cli.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start tmux sessions for all agents."""
    # tmux new-session / new-window for each agent
    # docker run/start for each agent's sandbox
```

Note: docker and tmux commands will be written but cannot be tested in the sandbox.
Dry-run mode is essential for development.

## workflow todo

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def todo(ctx: click.Context, plan_path: str) -> None:
    """Approve a plan for implementation."""
    # 1. Resolve plan (must be in state: planning, repo: planner)
    # 2. transfer_branch_to_main(planner → bare → master, merge to main)
    # 3. transition_plan(plan, PlanState.TODO)
    # 4. git_commit + git_push (master → bare)
    # 5. Print reminder: "next step is: workflow implement {plan_name}"
```

## workflow implement

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def implement(ctx: click.Context, plan_path: str) -> None:
    """Schedule a plan for implementation."""
    # 1. Resolve plan (must be in state: planning or todo, repo: master)
    # 2. If planning: do the same push-pull-merge as todo first
    # 3. transition_plan(plan, PlanState.IMPLEMENTING)
    # 4. git_commit + git_push (master → bare)
    # 5. Print reminder, pause briefly
    # 6. Switch to the builder's tmux window via send_prompt_to_agent (plan 004.2)
    #    No prompt is sent; only the window switch occurs so the human can see the builder.
```

## workflow push

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def push(ctx: click.Context, plan_path: str) -> None:
    """Push builder's branch after verification."""
    # 1. Resolve plan (must be in state: checking, repo: builder)
    # 2. Rerun pre-commit hook inside sandbox (docker exec)
    # 3. Run doublecheck(plan, ...)
    # 4. git_push(builder → bare)
    # 5. git_fetch(reviewer)
```

## workflow accept

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def accept(ctx: click.Context, plan_path: str) -> None:
    """Accept implementation and merge to main."""
    # 1. Resolve plan (must be in state: checking, repo: reviewer)
    # 2. git_push(reviewer → bare)
    # 3. git_pull(master)
    # 4. transition_plan(plan, PlanState.DONE)
    # 5. git_merge + git_commit (in master)
    # 6. git_push(master → bare)
```

## workflow reject

```python
@cli.command()
@click.argument("plan_path")
@click.pass_context
def reject(ctx: click.Context, plan_path: str) -> None:
    """Reject a plan."""
    # 1. Resolve plan (any state)
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

The `implement` script command switches to the builder window (step 6 above)
so the human can see the builder before running `workflow ask {plan}` to send the first prompt.

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

## Risks

- The git sequences in `todo`, `implement`, and `accept` are multi-step and can fail partway.
  Each step should have clear error handling. Consider whether partial completion should be
  rolled back or left for human intervention (lean toward the latter, with clear error messages).
- Docker/tmux commands cannot be tested in the sandbox. Rely on dry-run mode and manual testing.

