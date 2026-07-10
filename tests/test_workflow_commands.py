"""Tests for workflow script command orchestration."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

import workflow.commands.info as wi
import workflow.commands.workflow as wc
from workflow import main
from workflow.audit import read_audit_log, write_audit_log
from workflow.commands.info import _parse_datetime
from workflow.config import WORKFLOW_ROOT_MARKER
from workflow.exceptions import WorkflowError
from workflow.git_helpers import CommitResult
from workflow.models import AuditLogEntry, Plan, PlanState
from workflow.plan_resolver import next_intent_number
from workflow.validate import CheckResult


def _make_project(tmp_path: Path, *, marker: bool = True) -> Path:
    """Create a minimal project directory tree and return the config path.

    Note: the per-repo directories are *not* initialised as git repositories.
    Tests that need git-side behaviour (current branch, push, fetch, the
    git-history resolver source) must either monkey-patch the relevant helpers
    or initialise the repo themselves.
    """
    if marker:
        (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    for repo in ("canon", "planner", "builder", "reviewer"):
        (tmp_path / repo).mkdir(exist_ok=True)
    (tmp_path / "canon" / ".git-pre-commit-hook").touch()
    return tmp_path / "config.yaml"


def _make_plan(project_dir: Path, repo: str, state: str, filename: str) -> None:
    path = project_dir / repo / "docs" / "plans" / state
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text("# plan\n", encoding="utf-8")


def _plan(repo: str, state: PlanState, name: str = "005_foo") -> Plan:
    return Plan(sequence_number=[5], slug="foo", state=state, path=Path(f"{repo}/docs/plans/{state.value}/{name}.md"))


def test_init_runs_clone_commands_and_creates_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path, marker=False)
    (tmp_path / "canon" / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    # Single manager mock so we can assert call ordering across all init steps.
    manager = MagicMock()
    monkeypatch.setattr(wc, "generate_and_write_agents_md", manager.generate_and_write_agents_md)
    monkeypatch.setattr(wc, "git_add", manager.git_add)
    monkeypatch.setattr(wc, "git_commit_if_changed", manager.git_commit_if_changed)
    monkeypatch.setattr(wc, "run_command", manager.run_command)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])

    assert result.exit_code == 0, result.output
    project_dir = tmp_path.resolve()
    run_command_calls = [c.args[0] for c in manager.run_command.call_args_list]
    assert run_command_calls == [
        ["git", "clone", "--bare", "canon", "bare"],
        ["git", "clone", "bare", "planner"],
        ["git", "clone", "bare", "builder"],
        ["git", "clone", "bare", "reviewer"],
        ["git", "config", "push.autoSetupRemote", "true"],  # planner
        ["git", "config", "push.default", "current"],  # planner
        [
            "ln",
            "-s",
            "../../.git-pre-commit-hook",
            ".git/hooks/pre-commit",
        ],  # planner
        ["git", "config", "push.autoSetupRemote", "true"],  # builder
        ["git", "config", "push.default", "current"],  # builder
        [
            "ln",
            "-s",
            "../../.git-pre-commit-hook",
            ".git/hooks/pre-commit",
        ],  # builder
        ["git", "config", "push.autoSetupRemote", "true"],  # reviewer
        ["git", "config", "push.default", "current"],  # reviewer
        [
            "ln",
            "-s",
            "../../.git-pre-commit-hook",
            ".git/hooks/pre-commit",
        ],  # reviewer
        ["git", "remote", "add", "origin", str(project_dir / "bare")],
        ["git", "config", "push.autoSetupRemote", "true"],  # canon
        ["git", "config", "push.default", "current"],  # canon
        ["git", "push", "-u", "origin", "HEAD"],
        [
            "run_agent_in_docker.sh",
            "test_init_runs_clone_commands_0",
            "planner",
            "--project-dir",
            str(project_dir),
            "--venv",
            "--reset",
        ],
        [
            "run_agent_in_docker.sh",
            "test_init_runs_clone_commands_0",
            "builder",
            "--project-dir",
            str(project_dir),
            "--venv",
            "--reset",
        ],
        [
            "run_agent_in_docker.sh",
            "test_init_runs_clone_commands_0",
            "reviewer",
            "--project-dir",
            str(project_dir),
            "--venv",
            "--reset",
        ],
    ]
    assert (tmp_path / WORKFLOW_ROOT_MARKER).exists()

    # AGENTS.md must be generated, added, and committed before the bare clone.
    method_names = [name for name, _, _ in manager.method_calls]
    gen_idx = method_names.index("generate_and_write_agents_md")
    add_idx = method_names.index("git_add")
    commit_idx = method_names.index("git_commit_if_changed")
    bare_idx = next(
        i
        for i, call in enumerate(manager.method_calls)
        if call[0] == "run_command" and call.args and call.args[0][:3] == ["git", "clone", "--bare"]
    )
    assert gen_idx < add_idx < commit_idx < bare_idx


def test_init_succeeds_when_agents_md_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Init must continue past the AGENTS.md commit step when there's nothing to commit."""
    config_path = _make_project(tmp_path, marker=False)
    (tmp_path / "canon" / ".git").mkdir()
    (tmp_path / "canon" / ".git-pre-commit-hook").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "generate_and_write_agents_md", MagicMock())
    monkeypatch.setattr(wc, "git_add", MagicMock())
    monkeypatch.setattr(wc, "git_commit_if_changed", MagicMock(return_value=CommitResult.NOTHING_TO_COMMIT))
    monkeypatch.setattr(wc, "run_command", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / WORKFLOW_ROOT_MARKER).exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_init_fails_when_bare_exists_but_is_not_a_valid_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leftover, non-bare-repo `bare/` directory must fail the precondition, not get cloned over."""
    config_path = _make_project(tmp_path, marker=False)
    (tmp_path / "canon" / ".git").mkdir()
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    (bare_dir / "leftover.txt").write_text("junk\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_run_command = MagicMock()
    monkeypatch.setattr(wc, "run_command", fake_run_command)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])

    assert result.exit_code == 1
    assert "not a valid bare clone" in result.output
    fake_run_command.assert_not_called()


def test_start_runs_tmux_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    fake_create = MagicMock()
    monkeypatch.setattr(wc, "create_workflow_session", fake_create)
    monkeypatch.setattr(wc, "attach_workflow_session", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "start"])

    assert result.exit_code == 0, result.output
    _config, windows, control_window = fake_create.call_args.args
    assert control_window == "planner"
    assert [name for name, _cmd in windows] == ["planner", "builder", "reviewer", "builder-shell"]

    # Verify the actual command strings passed for each window.
    # No run_agent_in_docker.sh in tmp_path, so the bare script name is used.
    script = "run_agent_in_docker.sh"
    project = tmp_path.name
    project_dir = str(tmp_path.resolve())
    cmds = dict(windows)
    assert cmds["planner"] == f"{script} {project} planner --project-dir {project_dir}"
    assert cmds["builder"] == f"{script} {project} builder --project-dir {project_dir}"
    assert cmds["reviewer"] == f"{script} {project} reviewer --project-dir {project_dir}"
    assert cmds["builder-shell"] == f"{script} {project} builder --project-dir {project_dir} --shell"


def test_start_reset_flag_threads_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--reset is appended to each agent command but NOT to the builder-shell command."""
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    fake_create = MagicMock()
    monkeypatch.setattr(wc, "create_workflow_session", fake_create)
    monkeypatch.setattr(wc, "attach_workflow_session", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "start", "--reset"])

    assert result.exit_code == 0
    _config, windows, _control = fake_create.call_args.args
    cmds = dict(windows)
    assert cmds["planner"].endswith("--reset")
    assert cmds["builder"].endswith("--reset")
    assert cmds["reviewer"].endswith("--reset")
    assert "--reset" not in cmds["builder-shell"]  # builder-shell must not carry --reset


