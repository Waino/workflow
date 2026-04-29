"""Audit logging for the workflow tool.

Provides append-only JSONL write and sorted read for the workflow audit log.
The log file lives at {project_dir}/workflow.log.jsonl and is never committed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import ValidationError
from rich.console import Console

from workflow.models import AuditLogEntry, PlanState
from workflow.names import RepoName

AUDIT_LOG_FILENAME = "workflow.log.jsonl"

# Map audit log `command` values to the (repo, state) the command leaves the plan in.
# Used by the plan resolver to derive a repo/state hint from the most recent
# audit log entry mentioning a plan. `push` is intentionally absent — generic
# pushes are not state transitions and contribute no constraint.
AUDIT_COMMAND_REPO_STATE: dict[str, tuple[RepoName, PlanState]] = {
    "new": (RepoName.master, PlanState.INTENT),
    "plan": (RepoName.planner, PlanState.PLANNING),
    "subdivide": (RepoName.planner, PlanState.PLANNING),
    "todo": (RepoName.master, PlanState.TODO),
    "implement": (RepoName.master, PlanState.IMPLEMENTING),
    "check": (RepoName.builder, PlanState.CHECKING),
    "accept": (RepoName.master, PlanState.DONE),
    "reject": (RepoName.master, PlanState.REJECTED),
}

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
