"""Tests for workflow.state."""

from pathlib import Path

import pytest

from workflow import state
from workflow.exceptions import WorkflowError
from workflow.models import Plan, PlanState


def _plan(*, repo: str = "planner", state_value: PlanState = PlanState.PLANNING) -> Plan:
    return Plan(
        sequence_number=[5],
        slug="foo",
        state=state_value,
        path=Path(f"{repo}/docs/plans/{state_value.value}/005_foo.md"),
    )


def _write_plan_file(project_dir: Path, repo: str, state_value: PlanState, name: str = "005_foo.md") -> None:
    plan_dir = project_dir / repo / "docs" / "plans" / state_value.value
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / name).write_text("# 005: Foo\n", encoding="utf-8")


def test_transition_plan_uses_current_repo_when_not_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_plan_file(tmp_path, "planner", PlanState.PLANNING)
    captured: dict[str, object] = {}

    def fake_git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
        captured.update(repo_path=repo_path, source=source, target=target, dry_run=dry_run)

    monkeypatch.setattr(state, "git_mv", fake_git_mv)

    new_plan = state.transition_plan(_plan(), PlanState.TODO, tmp_path, dry_run=True)

    assert captured == {
        "repo_path": tmp_path / "planner",
        "source": tmp_path / "planner/docs/plans/planning/005_foo.md",
        "target": tmp_path / "planner/docs/plans/todo/005_foo.md",
        "dry_run": True,
    }
    assert new_plan.path == Path("planner/docs/plans/todo/005_foo.md")


def test_transition_plan_rewrites_source_repo_when_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_plan_file(tmp_path, "canon", PlanState.PLANNING)
    captured: dict[str, object] = {}

    def fake_git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
        captured.update(repo_path=repo_path, source=source, target=target, dry_run=dry_run)

    monkeypatch.setattr(state, "git_mv", fake_git_mv)

    new_plan = state.transition_plan(_plan(), PlanState.TODO, tmp_path, repo_override="canon", dry_run=True)

    assert captured == {
        "repo_path": tmp_path / "canon",
        "source": tmp_path / "canon/docs/plans/planning/005_foo.md",
        "target": tmp_path / "canon/docs/plans/todo/005_foo.md",
        "dry_run": True,
    }
    assert new_plan.path == Path("canon/docs/plans/todo/005_foo.md")


def test_transition_plan_locates_source_by_name_not_by_plan_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source is found by scanning the target repo, not trusted from plan.path's state dir.

    Regression for the `reject`-in-`checking` defect: the resolved plan reports the
    state (and thus path) it was found in on the feature branch (`checking/`), while
    canon's `main` still has the file under `implementing/`.
    """
    _write_plan_file(tmp_path, "canon", PlanState.IMPLEMENTING)
    captured: dict[str, object] = {}

    def fake_git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
        captured.update(repo_path=repo_path, source=source, target=target, dry_run=dry_run)

    monkeypatch.setattr(state, "git_mv", fake_git_mv)

    # plan.path claims 'checking' (as resolved on the feature branch), but canon's main
    # only has the file under 'implementing'.
    plan = _plan(repo="reviewer", state_value=PlanState.CHECKING)
    new_plan = state.transition_plan(plan, PlanState.REJECTED, tmp_path, repo_override="canon", dry_run=True)

    assert captured["source"] == tmp_path / "canon/docs/plans/implementing/005_foo.md"
    assert captured["target"] == tmp_path / "canon/docs/plans/rejected/005_foo.md"
    assert new_plan.path == Path("canon/docs/plans/rejected/005_foo.md")


def test_transition_plan_raises_clear_error_when_source_missing(tmp_path: Path) -> None:
    plan = _plan(repo="canon", state_value=PlanState.IMPLEMENTING)

    with pytest.raises(WorkflowError, match="not found in canon"):
        state.transition_plan(plan, PlanState.CHECKING, tmp_path, repo_override="canon", dry_run=False)


def test_transition_plan_dry_run_guesses_source_when_not_yet_landed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry-run must preview optimistically when an earlier (also simulated) step hasn't landed the file."""
    captured: dict[str, object] = {}

    def fake_git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
        captured.update(repo_path=repo_path, source=source, target=target, dry_run=dry_run)

    monkeypatch.setattr(state, "git_mv", fake_git_mv)
    plan = _plan(repo="canon", state_value=PlanState.IMPLEMENTING)

    new_plan = state.transition_plan(plan, PlanState.CHECKING, tmp_path, repo_override="canon", dry_run=True)

    assert captured["source"] == tmp_path / "canon/docs/plans/implementing/005_foo.md"
    assert captured["target"] == tmp_path / "canon/docs/plans/checking/005_foo.md"
    assert new_plan.path == Path("canon/docs/plans/checking/005_foo.md")


def test_transition_plan_reframes_an_unrelated_sequence_collision(tmp_path: Path) -> None:
    """The lookup scans the whole repo, so a collision elsewhere must not report the wrong plan."""
    _write_plan_file(tmp_path, "canon", PlanState.IMPLEMENTING)
    _write_plan_file(tmp_path, "canon", PlanState.TODO, name="007_bar.md")
    _write_plan_file(tmp_path, "canon", PlanState.PLANNING, name="007_baz.md")
    plan = _plan(repo="canon", state_value=PlanState.IMPLEMENTING)

    with pytest.raises(WorkflowError, match=r"cannot transition 005_foo\.md while sequence number '007' collides"):
        state.transition_plan(plan, PlanState.CHECKING, tmp_path, repo_override="canon", dry_run=True)


def test_transition_plan_raises_on_duplicate_source(tmp_path: Path) -> None:
    _write_plan_file(tmp_path, "canon", PlanState.IMPLEMENTING)
    _write_plan_file(tmp_path, "canon", PlanState.CHECKING)
    plan = _plan(repo="canon", state_value=PlanState.CHECKING)

    with pytest.raises(WorkflowError, match="multiple state dirs"):
        state.transition_plan(plan, PlanState.DONE, tmp_path, repo_override="canon", dry_run=True)
