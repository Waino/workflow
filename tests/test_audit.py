"""Tests for workflow.audit."""

import json
from datetime import UTC, datetime
from pathlib import Path

from workflow.audit import AUDIT_LOG_FILENAME, read_audit_log, write_audit_log
from workflow.models import AuditLogEntry


def _entry(ts: datetime, command: str = "build", actor: str = "builder") -> AuditLogEntry:
    return AuditLogEntry(timestamp=ts, actor=actor, command=command)


_T1 = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
_T2 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
_T3 = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)


class TestWriteAuditLog:
    def test_creates_file(self, tmp_path: Path) -> None:
        entry = _entry(_T1)
        write_audit_log(tmp_path, entry)
        assert (tmp_path / AUDIT_LOG_FILENAME).exists()

    def test_file_contains_one_line(self, tmp_path: Path) -> None:
        write_audit_log(tmp_path, _entry(_T1))
        lines = (tmp_path / AUDIT_LOG_FILENAME).read_text().splitlines()
        assert len(lines) == 1

    def test_appends_multiple_entries(self, tmp_path: Path) -> None:
        write_audit_log(tmp_path, _entry(_T1))
        write_audit_log(tmp_path, _entry(_T2))
        lines = (tmp_path / AUDIT_LOG_FILENAME).read_text().splitlines()
        assert len(lines) == 2

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        write_audit_log(tmp_path, _entry(_T1, command="critique"))
        line = (tmp_path / AUDIT_LOG_FILENAME).read_text().strip()
        parsed = json.loads(line)
        assert parsed["command"] == "critique"

    def test_roundtrip(self, tmp_path: Path) -> None:
        original = AuditLogEntry(
            timestamp=_T1,
            actor="planner",
            command="subdivide",
            input_plan="001_idea",
            output_plans=["002_models", "003_resolver"],
        )
        write_audit_log(tmp_path, original)
        restored = AuditLogEntry.model_validate_json((tmp_path / AUDIT_LOG_FILENAME).read_text().strip())
        assert restored == original


class TestReadAuditLog:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_audit_log(tmp_path) == []

    def test_reads_single_entry(self, tmp_path: Path) -> None:
        write_audit_log(tmp_path, _entry(_T1))
        result = read_audit_log(tmp_path)
        assert len(result) == 1
        assert result[0].timestamp == _T1

    def test_reads_multiple_entries(self, tmp_path: Path) -> None:
        write_audit_log(tmp_path, _entry(_T1))
        write_audit_log(tmp_path, _entry(_T2))
        result = read_audit_log(tmp_path)
        assert len(result) == 2

    def test_sorted_by_timestamp(self, tmp_path: Path) -> None:
        # Write in reverse order; read must return chronological order.
        write_audit_log(tmp_path, _entry(_T3))
        write_audit_log(tmp_path, _entry(_T1))
        write_audit_log(tmp_path, _entry(_T2))
        result = read_audit_log(tmp_path)
        assert [e.timestamp for e in result] == [_T1, _T2, _T3]

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        write_audit_log(tmp_path, _entry(_T1))
        log_path = tmp_path / AUDIT_LOG_FILENAME
        log_path.write_text(log_path.read_text() + "\n\n")
        result = read_audit_log(tmp_path)
        assert len(result) == 1

    def test_skips_malformed_json_line(self, tmp_path: Path) -> None:
        write_audit_log(tmp_path, _entry(_T1))
        log_path = tmp_path / AUDIT_LOG_FILENAME
        log_path.write_text("not json at all\n" + log_path.read_text())
        result = read_audit_log(tmp_path)
        # The good line is kept; the bad one is skipped.
        assert len(result) == 1
        assert result[0].timestamp == _T1

    def test_skips_invalid_entry(self, tmp_path: Path) -> None:
        bad = json.dumps({"timestamp": "not-a-date", "actor": "human", "command": "todo"})
        good = _entry(_T2).model_dump_json()
        (tmp_path / AUDIT_LOG_FILENAME).write_text(f"{bad}\n{good}\n")
        result = read_audit_log(tmp_path)
        assert len(result) == 1
        assert result[0].timestamp == _T2

    def test_all_fields_preserved(self, tmp_path: Path) -> None:
        original = AuditLogEntry(
            timestamp=_T1,
            actor="reviewer",
            command="pr",
            input_plan="005_git",
            output_plans=["review/005"],
        )
        write_audit_log(tmp_path, original)
        result = read_audit_log(tmp_path)
        assert result[0] == original
