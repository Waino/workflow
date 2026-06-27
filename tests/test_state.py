"""Tests for workflow.state."""

from pathlib import Path

import pytest

from workflow import state
from workflow.models import Plan, PlanState


def _plan(*, repo: str = "planner", state_value: PlanState = PlanState.PLANNING) -> Plan:
    return Plan(
        sequence_number=[5],
        slug="foo",
        state=state_value,
        path=Path(f"{repo}/docs/plans/{state_value.value}/005_foo.md"),
    )


def test_transition_plan_uses_current_repo_when_not_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
        captured.update(repo_path=repo_path, source=source, target=target, dry_run=dry_run)

    monkeypatch.setattr(state, "git_mv", fake_git_mv)

    new_plan = state.transition_plan(_plan(), PlanState.TODO, Path("/project"), dry_run=True)

    assert captured == {
        "repo_path": Path("/project/planner"),
        "source": Path("/project/planner/docs/plans/planning/005_foo.md"),
        "target": Path("/project/planner/docs/plans/todo/005_foo.md"),
        "dry_run": True,
    }
    assert new_plan.path == Path("planner/docs/plans/todo/005_foo.md")


def test_transition_plan_rewrites_source_repo_when_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
        captured.update(repo_path=repo_path, source=source, target=target, dry_run=dry_run)

    monkeypatch.setattr(state, "git_mv", fake_git_mv)

    new_plan = state.transition_plan(_plan(), PlanState.TODO, Path("/project"), repo_override="canon", dry_run=True)

    assert captured == {
        "repo_path": Path("/project/canon"),
        "source": Path("/project/canon/docs/plans/planning/005_foo.md"),
        "target": Path("/project/canon/docs/plans/todo/005_foo.md"),
        "dry_run": True,
    }
    assert new_plan.path == Path("canon/docs/plans/todo/005_foo.md")