def test_start_uses_project_local_script_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Script found in project_dir is used as an absolute path instead of bare name."""
    config_path = _make_project(tmp_path)
    local_script = tmp_path / "run_agent_in_docker.sh"
    local_script.touch()
    monkeypatch.chdir(tmp_path)
    fake_create = MagicMock()
    monkeypatch.setattr(wc, "create_workflow_session", fake_create)
    monkeypatch.setattr(wc, "attach_workflow_session", MagicMock())

    CliRunner().invoke(main, ["--config", str(config_path), "start"])

    _config, windows, _control = fake_create.call_args.args
    # The command string for the first agent window should start with the absolute path.
    assert dict(windows)["planner"].startswith(str(tmp_path.resolve()))


def test_start_attaches_without_recreating_when_session_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "workflow_session_exists", MagicMock(return_value=True))
    fake_create = MagicMock()
    monkeypatch.setattr(wc, "create_workflow_session", fake_create)
    monkeypatch.setattr(wc, "attach_workflow_session", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "start"])

    assert result.exit_code == 0, result.output
    fake_create.assert_not_called()


def test_start_reset_refuses_self_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "workflow_session_exists", MagicMock(return_value=True))
    monkeypatch.setattr(wc, "is_inside_workflow_session", MagicMock(return_value=True))
    fake_kill = MagicMock()
    monkeypatch.setattr(wc, "kill_workflow_session", fake_kill)

    result = CliRunner().invoke(main, ["--config", str(config_path), "start", "--reset"])

    assert result.exit_code == 1
    assert "refusing to kill the session you are attached to" in result.output
    fake_kill.assert_not_called()


def test_start_reset_kills_and_recreates_existing_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "workflow_session_exists", MagicMock(return_value=True))
    monkeypatch.setattr(wc, "is_inside_workflow_session", MagicMock(return_value=False))
    fake_kill = MagicMock()
    monkeypatch.setattr(wc, "kill_workflow_session", fake_kill)
    fake_create = MagicMock()
    monkeypatch.setattr(wc, "create_workflow_session", fake_create)
    monkeypatch.setattr(wc, "attach_workflow_session", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "start", "--reset"])

    assert result.exit_code == 0, result.output
    fake_kill.assert_called_once()
    fake_create.assert_called_once()


def test_new_uses_existing_plans_and_audit_log_for_numbering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "intent", "001_idea.md")
    write_audit_log(
        tmp_path,
        AuditLogEntry(
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            actor="human",
            command="new",
            output_plans=["002_bar"],
        ),
    )
    fake_open = MagicMock()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "_open_in_editor", fake_open)
    monkeypatch.setattr(wc, "git_add", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "new", "flux_capacitor"])

    assert result.exit_code == 0
    expected = tmp_path / "canon" / "docs" / "plans" / "intent" / "003_flux_capacitor.md"
    assert expected.exists()
    assert fake_open.call_args.args[0] == expected


def test_todo_transitions_and_prints_implement_reminder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    call_order: list[str] = []
    monkeypatch.setattr(
        wc,
        "run_validate",
        MagicMock(return_value=[CheckResult(check_name="x", status="pass", message="ok")]),
    )
    fake_transfer = MagicMock(side_effect=lambda *_a, **_kw: call_order.append("transfer"))
    fake_commit = MagicMock(side_effect=lambda *_a, **_kw: call_order.append("commit"))
    fake_push = MagicMock(side_effect=lambda *_a, **_kw: call_order.append("push"))
    monkeypatch.setattr(wc, "transfer_branch_to_main", fake_transfer)
    monkeypatch.setattr(wc, "transition_plan", MagicMock(return_value=_plan("canon", PlanState.TODO)))
    monkeypatch.setattr(wc, "git_commit", fake_commit)
    monkeypatch.setattr(wc, "git_push", fake_push)

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "005"])

    assert result.exit_code == 0
    assert "workflow implement 005_foo" in result.output
    assert call_order == ["transfer", "commit", "push"]


def _stub_implement_builder_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the builder-side git/tmux work so implement tests stay repo-free."""
    monkeypatch.setattr(wc, "git_status_porcelain", MagicMock(return_value=""))
    monkeypatch.setattr(wc, "git_branch_exists", MagicMock(return_value=False))
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="main"))
    monkeypatch.setattr(wc, "git_index_lock_present", MagicMock(return_value=False))
    monkeypatch.setattr(wc, "git_commit_if_changed", MagicMock(return_value=CommitResult.COMMITTED))
    monkeypatch.setattr(wc, "git_fetch", MagicMock())
    monkeypatch.setattr(wc, "git_checkout", MagicMock())
    monkeypatch.setattr(wc, "git_pull", MagicMock())
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock())


