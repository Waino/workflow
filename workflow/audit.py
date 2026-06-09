"""Audit logging for the workflow tool.

Provides append-only JSONL write and sorted read for the workflow audit log.
The log file lives at {project_dir}/workflow.log.jsonl and is never committed.
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import TYPE_CHECKING

from pydantic import ValidationError
from rich.console import Console

from workflow.models import AuditLogEntry, PlanState
from workflow.names import RepoName

if TYPE_CHECKING:
    from pathlib import Path

    from workflow.config import WorkflowConfig

AUDIT_LOG_FILENAME = "workflow.log.jsonl"


def build_audit_command_repo_state(config: WorkflowConfig) -> dict[str, tuple[RepoName, PlanState]]:
    """Derive the audit `command` → `(repo, state)` mapping from *config*.

    For each command (script or prompt) in any state with a non-``None``
    ``transition``, the command leaves the plan in ``transition`` state,
    owned by that state's agent (or ``master`` when no agent is configured).
    Commands without a transition contribute no entry; their audit-log
    appearances do not constrain the resolver.
    """
    # Lazy import to avoid a hard dependency from audit.py to config.py at import time.
    from workflow.config import PromptCommand, ScriptCommand

    mapping: dict[str, tuple[RepoName, PlanState]] = {}
    for state_config in config.states.values():
        for cmd in state_config.commands:
            if not isinstance(cmd, (PromptCommand, ScriptCommand)):
                continue
            if cmd.transition is None:
                continue
            target_state = cmd.transition
            target_cfg = config.states.get(target_state)
            agent = getattr(target_cfg, "agent", None) if target_cfg is not None else None
            repo = RepoName.master
            if agent is not None:
                with suppress(ValueError):
                    repo = RepoName(agent)
            mapping[cmd.name] = (repo, target_state)
    return mapping


_console = Console(stderr=True)


def write_audit_log(project_dir: Path, entry: AuditLogEntry) -> None:
    """Append one entry to the JSONL audit log.

    Creates the file if it does not exist.
    Each line is a complete JSON object terminated by a newline.
    """
    log_path = project_dir / AUDIT_LOG_FILENAME
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")


def read_audit_log(project_dir: Path) -> list[AuditLogEntry]:
    """Read all entries from the audit log, sorted by timestamp.

    Returns an empty list if the log file does not exist.
    Skips and warns on malformed lines rather than raising.
    """
    log_path = project_dir / AUDIT_LOG_FILENAME
    if not log_path.exists():
        return []

    entries: list[AuditLogEntry] = []
    with log_path.open(encoding="utf-8") as f:
        for line_number, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entries.append(AuditLogEntry.model_validate_json(line))
            except (ValidationError, json.JSONDecodeError) as exc:
                _console.print(f"[yellow]Warning: skipping malformed audit log line {line_number}: {exc}[/yellow]")

    entries.sort(key=lambda e: e.timestamp)
    return entries
