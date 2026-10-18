"""Direct unit tests for workflow/next_step.py."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile

import pytest

from tests.conftest import create_project_marker
from workflow.config import WorkflowConfig, load_config
from workflow.models import PlanState
from workflow.next_step import find_command, find_step_by_name, format_next_step


def _make_config(tmp_path: Path) -> WorkflowConfig:
    create_project_marker(tmp_path)
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


@pytest.mark.parametrize(
    ("state", "command", "expected"),
    [
        (PlanState.PLANNING, "todo", "workflow implement 005_foo"),  # command reminder
        (PlanState.INTENT, "ask", "workflow critique 005_foo"),
        (PlanState.CHECKING, "validate", "accept or reject"),  # free text
        (PlanState.IMPLEMENTING, "check", "manual review, then pr"),  # free text with a comma
        (PlanState.INTENT, "architecture", "workflow mvp 005_foo"),
        (PlanState.CHECKING, "accept", None),  # no reminder, and no fallback to the target state
        (PlanState.IMPLEMENTING, "rebase", "workflow check 005_foo"),
    ],
)
def test_format_next_step(tmp_path: Path, state: PlanState, command: str, expected: str | None) -> None:
    config = _make_config(tmp_path)
    cmd = find_command(config, state, command)
    assert cmd is not None

    assert format_next_step(config, cmd, "005_foo") == expected


def test_format_next_step_uses_hyphenated_multi_word_name(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    cmd = find_command(config, PlanState.CHECKING, "fix")
    assert cmd is not None
    # 'fix' -> 'validate'; emulate a multi-word target by renaming the reminder.
    multi = cmd.model_copy(update={"reminder": "full review"})

    assert format_next_step(config, multi, "005_foo") == "workflow full-review 005_foo"


class TestFindStepByName:
    def test_finds_known_command(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        cmd = find_step_by_name(config, "todo")
        assert cmd is not None
        assert cmd.name == "todo"

    def test_returns_none_for_unknown_name(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        assert find_step_by_name(config, "nonexistent_command_xyz") is None
