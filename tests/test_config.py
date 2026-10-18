"""Tests for workflow.config."""

from pathlib import Path

import pytest
import yaml

import workflow.commands.info
import workflow.commands.workflow  # noqa: F401  — populates script registry
from tests.conftest import create_project_marker
from workflow.config import (
    PromptCommand,
    TmuxConfig,
    WorkflowConfig,
    find_legacy_project_root,
    find_project_root,
    load_config,
)
from workflow.metadata import LEGACY_ROOT_MARKER
from workflow.models import PlanState


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory with marker and config."""
    create_project_marker(tmp_path)
    config = {
        "agents": [
            {"name": "planner", "model": "claude-sonnet-4-20250514"},
            {"name": "builder", "model": "claude-sonnet-4-20250514"},
        ],
        "states": {
            "intent": {"description": "Plans being explored.", "agent": "planner", "commands": []},
            "planning": {"description": "Plans being drafted.", "agent": "planner", "commands": []},
            "todo": {"description": "Plans approved for implementation.", "agent": None, "commands": []},
            "implementing": {"description": "Plans under active implementation.", "agent": "builder", "commands": []},
            "checking": {"description": "Review in progress.", "agent": None, "commands": []},
            "done": {"description": "Merged to main.", "agent": None, "commands": []},
            "rejected": {"description": "Will not be implemented.", "agent": None, "commands": []},
            "review": {"description": "Codebase-wide review tasks.", "agent": None, "commands": []},
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    return tmp_path


class TestFindProjectRoot:
    def test_finds_marker_in_current_dir(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        assert find_project_root(tmp_path) == tmp_path

    def test_finds_marker_in_parent(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        assert find_project_root(child) == tmp_path

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        assert find_project_root(tmp_path) is None

    def test_finds_metadata_file_from_nested_cwd(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        assert find_project_root(child) == tmp_path


class TestFindLegacyProjectRoot:
    def test_finds_legacy_marker(self, tmp_path: Path) -> None:
        (tmp_path / LEGACY_ROOT_MARKER).touch()
        assert find_legacy_project_root(tmp_path) == tmp_path

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        assert find_legacy_project_root(tmp_path) is None

    def test_ignores_metadata_file(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        assert find_legacy_project_root(tmp_path) is None


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

    # BROKEN: needs to mock the check of the default location
    # def test_raises_on_missing_config(self, tmp_path: Path) -> None:
    #     with pytest.raises(FileNotFoundError, match="No config file found"):
    #         load_config(project_dir=tmp_path)     noqa: ERA001

    def test_state_keys_are_plan_state(self, project_dir: Path) -> None:
        config = load_config(project_dir=project_dir)
        for key in config.states:
            assert isinstance(key, PlanState)

    def test_populates_metadata(self, project_dir: Path) -> None:
        config = load_config(project_dir=project_dir)
        assert config.metadata is not None
        assert config.metadata.mode.value == "in_band"


class TestLoadConfigLegacyMarker:
    def test_raises_with_migration_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / LEGACY_ROOT_MARKER).touch()
        monkeypatch.chdir(tmp_path)
        with pytest.raises(Exception, match=r"migrate_023\.py"):
            load_config()


class TestLoadConfigNoProjectRoot:
    def test_project_dir_is_none_when_no_root_found(self, tmp_path: Path) -> None:
        """When no .workflow.json exists, project_dir and project_name are None."""
        config = {
            "agents": [{"name": "planner", "model": "test"}],
            "states": {"intent": {"description": "Plans being explored.", "agent": "planner", "commands": []}},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        # No .workflow.json in tmp_path, and no find_project_root() from cwd either
        # (the dev repo has no .workflow.json marker).
        result = load_config(config_path=config_path)
        assert result.project_dir is None
        assert result.project_name is None
        assert result.metadata is None


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

    def test_rejects_duplicate_agent_names(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        config = {
            "agents": [
                {"name": "planner", "model": "test"},
                {"name": "planner", "model": "test2"},
            ],
            "states": {"intent": {"description": "Plans being explored.", "agent": "planner", "commands": []}},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(config))
        with pytest.raises(Exception, match="Duplicate agent names"):
            load_config(project_dir=tmp_path)

    def test_rejects_multiple_manual_shell_agents(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        config = {
            "agents": [
                {"name": "planner", "model": "test", "manual_shell": True},
                {"name": "builder", "model": "test", "manual_shell": True},
            ],
            "states": {"intent": {"description": "Plans being explored.", "agent": "planner", "commands": []}},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(config))
        with pytest.raises(Exception, match="At most one agent may set manual_shell"):
            load_config(project_dir=tmp_path)

    def test_accepts_single_manual_shell_agent(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        config = {
            "agents": [
                {"name": "planner", "model": "test"},
                {"name": "builder", "model": "test", "manual_shell": True},
            ],
            "states": {"intent": {"description": "Plans being explored.", "agent": "planner", "commands": []}},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(config))
        loaded = load_config(project_dir=tmp_path)
        assert [a.name for a in loaded.agents if a.manual_shell] == ["builder"]


class TestLoadConfigMalformedYAML:
    def test_raises_value_error_on_bad_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text(": bad: yaml: [unmatched")
        with pytest.raises(ValueError, match="Could not parse config file"):
            load_config(project_dir=tmp_path)


class TestDescriptionValidation:
    def test_rejects_whitespace_only_state_description(self, project_dir: Path) -> None:
        config_data = yaml.safe_load((project_dir / "config.yaml").read_text())
        config_data["states"]["intent"]["description"] = "   \t  "
        (project_dir / "config.yaml").write_text(yaml.dump(config_data))
        with pytest.raises(Exception, match="description must not be empty"):
            load_config(project_dir=project_dir)

    def test_rejects_whitespace_only_command_description(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        config = {
            "agents": [{"name": "planner", "model": "test"}],
            "states": {
                "intent": {
                    "description": "Plans being explored.",
                    "agent": "planner",
                    "commands": [
                        {"command_type": "manual", "name": "noop", "description": "   "},
                    ],
                },
            },
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(config))
        with pytest.raises(Exception, match="description for command 'noop' must not be empty"):
            load_config(project_dir=tmp_path)


class TestCommandModels:
    def test_prompt_command_in_config(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        config = {
            "agents": [{"name": "planner", "model": "test"}],
            "states": {
                "intent": {
                    "description": "Plans being explored.",
                    "agent": "planner",
                    "commands": [
                        {
                            "command_type": "prompt",
                            "name": "ask",
                            "description": "Ask clarifying questions.",
                            "prompt": "Ask questions.",
                            "transition": "planning",
                            "reminder": "review",
                        },
                        {
                            "command_type": "script",
                            "name": "todo",
                            "description": "Approve for implementation.",
                            "script": "todo",
                            "transition": "todo",
                        },
                        {"command_type": "manual", "name": "answer", "description": "Human answers questions."},
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
        assert commands[0].reminder == "review"
        assert commands[1].command_type == "script"
        assert commands[1].script == "todo"
        assert commands[2].command_type == "manual"


class TestTmuxConfig:
    def test_defaults(self) -> None:
        cfg = TmuxConfig()
        assert cfg.session_name is None
        assert cfg.prompt_send_delay == 1.0

    def test_loaded_from_yaml(self, tmp_path: Path) -> None:
        create_project_marker(tmp_path)
        config = {
            "agents": [{"name": "planner", "model": "test"}],
            "tmux": {"session_name": "myproject", "prompt_send_delay": 0.5},
            "states": {"intent": {"description": "Plans being explored.", "agent": "planner", "commands": []}},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(config))
        loaded = load_config(project_dir=tmp_path)
        assert loaded.tmux.session_name == "myproject"
        assert loaded.tmux.prompt_send_delay == 0.5

    def test_tmux_defaults_when_omitted(self, project_dir: Path) -> None:
        """When no tmux section is in YAML, defaults are used."""
        loaded = load_config(project_dir=project_dir)
        assert loaded.tmux.session_name is None
        assert loaded.tmux.prompt_send_delay == 1.0

    @pytest.mark.parametrize(
        ("explicit", "project_name", "expected"),
        [
            pytest.param("pinned", "proj", "pinned", id="explicit"),
            pytest.param(None, "proj", "proj", id="derived"),
            pytest.param(None, "my.proj:x", "my_proj_x", id="sanitized"),
            pytest.param(None, None, "workflow", id="no_project"),
            pytest.param(None, "", "workflow", id="empty_name"),
        ],
    )
    def test_session_name(self, explicit: str | None, project_name: str | None, expected: str) -> None:
        cfg = WorkflowConfig(agents=[], states={}, tmux=TmuxConfig(session_name=explicit), project_name=project_name)
        assert cfg.session_name == expected


class TestDefaultConfig:
    def test_default_config_loads(self, tmp_path: Path) -> None:
        """Verify docs/default_config.yaml is valid and parseable."""
        default_path = Path(__file__).parent.parent / "docs" / "default_config.yaml"
        if not default_path.exists():
            pytest.skip("default config not found")
        # Use a dummy project dir since we're just validating the YAML structure
        create_project_marker(tmp_path)
        config = load_config(config_path=default_path, project_dir=tmp_path)
        assert len(config.agents) == 3
        assert PlanState.INTENT in config.states
        assert PlanState.REVIEW in config.states


def test_default_config_rebase_prompts_are_identical() -> None:
    config = load_config(Path(__file__).parents[1] / "docs" / "default_config.yaml", project_dir=None)
    prompts = {
        state: cmd.prompt
        for state, state_config in config.states.items()
        for cmd in state_config.commands
        if isinstance(cmd, PromptCommand) and cmd.name == "rebase"
    }
    assert set(prompts) == {PlanState.PLANNING, PlanState.IMPLEMENTING, PlanState.CHECKING}
    assert len(set(prompts.values())) == 1
