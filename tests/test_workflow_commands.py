"""Tests for workflow script command orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile
from typing import TYPE_CHECKING, TypeVar

from click.testing import CliRunner

import workflow.commands.workflow as wc
from workflow import main
from workflow.audit import write_audit_log
from workflow.config import WORKFLOW_ROOT_MARKER
from workflow.doublecheck import CheckResult
from workflow.models import AuditLogEntry, Plan, PlanState

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


def _make_project(tmp_path: Path, *, marker: bool = True) -> Path:
    if marker:
        (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    for repo in ("master", "planner", "builder", "reviewer"):
        (tmp_path / repo).mkdir(exist_ok=True)
    return tmp_path / "config.yaml"


def _make_plan(project_dir: Path, repo: str, state: str, filename: str) -> None:
    path = project_dir / repo / "docs" / "plans" / state
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text("# plan\n", encoding="utf-8")


def _plan(repo: str, state: PlanState, name: str = "005_foo") -> Plan:
    return Plan(sequence_number=[5], slug="foo", state=state, path=Path(f"{repo}/docs/plans/{state.value}/{name}.md"))


T = TypeVar("T")


def _noop(*_args: object, **_kwargs: object) -> None:
    pass


def _return(value: T) -> Callable[..., T]:
    def _inner(*_args: object, **_kwargs: object) -> T:
        return value

    return _inner


def _record_command(calls: list[list[str]]) -> Callable[..., None]:
    def _inner(args: list[str], **_kwargs: object) -> None:
        calls.append(args)

    return _inner


def _record_path(paths: list[Path]) -> Callable[[Path], None]:
    def _inner(path: Path) -> None:
        paths.append(path)

    return _inner


def _record_args(calls: list[tuple[str, tuple[object, ...]]], name: str) -> Callable[..., None]:
    def _inner(*args: object, **_kwargs: object) -> None:
        calls.append((name, args))

    return _inner


def _record_repo(repos: list[Path]) -> Callable[..., None]:
    def _inner(repo: Path, *_args: object, **_kwargs: object) -> None:
        repos.append(repo)

    return _inner


def test_init_runs_clone_commands_and_creates_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path, marker=False)
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(wc, "run_command", _record_command(calls))

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])

    assert result.exit_code == 0
    assert calls == [
        ["git", "clone", "--bare", "master", "bare"],
        ["git", "clone", "bare", "planner"],
        ["git", "clone", "bare", "builder"],
        ["git", "clone", "bare", "reviewer"],
    ]
    assert (tmp_path / WORKFLOW_ROOT_MARKER).exists()


def test_start_runs_tmux_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(wc, "run_command", _record_command(calls))

    result = CliRunner().invoke(main, ["--config", str(config_path), "start"])

    assert result.exit_code == 0
    assert calls[0][:6] == ["tmux", "new-session", "-d", "-s", "workflow", "-n"]
    assert calls[0][6] == "planner"
    assert [call[5] for call in calls[1:3]] == ["builder", "reviewer"]
    assert calls[3][5] == "builder-shell"


def test_new_uses_existing_plans_and_audit_log_for_numbering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "master", "intent", "001_idea.md")
    write_audit_log(
        tmp_path,
        AuditLogEntry(
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            actor="human",
            command="new",
            output_plans=["002_bar"],
        ),
    )
    opened: list[Path] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "_open_in_editor", _record_path(opened))

    result = CliRunner().invoke(main, ["--config", str(config_path), "new", "flux_capacitor"])

    assert result.exit_code == 0
    expected = tmp_path / "master" / "docs" / "plans" / "intent" / "003_flux_capacitor.md"
    assert expected.exists()
    assert opened == [expected]


def test_todo_transitions_and_prints_implement_reminder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(wc, "transfer_branch_to_main", _record_args(calls, "transfer"))
    monkeypatch.setattr(wc, "transition_plan", _return(_plan("master", PlanState.TODO)))
    monkeypatch.setattr(wc, "git_commit", _record_args(calls, "commit"))
    monkeypatch.setattr(wc, "git_push", _record_args(calls, "push"))

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "005"])

    assert result.exit_code == 0
    assert "workflow implement 005_foo" in result.output
    assert [name for name, _args in calls] == ["transfer", "commit", "push"]


def test_implement_prints_explicit_ask_reminder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "master", "todo", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "transition_plan", _return(_plan("master", PlanState.IMPLEMENTING)))
    monkeypatch.setattr(wc, "git_commit", _noop)
    monkeypatch.setattr(wc, "git_push", _noop)
    monkeypatch.setattr(wc.time, "sleep", _noop)

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005"])

    assert result.exit_code == 0
    assert "workflow ask 005_foo" in result.output


def test_push_runs_pre_commit_and_prints_review_reminder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "builder", "checking", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(wc, "run_command", _record_command(calls))
    monkeypatch.setattr(wc, "run_doublecheck", _return([CheckResult(check_name="x", status="pass", message="ok")]))
    monkeypatch.setattr(wc, "git_push", _noop)
    monkeypatch.setattr(wc, "git_fetch", _noop)

    result = CliRunner().invoke(main, ["--config", str(config_path), "push", "005"])

    assert result.exit_code == 0
    assert calls[0][:2] == ["docker", "exec"]
    assert calls[0][2].endswith("-builder")
    assert "Next step: review" in result.output


def test_accept_and_reject_run_in_expected_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "reviewer", "checking", "005_foo.md")
    _make_plan(tmp_path, "reviewer", "implementing", "006_bar.md")
    monkeypatch.chdir(tmp_path)
    repos: list[Path] = []
    monkeypatch.setattr(wc, "git_current_branch", _return("feat/005_foo"))
    monkeypatch.setattr(
        wc,
        "transition_plan",
        lambda _plan_arg, target_state, *_args, **_kwargs: _plan(
            "master" if target_state == PlanState.DONE else "reviewer",
            target_state,
        ),
    )
    monkeypatch.setattr(wc, "git_push", _record_repo(repos))
    monkeypatch.setattr(wc, "git_pull", _record_repo(repos))
    monkeypatch.setattr(wc, "git_merge", _noop)
    monkeypatch.setattr(wc, "git_commit", _record_repo(repos))

    accept_result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005"])
    reject_result = CliRunner().invoke(
        main,
        ["--config", str(config_path), "reject", "--target-repo", "reviewer", "006"],
    )

    assert accept_result.exit_code == 0
    assert reject_result.exit_code == 0
    assert tmp_path / "reviewer" in repos
    assert tmp_path / "master" in repos
