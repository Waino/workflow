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
    # 6. tmux attach to builder
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

## File organization

- `workflow/commands/workflow.py` — all workflow commands (or split into multiple files if large)
- Each command is registered with `@cli.command()` and imported in `cli.py`

## Dependencies

- Plan 002 (models): all models
- Plan 003 (path resolution): resolve_plan
- Plan 004 (CLI framework): cli group, run_command, audit logging
- Plan 005 (state/git): transition_plan, git helpers, transfer_branch_to_main, doublecheck

## Risks

- The git sequences in `todo`, `implement`, and `accept` are multi-step and can fail partway.
  Each step should have clear error handling. Consider whether partial completion should be
  rolled back or left for human intervention (lean toward the latter, with clear error messages).
- Docker/tmux commands cannot be tested in the sandbox. Rely on dry-run mode and manual testing.

