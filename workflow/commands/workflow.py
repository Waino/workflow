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

from typing import Literal

import click

from workflow.doublecheck import CHECK_NAMES, CheckResult, infer_target_repo
from workflow.doublecheck import doublecheck as run_doublecheck
from workflow.exceptions import AmbiguousPlanError, PlanCollisionError, PlanNotFoundError, WorkflowError
from workflow.models import PlanState
from workflow.plan_resolver import resolve_plan
from workflow.registry import register_script
from workflow.shell import console


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
@click.pass_context
@click.option(
    "--target-repo",
    type=click.Choice(["planner", "builder", "reviewer", "master"]),
    default=None,
    help="Override the target repo (default: inferred from plan state).",
)
@click.argument("plan_path")
def doublecheck(
    ctx: click.Context,
    plan_path: str,
    target_repo: Literal["planner", "builder", "reviewer", "master"] | None,
) -> None:
    """Run pre-merge verification checks on the plan's branch."""
    config = ctx.obj["config"]
    dry_run: bool = ctx.obj["dry_run"]

    if config.project_dir is None:
        console.print("[red]Error: not inside a workflow project directory.[/red]")
        ctx.exit(1)
        return

    try:
        plan = resolve_plan(plan_path, config.project_dir)
    except (PlanNotFoundError, AmbiguousPlanError, PlanCollisionError) as exc:
        console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return

    target = infer_target_repo(plan, config, target_repo)
    console.print(f"Target repo: [bold]{target}[/bold]")

    if dry_run:
        for name in CHECK_NAMES:
            console.print(f"[dim][-] {name}: skipped in dry-run[/dim]")
        return

    try:
        results = run_doublecheck(plan, config.project_dir, config, target_repo_override=target_repo)
    except WorkflowError as exc:
        console.print(f"[red]{exc}[/red]")
        ctx.exit(1)
        return

    _print_results(results)

    has_failures = any(r.status == "fail" for r in results)
    if not has_failures:
        _print_next_step(plan)
    ctx.exit(1 if has_failures else 0)


def _print_results(results: list[CheckResult]) -> None:
    for result in results:
        if result.status == "pass":
            console.print(f"[green][✓][/green] {result.check_name}: {result.message}")
        elif result.status == "warn":
            console.print(f"[yellow][!][/yellow] {result.check_name}: {result.message}")
            for detail in result.details:
                console.print(f"    {detail}")
        else:
            console.print(f"[red][✗][/red] {result.check_name}: {result.message}")
            for detail in result.details:
                console.print(f"    {detail}")


def _print_next_step(plan: object) -> None:
    from workflow.models import Plan  # noqa: PLC0415

    if not isinstance(plan, Plan):
        return
    name = plan.name
    if plan.state == PlanState.PLANNING:
        console.print(f"\nNext step: [bold]workflow todo {name}[/bold] or [bold]workflow reject {name}[/bold]")
    elif plan.state == PlanState.CHECKING:
        console.print(f"\nNext step: [bold]workflow accept {name}[/bold] or [bold]workflow reject {name}[/bold]")


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
