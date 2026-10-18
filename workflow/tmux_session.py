"""Lookup of the project's tmux session, guarded against sessions owned by another project."""

from __future__ import annotations

import libtmux
import libtmux.exc

from workflow.config import WorkflowConfig
from workflow.exceptions import WorkflowError
from workflow.names import WorktreeName

PROJECT_DIR_OPTION = "@workflow_project_dir"


def find_workflow_session(config: WorkflowConfig) -> libtmux.Session | None:
    """Return the project's tmux session, or ``None`` if the server is down or the session is absent.

    Raises :class:`WorkflowError` if the session is tagged with (or lacks) a different project directory.
    """
    server = libtmux.Server()
    if not server.is_alive():
        return None
    name = config.session_name
    session = server.sessions.get(session_name=name, default=None)
    if session is None or config.project_dir is None:
        return session
    owner = session.show_option(PROJECT_DIR_OPTION)
    if owner is None or str(owner) != str(config.project_dir):
        whose = f"the project at {owner}" if owner else "an unknown project"
        msg = (
            f"tmux session {name!r} belongs to {whose}. Set 'tmux.session_name' in config.yaml "
            f"to use another session name, or remove the session with: tmux kill-session -t {name}"
        )
        raise WorkflowError(msg)
    return session


def active_tmux_repo(config: WorkflowConfig) -> WorktreeName | None:
    """Return the WorktreeName of the active window in the project's tmux session, or None."""
    try:
        session = find_workflow_session(config)
        if session is None:
            return None
        active = session.active_window
        if active is None or active.name is None:
            return None
        return WorktreeName(active.name)
    except (OSError, libtmux.exc.LibTmuxException, WorkflowError, ValueError):
        return None
