"""Tests for workflow.tmux_session."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import create_project_marker
from workflow.config import WorkflowConfig, load_config
from workflow.exceptions import WorkflowError
from workflow.names import WorktreeName
from workflow.tmux_session import PROJECT_DIR_OPTION, active_tmux_repo, find_workflow_session


def _config(tmp_path: Path) -> WorkflowConfig:
    create_project_marker(tmp_path)
    shutil.copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


def _server(*, alive: bool = True, session: MagicMock | None = None) -> MagicMock:
    server = MagicMock()
    server.is_alive.return_value = alive
    server.sessions.get.return_value = session
    return server


def _session(tag: str | None, window: str = "builder") -> MagicMock:
    session = MagicMock()
    session.show_option.side_effect = lambda option: tag if option == PROJECT_DIR_OPTION else None
    session.active_window.name = window
    return session


def test_matching_tag_returns_session(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = _session(str(tmp_path))
    with patch("workflow.tmux_session.libtmux.Server", return_value=_server(session=session)) as fake:
        assert find_workflow_session(config) is session
    fake.return_value.sessions.get.assert_called_once_with(session_name=config.session_name, default=None)


@pytest.mark.parametrize("tag", [pytest.param("/other/project", id="foreign"), pytest.param(None, id="missing")])
def test_foreign_or_missing_tag_raises(tmp_path: Path, tag: str | None) -> None:
    config = _config(tmp_path)
    with (
        patch("workflow.tmux_session.libtmux.Server", return_value=_server(session=_session(tag))),
        pytest.raises(WorkflowError, match=r"tmux\.session_name.*kill-session") as exc,
    ):
        find_workflow_session(config)
    assert (tag in str(exc.value)) if tag else ("unknown project" in str(exc.value))


def test_no_session_or_dead_server_returns_none(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with patch("workflow.tmux_session.libtmux.Server", return_value=_server()):
        assert find_workflow_session(config) is None
    with patch("workflow.tmux_session.libtmux.Server", return_value=_server(alive=False)):
        assert find_workflow_session(config) is None


def test_without_project_dir_skips_tag_check() -> None:
    config = WorkflowConfig(agents=[], states={})
    session = _session(None)
    with patch("workflow.tmux_session.libtmux.Server", return_value=_server(session=session)):
        assert find_workflow_session(config) is session


def test_active_tmux_repo(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with patch("workflow.tmux_session.libtmux.Server", return_value=_server(session=_session(str(tmp_path)))):
        assert active_tmux_repo(config) == WorktreeName.builder


def test_active_tmux_repo_none_on_foreign_session(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with patch("workflow.tmux_session.libtmux.Server", return_value=_server(session=_session("/other"))):
        assert active_tmux_repo(config) is None