def test_implement_prints_explicit_ask_reminder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "todo", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "transition_plan", MagicMock(return_value=_plan("canon", PlanState.IMPLEMENTING)))
    monkeypatch.setattr(wc, "git_commit", MagicMock())
    monkeypatch.setattr(wc, "git_push", MagicMock())
    _stub_implement_builder_setup(monkeypatch)
    monkeypatch.setattr(wc.time, "sleep", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005"])

    assert result.exit_code == 0
    assert "workflow ask 005_foo" in result.output


def test_implement_rejects_dirty_builder_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dirty builder working tree aborts implement before any state transition."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "todo", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    fake_transition = MagicMock()
    monkeypatch.setattr(wc, "transition_plan", fake_transition)
    monkeypatch.setattr(wc, "git_status_porcelain", MagicMock(return_value=" M file.py"))
    monkeypatch.setattr(wc, "git_branch_exists", MagicMock(return_value=False))

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005"])

    assert result.exit_code == 1
    assert "dirty" in result.output
    fake_transition.assert_not_called()


def test_implement_rejects_existing_feature_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-existing feat/<plan> branch (plan not yet implementing) requires --continue."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "todo", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    fake_transition = MagicMock()
    monkeypatch.setattr(wc, "transition_plan", fake_transition)
    monkeypatch.setattr(wc, "git_status_porcelain", MagicMock(return_value=""))
    monkeypatch.setattr(wc, "git_branch_exists", MagicMock(return_value=True))
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="main"))
    monkeypatch.setattr(wc, "git_index_lock_present", MagicMock(return_value=False))

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005"])

    assert result.exit_code == 1
    assert "pass --continue" in result.output
    fake_transition.assert_not_called()


