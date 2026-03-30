"""CLI integration tests for dynamic command registration and prompt dispatch."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile
from typing import TYPE_CHECKING

from click.testing import CliRunner

import workflow.commands.workflow as workflow_commands
from workflow import main, registry, tmux_dispatch
from workflow.config import WORKFLOW_ROOT_MARKER
from workflow.doublecheck import CheckResult
from workflow.exceptions import WorkflowError

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from workflow.models import AuditLogEntry


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    config_path = tmp_path / "config.yaml"
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", config_path)
    for repo in ("master", "planner", "builder", "reviewer"):
        (tmp_path / repo).mkdir()
    return config_path


def _make_plan(project_dir: Path, repo: str, state: str, filename: str) -> None:
    plan_dir = project_dir / repo / "docs" / "plans" / state
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / filename).write_text(f"# {filename}\n", encoding="utf-8")


def _noop(*_args: object, **_kwargs: object) -> None:
    pass


def _return(value: str) -> Callable[..., str]:
    def _inner(*_args: object, **_kwargs: object) -> str:
        return value

    return _inner


def test_help_lists_dynamic_prompt_and_script_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "--help"])

    assert result.exit_code == 0
    assert "ask" in result.output
    assert "todo" in result.output
    assert "full-review" in result.output
    assert "init" in result.output
    assert "start" in result.output
    assert "new" in result.output
    assert "reject" in result.output


def test_script_commands_are_invokable_once_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    called: list[str] = []

    def fake_todo() -> int:
        called.append("todo")
        return 0

    monkeypatch.setitem(registry._SCRIPT_REGISTRY, "todo", fake_todo)
    result = CliRunner().invoke(main, ["--config", str(config_path), "todo"])

    assert result.exit_code == 0
    assert called == ["todo"]


def test_ask_resolves_planner_plan_and_dispatches_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "004_cli_framework.md")
    monkeypatch.chdir(tmp_path)
    pulls: list[tuple[Path, bool]] = []
    sends: list[tuple[str, str, bool]] = []

    def fake_pull(repo_path: Path, *, dry_run: bool = False) -> None:
        pulls.append((repo_path, dry_run))

    def fake_send(agent_name: str, prompt: str, _config: object, *, dry_run: bool = False) -> None:
        sends.append((agent_name, prompt, dry_run))

    expected_prompt = (
        "Read the plan at planner/docs/plans/planning/004_cli_framework.md.\n"
        "Ask any questions until you have a clear understanding."
    )

    monkeypatch.setattr(tmux_dispatch, "git_pull", fake_pull)
    monkeypatch.setattr(tmux_dispatch, "git_current_branch", _return("plan/ask/004_cli_framework"))
    monkeypatch.setattr(tmux_dispatch, "git_commit_hash", _return("abc1234"))
    monkeypatch.setattr(tmux_dispatch, "write_audit_log", _noop)
    monkeypatch.setattr(tmux_dispatch, "send_prompt_to_agent", fake_send)

    result = CliRunner().invoke(main, ["--config", str(config_path), "ask", "004"])

    assert result.exit_code == 0
    assert pulls == [(tmp_path / "planner", False)]
    assert sends == [("planner", expected_prompt, False)]


def test_pr_uses_reviewer_repo_and_renders_branch_commit_and_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "master", "checking", "004_cli_framework.md")
    _make_plan(tmp_path, "reviewer", "checking", "004_cli_framework.md")
    monkeypatch.chdir(tmp_path)
    pulls: list[tuple[Path, bool]] = []
    prompts: list[str] = []

    def fake_pull(repo_path: Path, *, dry_run: bool = False) -> None:
        pulls.append((repo_path, dry_run))

    def fake_send(_agent_name: str, prompt: str, _config: object, *, dry_run: bool = False) -> None:
        del dry_run
        prompts.append(prompt)

    monkeypatch.setattr(tmux_dispatch, "git_pull", fake_pull)
    monkeypatch.setattr(tmux_dispatch, "git_current_branch", _return("feat/004_cli_framework"))
    monkeypatch.setattr(tmux_dispatch, "git_commit_hash", _return("1e84d3a"))
    monkeypatch.setattr(tmux_dispatch, "write_audit_log", _noop)
    monkeypatch.setattr(tmux_dispatch, "send_prompt_to_agent", fake_send)

    result = CliRunner().invoke(main, ["--config", str(config_path), "pr", "004"])

    assert result.exit_code == 0
    assert pulls == [(tmp_path / "reviewer", False)]
    assert "feat/004_cli_framework" in prompts[0]
    assert "reviewer/docs/plans/checking/004_cli_framework.md" in prompts[0]
    assert "docs/plans/reviews/pr/004_cli_framework_1e84d3a.md" in prompts[0]


def test_full_review_works_without_plan_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    prompts: list[str] = []

    def fake_send(_agent_name: str, prompt: str, _config: object, *, dry_run: bool = False) -> None:
        del dry_run
        prompts.append(prompt)

    expected_prompt = (
        "Perform a full review of the current state of the codebase.\n"
        "Search for dead code, propose integration tests, and review overall architecture.\n"
        "Write a report into docs/plans/reviews/full/."
    )

    monkeypatch.setattr(tmux_dispatch, "git_pull", _noop)
    monkeypatch.setattr(tmux_dispatch, "write_audit_log", _noop)
    monkeypatch.setattr(tmux_dispatch, "send_prompt_to_agent", fake_send)

    result = CliRunner().invoke(main, ["--config", str(config_path), "full-review"])

    assert result.exit_code == 0
    assert prompts == [expected_prompt]


def test_edit_cancellation_skips_dispatch_and_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "004_cli_framework.md")
    monkeypatch.chdir(tmp_path)
    sends: list[str] = []
    audits: list[str] = []

    def fake_send(_agent_name: str, prompt: str, _config: object, *, dry_run: bool = False) -> None:
        del dry_run
        sends.append(prompt)

    def fake_audit(_project_dir: Path, entry: AuditLogEntry) -> None:
        audits.append(entry.command)

    monkeypatch.setattr(tmux_dispatch, "git_pull", _noop)
    monkeypatch.setattr(tmux_dispatch, "git_current_branch", _return("plan/ask/004_cli_framework"))
    monkeypatch.setattr(tmux_dispatch, "git_commit_hash", _return("abc1234"))
    monkeypatch.setattr(tmux_dispatch, "_open_in_editor", _noop)
    monkeypatch.setattr(tmux_dispatch, "send_prompt_to_agent", fake_send)
    monkeypatch.setattr(tmux_dispatch, "write_audit_log", fake_audit)

    result = CliRunner().invoke(main, ["--config", str(config_path), "ask", "004", "--edit"])

    assert result.exit_code == 0
    assert "Cancelled (empty prompt)." in result.output
    assert sends == []
    assert audits == []


def test_tmux_failures_are_reported_without_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "004_cli_framework.md")
    monkeypatch.chdir(tmp_path)

    def raise_tmux(_agent_name: str, _prompt: str, _config: object, *, dry_run: bool = False) -> None:
        del dry_run
        message = "tmux session missing"
        raise WorkflowError(message)

    monkeypatch.setattr(tmux_dispatch, "git_pull", _noop)
    monkeypatch.setattr(tmux_dispatch, "git_current_branch", _return("plan/ask/004_cli_framework"))
    monkeypatch.setattr(tmux_dispatch, "git_commit_hash", _return("abc1234"))
    monkeypatch.setattr(tmux_dispatch, "send_prompt_to_agent", raise_tmux)

    result = CliRunner().invoke(main, ["--config", str(config_path), "ask", "004"])

    assert result.exit_code == 1
    assert "tmux session missing" in result.output
    assert "Traceback" not in result.output


def test_doublecheck_dry_run_resolves_planner_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "005_plan_state_and_git.md")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "--dry-run", "doublecheck", "005"])

    assert result.exit_code == 0
    assert "Target repo: planner" in result.output
    assert "[-] file-scope: skipped in dry-run" in result.output


def test_doublecheck_dry_run_resolves_reviewer_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "reviewer", "checking", "005_plan_state_and_git.md")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "--dry-run", "doublecheck", "005"])

    assert result.exit_code == 0
    assert "Target repo: reviewer" in result.output
    assert "[-] git-hooks: skipped in dry-run" in result.output


def test_doublecheck_keeps_click_arguments_across_invocations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "005_plan_state_and_git.md")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    help_result = runner.invoke(main, ["--config", str(config_path), "--help"])
    result = runner.invoke(main, ["--config", str(config_path), "--dry-run", "doublecheck", "005"])

    assert help_result.exit_code == 0
    assert result.exit_code == 0
    assert "Target repo: planner" in result.output


def test_doublecheck_prints_results_and_next_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "005_plan_state_and_git.md")
    monkeypatch.chdir(tmp_path)

    def fake_doublecheck(*_args: object, **_kwargs: object) -> list[CheckResult]:
        return [
            CheckResult(check_name="file-scope", status="pass", message="ok"),
            CheckResult(check_name="noqa-nosec", status="warn", message="1 found", details=["# noqa"]),
            CheckResult(check_name="git-hooks", status="pass", message="ok"),
            CheckResult(check_name="claude-md", status="pass", message="ok"),
        ]

    monkeypatch.setattr(workflow_commands, "run_doublecheck", fake_doublecheck)

    result = CliRunner().invoke(main, ["--config", str(config_path), "doublecheck", "005"])

    assert result.exit_code == 0
    assert "[✓] file-scope: ok" in result.output
    assert "[!] noqa-nosec: 1 found" in result.output
    assert "# noqa" in result.output
    assert "Next step: workflow todo 005_plan_state_and_git" in result.output
    assert "workflow reject" in result.output
