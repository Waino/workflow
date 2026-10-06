# 029: Freeform Agent

## Goal

There are some tasks that don't fit well with the current fixed 3-agent workflow,
e.g. codebase exploration, debugging, and trivial edits not worthy of a plan document.
Add a fourth agent "freeform" for these tasks.

## Why not use one of the existing agents?

The workaround is picking one of the existing agents to use in an improvised manner,
outside the prompt commands in the config or even without referencing a plan document.
Because each agent has a system prompt customized to its particular role,
the workaround requires considering which role is the least bad fit for the improvised task.
Creating a fourth agent allows giving it a more generic system prompt.

## Preliminary work

Some attempts have been made to prevent the agent list from becoming ossified in code.
For example:
- the config carries a list of agents defining the agent name and which one carries the manual shell, and
- some instances of hard-coded agent names have been replaced with lookup from the config during reviews.
However, the only tested and actively developed configuration is the planner-builder-reviewer one.

The list cannot be arbitrary: because transitions are implemented as script commands,
none of the current three agents can be removed in config (they might perhaps be renamed).
However, it should be possible to configure new agents without requiring extensive code change.

Planner should begin by searching for locations where agent identities and quantities are hard-coded.
If these can with reasonable effort and complexity be made flexible, we should do so before adding "freeform".

## Specifics

Specifics for the freeform agent in the default config.

### States and transitions

Freeform does not own any state.
There are no transitions handing off a plan to or from freeform.

### Prompts

Initially, there will not be any prompts for freeform shipping with the package.
Projects may add project-specific canned prompts for freeform.
These can either take a plan (`has_plan` in `tmux_dispatch.py`) or not.
Instead of deriving `has_plan` from the state `state != PlanState.REVIEW`, it should be a field on the command.

It must be possible to send a prompt referencing a plan to freeform by giving a path that explicitly specifies both agent and state.

### Worktree, sandbox, and tmux window

Freeform should have its own worktree with virtual environment.
A sandbox for freeform should be created and started along with the other agents.
Freeform should have a pane in tmux, and be addressable by `go`.

### Opt-out, backwards compatibility, and migration

When configuring/initializing a project, it should be possible to opt-out of creating freeform.
If the config entries and/or worktree do not exist (either due to opt-out or legacy project),
workflow should still function: only functionality related to freeform should be disabled.
There should be a clear migration path for introducing freeform to a legacy project.

### Documentation and decision pass

This is a major change for product philosophy, which has included "three is enough" from the start.
There are multiple locations in documentation and indexed decisions referencing exactly three agents.
These will need an update pass.
