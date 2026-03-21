"""Workflow script commands: plan lifecycle and git operations.

These are placeholder implementations — full logic will be added in later plans.
Each function is registered in the script registry on module import.

Commands by workflow stage:
- init / start       — project setup
- new                — create a new intent file
- todo               — approve a plan (planning → todo)
- implement          — schedule a plan for implementation (todo → implementing)
- doublecheck        — run pre-merge checks (used in planning and checking)
- push               — push builder branch to bare repo
- accept             — merge reviewed branch to main (checking → done)
- reject             — reject a plan (any state → rejected)
"""

from __future__ import annotations

from workflow.registry import register_script


@register_script("init")
def init() -> int:
    """Initialise the project directory: create bare and agent git clones."""
    return 0


@register_script("start")
def start() -> int:
    """Start agent tmux windows for all configured agents."""
    return 0


@register_script("new")
def new() -> int:
    """Create a new intent file and open it in $EDITOR."""
    return 0


@register_script("todo")
def todo() -> int:
    """Approve a plan: push from planner, pull to master, transition to todo."""
    return 0


@register_script("implement")
def implement() -> int:
    """Schedule a plan for implementation: transition to implementing and push."""
    return 0


@register_script("doublecheck")
def doublecheck() -> int:
    """Run pre-merge checks on the current branch."""
    return 0


@register_script("push")
def push() -> int:
    """Push the builder branch to the bare repo and fetch in reviewer."""
    return 0


@register_script("accept")
def accept() -> int:
    """Accept a reviewed branch: push from reviewer, merge to main, transition to done."""
    return 0


@register_script("reject")
def reject() -> int:
    """Transition a plan to the rejected state."""
    return 0
