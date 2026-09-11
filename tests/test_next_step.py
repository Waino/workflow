"""Direct unit tests for workflow/next_step.py."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile

import pytest

from tests.conftest import create_project_marker
from workflow.config import ManualStep, PromptCommand, ScriptCommand, WorkflowConfig, load_config
from workflow.models import PlanState
from workflow.next_step import (
    find_source_state_for_command,
    find_step_by_name,
    format_next_step_invocation,
)


def _make_config(tmp_path: Path) -> WorkflowConfig:
    create_project_marker(tmp_path)
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


# ---------------------------------------------------------------------------
# format_next_step_invocation
# ---------------------------------------------------------------------------


class TestFormatNextStepInvocation:
    def test_none_command_returns_reminder_as_is(self) -> None:
        assert format_next_step_invocation("ask", "005_foo", command=None) == "ask"

    def test_manual_command_returns_reminder_as_is(self) -> None:
        cmd = ManualStep(command_type="manual", name="answer", description="Human answers questions.")
        assert format_next_step_invocation("answer", "005_foo", command=cmd) == "answer"

    def test_script_command_formats_cli_invocation(self) -> None:
        cmd = ScriptCommand(
            command_type="script",
            name="todo",
            script="todo",
            description="Approve for implementation.",
        )
        result = format_next_step_invocation("todo", "005_foo", command=cmd)
        assert result == "workflow todo 005_foo"

    def test_prompt_command_formats_cli_invocation(self) -> None:
        cmd = PromptCommand(
            command_type="prompt",
            name="ask",
            prompt="Ask about {{ plan.path }}.",
            description="Ask clarifying questions.",
        )
        result = format_next_step_invocation("ask", "005_foo", command=cmd)
        assert result == "workflow ask 005_foo"

    def test_multi_word_name_uses_hyphenated_form(self) -> None:
        cmd = PromptCommand(
            command_type="prompt",
            name="full review",
            prompt="Do a full review.",
            description="Perform a full review.",
        )
        result = format_next_step_invocation("full review", "005_foo", command=cmd)
        assert result == "workflow full-review 005_foo"


# ---------------------------------------------------------------------------
# find_step_by_name
# ---------------------------------------------------------------------------


class TestFindStepByName:
    def test_finds_known_command(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        cmd = find_step_by_name(config, "todo")
        assert cmd is not None
        assert cmd.name == "todo"

    def test_returns_none_for_unknown_name(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        assert find_step_by_name(config, "nonexistent_command_xyz") is None


# ---------------------------------------------------------------------------
# find_source_state_for_command
# ---------------------------------------------------------------------------


class TestFindSourceStateForCommand:
    def test_transition_match_preferred(self, tmp_path: Path) -> None:
        # 'todo' is in PLANNING with transition→TODO; plan is now in TODO.
        # The function should identify PLANNING as the source state.
        config = _make_config(tmp_path)
        result = find_source_state_for_command(config, PlanState.TODO, "todo")
        assert result == PlanState.PLANNING

    def test_same_state_fallback(self, tmp_path: Path) -> None:
        # 'ask' is in INTENT with no transition; plan is still in INTENT.
        config = _make_config(tmp_path)
        result = find_source_state_for_command(config, PlanState.INTENT, "ask")
        assert result == PlanState.INTENT

    def test_unknown_command_returns_none(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        result = find_source_state_for_command(config, PlanState.INTENT, "nonexistent_xyz")
        assert result is None

    @pytest.mark.parametrize(
        ("plan_state", "command", "expected_source"),
        [
            (PlanState.TODO, "todo", PlanState.PLANNING),
            (PlanState.IMPLEMENTING, "implement", PlanState.TODO),
            (PlanState.CHECKING, "check", PlanState.IMPLEMENTING),
            (PlanState.DONE, "accept", PlanState.CHECKING),
        ],
    )
    def test_transition_states_parametrised(
        self,
        tmp_path: Path,
        plan_state: PlanState,
        command: str,
        expected_source: PlanState,
    ) -> None:
        config = _make_config(tmp_path)
        result = find_source_state_for_command(config, plan_state, command)
        assert result == expected_source
