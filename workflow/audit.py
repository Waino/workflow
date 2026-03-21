"""Audit logging for the workflow tool.

Provides append-only JSONL write and sorted read for the workflow audit log.
The log file lives at {project_dir}/workflow.log.jsonl and is never committed.
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 - used at runtime (path arithmetic)

from pydantic import ValidationError
from rich.console import Console

from workflow.models import AuditLogEntry

AUDIT_LOG_FILENAME = "workflow.log.jsonl"

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