def test_check_runs_pre_commit_and_prints_review_reminder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "builder", "implementing", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    fake_run_command = MagicMock()
    monkeypatch.setattr(wc, "run_command", fake_run_command)
    monkeypatch.setattr(
        wc,
        "run_validate",
        MagicMock(return_value=[CheckResult(check_name="x", status="pass", message="ok")]),
    )
    monkeypatch.setattr(wc, "transition_plan", lambda _p, ts, *_a, **_kw: _plan("builder", ts))
    monkeypatch.setattr(wc, "git_commit", MagicMock())
    monkeypatch.setattr(wc, "git_commit_if_changed", MagicMock(return_value=CommitResult.COMMITTED))
    monkeypatch.setattr(wc, "git_push", MagicMock())
    monkeypatch.setattr(wc, "git_fetch", MagicMock())
    monkeypatch.setattr(wc, "git_index_lock_present", MagicMock(return_value=False))
    fake_rotate = MagicMock()
    monkeypatch.setattr(wc, "rotate_control_pane", fake_rotate)
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="feat/005_foo"))
    monkeypatch.setattr(wc, "git_checkout", MagicMock())
    monkeypatch.setattr(wc, "git_pull", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "check", "005"])

    assert result.exit_code == 0, result.output
    first_call_args = fake_run_command.call_args_list[0].args[0]
    assert first_call_args[:2] == ["docker", "exec"]
    assert first_call_args[6].endswith("-builder")
    assert "Next step" in result.output
    assert "review" in result.output
    # The reviewer window is rotated into view after the handoff.
    assert fake_rotate.call_args.args[1] == "reviewer"


def test_check_resume_skips_precommit_and_validate_when_already_checking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed `check` (plan already transitioned) must not re-run pre-commit/validate."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "builder", "checking", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    fake_precommit = MagicMock()
    fake_validate = MagicMock()
    monkeypatch.setattr(wc, "_run_pre_commit_in_sandbox", fake_precommit)
    monkeypatch.setattr(wc, "run_validate", fake_validate)
    monkeypatch.setattr(wc, "transition_plan", MagicMock())
    monkeypatch.setattr(wc, "git_commit_if_changed", MagicMock(return_value=CommitResult.COMMITTED))
    monkeypatch.setattr(wc, "git_push", MagicMock())
    monkeypatch.setattr(wc, "git_fetch", MagicMock())
    monkeypatch.setattr(wc, "git_index_lock_present", MagicMock(return_value=False))
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="main"))
    monkeypatch.setattr(wc, "git_checkout", MagicMock())
    monkeypatch.setattr(wc, "git_pull", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "check", "005"])

    assert result.exit_code == 0, result.output
    fake_precommit.assert_not_called()
    fake_validate.assert_not_called()


def test_push_pushes_resolved_repo_without_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "intent", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    fake_push = MagicMock()
    fake_pull = MagicMock()
    monkeypatch.setattr(wc, "git_push", fake_push)
    monkeypatch.setattr(wc, "git_pull", fake_pull)

    result = CliRunner().invoke(main, ["--config", str(config_path), "push", "005"])

    assert result.exit_code == 0
    assert [c.args[0] for c in fake_push.call_args_list] == [tmp_path / "canon"]
    # intent-state push has pull_target=planner — assert pull_target was also called
    assert [c.args[0] for c in fake_pull.call_args_list] == [tmp_path / "planner"]
    assert "workflow ask 005_foo" in result.output


