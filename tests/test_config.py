"""Tests for workflow.config."""

from pathlib import Path

import pytest
import yaml

from workflow.config import (
    WORKFLOW_ROOT_MARKER,
    find_project_root,
    load_config,
)
from workflow.models import PlanState


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory with marker and config."""
    (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    config = {
        "agents": [
            {"name": "planner", "model": "claude-sonnet-4-20250514"},
            {"name": "builder", "model": "claude-sonnet-4-20250514"},
        ],
        "states": {
            "intent": {"agent": "planner", "commands": []},
            "planning": {"agent": "planner", "commands": []},
            "todo": {"agent": None, "commands": []},
            "implementing": {"agent": "builder", "commands": []},
            "checking": {"agent": None, "commands": []},
            "done": {"agent": None, "commands": []},
            "rejected": {"agent": None, "commands": []},
            "review": {"agent": None, "commands": []},
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    return tmp_path


class TestFindProjectRoot:
    def test_finds_marker_in_current_dir(self, tmp_path: Path) -> None:
        (tmp_path / WORKFLOW_ROOT_MARKER).touch()
        assert find_project_root(tmp_path) == tmp_path

    def test_finds_marker_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / WORKFLOW_ROOT_MARKER).touch()
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        assert find_project_root(child) == tmp_path

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        assert find_project_root(tmp_path) is None


class TestLoadConfig:
    def test_loads_from_project_dir(self, project_dir: Path) -> None:
        config = load_config(project_dir=project_dir)
        assert config.project_dir == project_dir
        assert config.project_name == project_dir.name
        assert len(config.agents) == 2
        assert PlanState.INTENT in config.states

    def test_loads_from_explicit_path(self, project_dir: Path) -> None:
        config_path = project_dir / "config.yaml"
        config = load_config(config_path=config_path, project_dir=project_dir)
        assert config.project_name == project_dir.name

    def test_raises_on_missing_config(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No config file found"):
            load_config(project_dir=tmp_path)

    def test_state_keys_are_plan_state(self, project_dir: Path) -> None:
        config = load_config(project_dir=project_dir)
        for key in config.states:
            assert isinstance(key, PlanState)


class TestWorkflowConfigValidation:
    def test_rejects_unknown_agent_in_state(self, project_dir: Path) -> None:
        config_data = yaml.safe_load((project_dir / "config.yaml").read_text())
        config_data["states"]["intent"]["agent"] = "nonexistent"
        (project_dir / "config.yaml").write_text(yaml.dump(config_data))
        with pytest.raises(Exception, match="unknown agent"):
            load_config(project_dir=project_dir)

    def test_accepts_null_agent(self, project_dir: Path) -> None:
        config = load_config(project_dir=project_dir)
        assert config.states[PlanState.TODO].agent is None


class TestCommandModels:
    def test_prompt_command_in_config(self, tmp_path: Path) -> None:
        (tmp_path / WORKFLOW_ROOT_MARKER).touch()
        config = {
            "agents": [{"name": "planner", "model": "test"}],
            "states": {
                "intent": {
                    "agent": "planner",
                    "commands": [
                        {
                            "command_type": "prompt",
                            "name": "ask",
                            "prompt": "Ask questions.",
                            "transition": "planning",
                        },
                        {"command_type": "script", "name": "todo", "script": "todo", "transition": "todo"},
                        {"command_type": "manual", "name": "answer"},
                    ],
                },
            },
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(config))
        loaded = load_config(project_dir=tmp_path)
        commands = loaded.states[PlanState.INTENT].commands
        assert len(commands) == 3
        assert commands[0].command_type == "prompt"
        assert commands[0].transition == PlanState.PLANNING
        assert commands[1].command_type == "script"
        assert commands[1].script == "todo"
        assert commands[2].command_type == "manual"


class TestDefaultConfig:
    def test_default_config_loads(self) -> None:
        """Verify docs/default_config.yaml is valid and parseable."""
        default_path = Path(__file__).parent.parent / "docs" / "default_config.yaml"
        if not default_path.exists():
            pytest.skip("default config not found")
        # Use a dummy project dir since we're just validating the YAML structure
        config = load_config(config_path=default_path, project_dir=Path("/tmp"))
        assert len(config.agents) == 3
        assert PlanState.INTENT in config.states
        assert PlanState.REVIEW in config.states