def test_accept_and_reject_run_in_expected_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "reviewer", "checking", "005_foo.md")
    _make_plan(tmp_path, "reviewer", "implementing", "006_bar.md")
    monkeypatch.chdir(tmp_path)
    fake_push = MagicMock()
    fake_pull = MagicMock()
    fake_merge = MagicMock()
    fake_commit = MagicMock()
    fake_checkout = MagicMock()

    def _current_branch(repo_path: Path) -> str:
        return "main" if repo_path == tmp_path / "canon" else "feat/005_foo"

    monkeypatch.setattr(wc, "git_current_branch", _current_branch)
    monkeypatch.setattr(
        wc,
        "transition_plan",
        lambda _plan_arg, target_state, *_args, **_kwargs: _plan(
            "canon" if target_state == PlanState.DONE else "reviewer",
            target_state,
        ),
    )
    monkeypatch.setattr(wc, "git_push", fake_push)
    monkeypatch.setattr(wc, "git_pull", fake_pull)
    monkeypatch.setattr(wc, "git_merge", fake_merge)
    monkeypatch.setattr(wc, "git_commit", fake_commit)
    monkeypatch.setattr(wc, "git_commit_if_changed", fake_commit)
    monkeypatch.setattr(wc, "git_checkout", fake_commit)

    accept_result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005"])
    reject_result = CliRunner().invoke(
        main,
        ["--config", str(config_path), "reject", "--target-repo", "reviewer", "006"],
    )

    assert accept_result.exit_code == 0
    assert reject_result.exit_code == 0
    all_repos = (
        [c.args[0] for c in fake_push.call_args_list]
        + [c.args[0] for c in fake_pull.call_args_list]
        + [c.args[0] for c in fake_commit.call_args_list]
        + [c.args[0] for c in fake_checkout.call_args_list]
    )
    assert tmp_path / "reviewer" in all_repos
    assert tmp_path / "canon" in all_repos
    # §2.10 audit-log assertions
    entries = read_audit_log(tmp_path)
    commands = [e.command for e in entries]
    assert "accept" in commands
    assert "reject" in commands
    # accept produces no next-step reminder (done state has no commands)
    assert "Next step:" not in accept_result.output


# ---------------------------------------------------------------------------
# next_intent_number (moved to plan_resolver)
# ---------------------------------------------------------------------------


def test_next_intent_number_accounts_for_plans_and_audit_log(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "intent", "001_idea.md")
    write_audit_log(
        tmp_path,
        AuditLogEntry(
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            actor="human",
            command="new",
            output_plans=["002_bar"],
        ),
    )
    assert next_intent_number(tmp_path) == 3


# ---------------------------------------------------------------------------
# new: template rendering and git_add
# ---------------------------------------------------------------------------


def test_new_renders_template_and_calls_git_add(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "_open_in_editor", MagicMock())
    fake_git_add = MagicMock()
    monkeypatch.setattr(wc, "git_add", fake_git_add)

    result = CliRunner().invoke(main, ["--config", str(config_path), "new", "flux_capacitor"])

    assert result.exit_code == 0
    expected = tmp_path / "canon" / "docs" / "plans" / "intent" / "001_flux_capacitor.md"
    assert expected.exists()
    content = expected.read_text(encoding="utf-8")
    assert "# 001: Flux Capacitor" in content
    assert "## Goal" in content
    assert [(c.args[0], c.args[1]) for c in fake_git_add.call_args_list] == [
        (tmp_path / "canon", Path("docs/plans/intent/001_flux_capacitor.md")),
    ]


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------


def test_parse_datetime_accepts_full_iso8601() -> None:
    dt = _parse_datetime("2026-03-18T14:30:00+00:00")
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 18
    assert dt.hour == 14
    assert dt.tzinfo is not None


def test_parse_datetime_accepts_date_only() -> None:
    dt = _parse_datetime("2026-03-18")
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 18
    assert dt.hour == 0
    assert dt.tzinfo is not None


def test_parse_datetime_treats_naive_as_utc() -> None:
    dt = _parse_datetime("2026-03-18T14:30:00")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_parse_datetime_rejects_garbage() -> None:
    with pytest.raises(ValueError, match=r"."):
        _parse_datetime("not-a-date")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_shows_active_plans_and_next_free_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "intent", "003_foo.md")
    _make_plan(tmp_path, "canon", "implementing", "001_bar.md")
    _make_plan(tmp_path, "canon", "done", "002_baz.md")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wi, "git_status", MagicMock(return_value=""))

    result = CliRunner().invoke(main, ["--config", str(config_path), "status"])

    assert result.exit_code == 0
    assert "001" in result.output
    assert "003" in result.output
    # done plan excluded by default
    assert "002" not in result.output
    assert "last action" in result.output
    assert "next step" in result.output
    assert "Next free number: 004" in result.output


def test_status_shows_done_plans_with_all_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "done", "002_baz.md")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wi, "git_status", MagicMock(return_value=""))

    result = CliRunner().invoke(main, ["--config", str(config_path), "status", "--all"])

    assert result.exit_code == 0
    assert "002" in result.output


def test_status_last_action_and_next_step_from_audit_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "implementing", "001_foo.md")
    write_audit_log(
        tmp_path,
        AuditLogEntry(
            timestamp=datetime(2026, 3, 18, tzinfo=UTC),
            actor="builder",
            command="build",
            input_plan="001_foo",
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wi, "git_status", MagicMock(return_value=""))

    result = CliRunner().invoke(main, ["--config", str(config_path), "status"])

    assert result.exit_code == 0
    # last-action column shows the executed command; next step is the configured reminder.
    assert "build" in result.output
    assert "check" in result.output


def test_status_cross_state_next_step_via_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`todo` is defined in PLANNING; running it transitions the plan to TODO.

    For a plan now in TODO with the last logged action ``todo``, the heuristic
    should locate the source state (PLANNING) and resolve the next step to the
    first command in TODO ("implement").
    """
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "todo", "001_foo.md")
    write_audit_log(
        tmp_path,
        AuditLogEntry(
            timestamp=datetime(2026, 3, 18, tzinfo=UTC),
            actor="planner",
            command="todo",
            input_plan="001_foo",
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wi, "git_status", MagicMock(return_value=""))

    result = CliRunner().invoke(main, ["--config", str(config_path), "status"])

    assert result.exit_code == 0
    assert "todo" in result.output
    assert "implement" in result.output


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def _write_entries(tmp_path: Path) -> None:
    for actor, command, ts, input_plan, output_plans in [
        ("human", "new", datetime(2026, 3, 1, tzinfo=UTC), None, ["001_foo"]),
        ("human", "todo", datetime(2026, 3, 2, tzinfo=UTC), "001_foo", []),
        ("planner", "plan", datetime(2026, 3, 3, tzinfo=UTC), "001_foo", ["002_bar"]),
        ("human", "new", datetime(2026, 3, 4, tzinfo=UTC), None, ["003_baz"]),
    ]:
        write_audit_log(
            tmp_path,
            AuditLogEntry(
                timestamp=ts,
                actor=actor,
                command=command,
                input_plan=input_plan,
                output_plans=output_plans,
            ),
        )


def test_log_no_filter_shows_all_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _write_entries(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log"])

    assert result.exit_code == 0
    assert "001_foo" in result.output
    assert "003_baz" in result.output


def test_log_filter_by_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _write_entries(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--agent", "planner"])

    assert result.exit_code == 0
    assert "plan" in result.output
    assert "todo" not in result.output


def test_log_filter_by_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _write_entries(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--command", "new"])

    assert result.exit_code == 0
    assert "001_foo" in result.output
    assert "003_baz" in result.output
    assert "todo" not in result.output


def test_log_filter_by_since(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _write_entries(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--since", "2026-03-03"])

    assert result.exit_code == 0
    assert "002_bar" in result.output
    assert "003_baz" in result.output
    assert "todo" not in result.output  # 2026-03-02 excluded


def test_log_filter_by_plan_substring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _write_entries(tmp_path)
    monkeypatch.chdir(tmp_path)
    # "001" matches 001_foo but resolve_plan will fail (no actual plan files) → substring fallback
    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--plan", "baz"])

    assert result.exit_code == 0
    assert "003_baz" in result.output
    assert "001_foo" not in result.output


def test_log_since_invalid_value_exits_with_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--since", "not-a-date"])

    assert result.exit_code != 0


def test_log_filter_by_until(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _write_entries(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--until", "2026-03-02"])

    assert result.exit_code == 0
    assert "001_foo" in result.output
    assert "todo" in result.output
    assert "002_bar" not in result.output  # 2026-03-03 excluded


def test_log_filter_by_plan_strict_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the plan resolves uniquely the substring-fallback notice must NOT appear."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "intent", "001_foo.md")
    write_audit_log(
        tmp_path,
        AuditLogEntry(
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            actor="human",
            command="new",
            output_plans=["001_foo"],
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--plan", "001_foo"])

    assert result.exit_code == 0
    assert "001_foo" in result.output
    assert "falling back to substring match" not in result.output


def test_log_filter_by_plan_fallback_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresolvable --plan value triggers the fallback notice in the output."""
    config_path = _make_project(tmp_path)
    _write_entries(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "log", "--plan", "baz"])

    assert result.exit_code == 0
    assert "falling back to substring match" in result.output


def test_status_shows_rejected_plan_only_with_all_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "rejected", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wi, "git_status", MagicMock(return_value=""))

    result_default = CliRunner().invoke(main, ["--config", str(config_path), "status"])
    result_all = CliRunner().invoke(main, ["--config", str(config_path), "status", "--all"])

    assert result_default.exit_code == 0
    assert "005" not in result_default.output
    assert result_all.exit_code == 0
    assert "005" in result_all.output


def test_new_dry_run_does_not_create_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "--dry-run", "new", "my_feature"])

    assert result.exit_code == 0
    expected = tmp_path / "canon" / "docs" / "plans" / "intent" / "001_my_feature.md"
    assert not expected.exists()
    assert "Would create" in result.output
    assert "Would open in $EDITOR" in result.output


def test_new_rejects_invalid_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    for bad_slug in ("Foo", "foo/bar", "../tmp/pwn", "foo bar", ""):
        result = CliRunner().invoke(main, ["--config", str(config_path), "new", bad_slug])
        assert result.exit_code != 0, f"Expected non-zero exit for slug {bad_slug!r}"
        assert "Invalid slug" in result.output


def test_implement_from_planning_branch_transfers_and_transitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Implement from a PLANNING plan: transfer planner->canon (inlined+guarded), then TODO, IMPLEMENTING."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "planner", "planning", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    call_order: list[str] = []
    monkeypatch.setattr(wc, "git_push", MagicMock(side_effect=lambda *_a, **_kw: call_order.append("push")))
    monkeypatch.setattr(wc, "git_pull", MagicMock(side_effect=lambda *_a, **_kw: call_order.append("pull")))
    monkeypatch.setattr(wc, "git_merge", MagicMock(side_effect=lambda *_a, **_kw: call_order.append("merge")))
    monkeypatch.setattr(wc, "git_checkout", MagicMock(side_effect=lambda *_a, **_kw: call_order.append("checkout")))

    def _commit_if_changed(*_a: object, **_kw: object) -> CommitResult:
        call_order.append("commit")
        return CommitResult.COMMITTED

    monkeypatch.setattr(wc, "git_commit_if_changed", _commit_if_changed)
    monkeypatch.setattr(
        wc,
        "transition_plan",
        MagicMock(
            side_effect=[
                _plan("canon", PlanState.TODO),
                _plan("canon", PlanState.IMPLEMENTING),
            ],
        ),
    )
    monkeypatch.setattr(wc, "git_status_porcelain", MagicMock(return_value=""))
    monkeypatch.setattr(wc, "git_branch_exists", MagicMock(return_value=False))
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="feat/planner-branch"))
    monkeypatch.setattr(wc, "git_index_lock_present", MagicMock(return_value=False))
    monkeypatch.setattr(wc, "git_fetch", MagicMock())
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock())
    monkeypatch.setattr(wc.time, "sleep", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "implement", "005"])

    assert result.exit_code == 0, result.output
    # push planner -> pull canon -> merge -> push canon -> checkout planner main -> pull planner
    # -> commit (todo) -> commit (implementing) -> push canon -> checkout builder main -> pull builder
    # -> checkout feat branch
    assert call_order == [
        "push",
        "pull",
        "merge",
        "push",
        "checkout",
        "pull",
        "commit",
        "commit",
        "push",
        "checkout",
        "pull",
        "checkout",
    ]
    assert "workflow ask 005_foo" in result.output
    entries = read_audit_log(tmp_path)
    assert any(e.command == "implement" for e in entries)


def test_accept_main_branch_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept exits non-zero when reviewer is on 'main' instead of a feature branch."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "reviewer", "checking", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="main"))

    result = CliRunner().invoke(main, ["--config", str(config_path), "accept", "005"])

    assert result.exit_code != 0
    assert "main" in result.output


def test_init_fails_without_canon_git_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Init exits non-zero when canon/.git is missing."""
    config_path = _make_project(tmp_path, marker=False)
    # Do NOT create canon/.git — the precheck should fire.
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "init"])

    assert result.exit_code != 0
    assert "no .git/" in result.output


def test_todo_resolver_error_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Todo exits non-zero with a red message when the plan cannot be resolved."""
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    fake_push = MagicMock()
    fake_commit = MagicMock()
    monkeypatch.setattr(wc, "git_push", fake_push)
    monkeypatch.setattr(wc, "git_commit", fake_commit)

    result = CliRunner().invoke(main, ["--config", str(config_path), "todo", "999_does_not_exist"])

    assert result.exit_code != 0
    fake_push.assert_not_called()
    fake_commit.assert_not_called()


def test_push_with_pull_target_pulls_after_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Push from canon with pull_target=planner runs git_push then git_pull(planner)."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "intent", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    fake_push = MagicMock()
    fake_pull = MagicMock()
    monkeypatch.setattr(wc, "git_push", fake_push)
    monkeypatch.setattr(wc, "git_pull", fake_pull)

    result = CliRunner().invoke(main, ["--config", str(config_path), "push", "005"])

    assert result.exit_code == 0
    assert fake_push.call_args_list[0].args[0] == tmp_path / "canon"
    assert fake_pull.call_args_list[0].args[0] == tmp_path / "planner"


def test_push_main_branch_guard_non_canon_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Push raises error when source repo is not canon and current branch is 'main'."""
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    # The default config always resolves push to canon; bypass the resolver
    # to exercise the defensive guard for non-canon repos.
    monkeypatch.setattr(wc, "resolve_plan", MagicMock(return_value=_plan("planner", PlanState.PLANNING)))
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="main"))
    monkeypatch.setattr(wc, "git_push", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "push", "005"])

    assert result.exit_code != 0
    assert "main" in result.output


def test_push_canon_on_main_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Push from canon on 'main' is allowed (canon legitimately pushes intent/todo)."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "canon", "intent", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "git_push", MagicMock())
    monkeypatch.setattr(wc, "git_pull", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "push", "005"])

    assert result.exit_code == 0


def test_go_agent_rotates_and_prints_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "assert_in_workflow_session", MagicMock())
    fake_rotate = MagicMock()
    fake_status = MagicMock()
    monkeypatch.setattr(wc, "rotate_control_pane", fake_rotate)
    monkeypatch.setattr(wc, "git_status", fake_status)

    result = CliRunner().invoke(main, ["--config", str(config_path), "go", "builder"])

    assert result.exit_code == 0, result.output
    assert fake_rotate.call_args.args[1] == "builder"
    assert fake_status.call_args.args[0] == tmp_path / "builder"


def test_go_manual_resolves_manual_shell_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`go manual` rotates to builder-shell and shows the manual_shell agent's repo."""
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "assert_in_workflow_session", MagicMock())
    fake_rotate = MagicMock()
    fake_status = MagicMock()
    monkeypatch.setattr(wc, "rotate_control_pane", fake_rotate)
    monkeypatch.setattr(wc, "git_status", fake_status)

    result = CliRunner().invoke(main, ["--config", str(config_path), "go", "manual"])

    assert result.exit_code == 0, result.output
    assert fake_rotate.call_args.args[1] == "builder-shell"
    # Default config marks the builder as the manual-shell agent.
    assert fake_status.call_args.args[0] == tmp_path / "builder"


def test_go_rejects_invalid_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "assert_in_workflow_session", MagicMock())
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "go", "nope"])

    assert result.exit_code == 1
    assert "invalid go target" in result.output


def test_go_dry_run_skips_session_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    fake_guard = MagicMock()
    monkeypatch.setattr(wc, "assert_in_workflow_session", fake_guard)

    result = CliRunner().invoke(main, ["--config", str(config_path), "--dry-run", "go", "builder"])

    assert result.exit_code == 0, result.output
    fake_guard.assert_not_called()
    assert "join-pane" in result.output


def test_go_writes_no_audit_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc, "assert_in_workflow_session", MagicMock())
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock())
    monkeypatch.setattr(wc, "git_status", MagicMock())

    result = CliRunner().invoke(main, ["--config", str(config_path), "go", "reviewer"])

    assert result.exit_code == 0, result.output
    assert all(entry.command != "go" for entry in read_audit_log(tmp_path))


def test_rotate_best_effort_downgrades_error_to_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rotation failure in implement/check is warned about, not fatal."""
    config_path = _make_project(tmp_path)
    _make_plan(tmp_path, "builder", "implementing", "005_foo.md")
    monkeypatch.chdir(tmp_path)
    fake_run_command = MagicMock()
    monkeypatch.setattr(wc, "run_command", fake_run_command)
    monkeypatch.setattr(
        wc,
        "run_validate",
        MagicMock(return_value=[CheckResult(check_name="x", status="pass", message="ok")]),
    )
    monkeypatch.setattr(wc, "transition_plan", lambda _p, ts, *_a, **_kw: _plan("builder", ts))
    monkeypatch.setattr(wc, "git_commit", MagicMock())
    monkeypatch.setattr(wc, "git_commit_if_changed", MagicMock(return_value=CommitResult.COMMITTED))
    monkeypatch.setattr(wc, "git_push", MagicMock())
    monkeypatch.setattr(wc, "git_fetch", MagicMock())
    monkeypatch.setattr(wc, "git_index_lock_present", MagicMock(return_value=False))
    monkeypatch.setattr(wc.time, "sleep", MagicMock())
    monkeypatch.setattr(wc, "git_current_branch", MagicMock(return_value="feat/005_foo"))
    monkeypatch.setattr(wc, "git_checkout", MagicMock())
    monkeypatch.setattr(wc, "git_pull", MagicMock())
    monkeypatch.setattr(wc, "rotate_control_pane", MagicMock(side_effect=WorkflowError("boom")))

    result = CliRunner().invoke(main, ["--config", str(config_path), "check", "005"])

    assert result.exit_code == 0, result.output
    assert "tmux rotation skipped" in result.output
