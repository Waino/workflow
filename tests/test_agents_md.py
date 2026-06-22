"""Tests for workflow.agents_md and workflow.commands.agents_md."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from shutil import copyfile
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

import workflow.commands.agents_md as cm_cmd
from workflow import main
from workflow.agents_md import (
    MARKER_BEGIN,
    MARKER_END,
    HashFileV1,
    ManagedSection,
    append_managed_section,
    compute_hash_file_contents,
    find_managed_section,
    normalise,
    parse_hash_file,
    render_workflow_section,
    replace_managed_section,
)
from workflow.config import WORKFLOW_ROOT_MARKER, WorkflowConfig, load_config
from workflow.exceptions import AgentsMdGenerationError

# ---------------------------------------------------------------------------
# normalise
# ---------------------------------------------------------------------------


def test_normalise_strips_trailing_whitespace() -> None:
    assert normalise("foo   \nbar\n") == "foo\nbar\n"


def test_normalise_converts_crlf() -> None:
    assert normalise("a\r\nb\r\n") == "a\nb\n"


def test_normalise_adds_trailing_newline() -> None:
    assert normalise("hello") == "hello\n"


def test_normalise_drops_trailing_blank_lines() -> None:
    assert normalise("a\n\n\n") == "a\n"


def test_normalise_idempotent() -> None:
    text = "line one\nline two\n"
    assert normalise(normalise(text)) == normalise(text)


# ---------------------------------------------------------------------------
# find_managed_section
# ---------------------------------------------------------------------------


def test_find_managed_section_returns_none_on_empty_file() -> None:
    assert find_managed_section("") is None


def test_find_managed_section_returns_none_when_no_markers() -> None:
    assert find_managed_section("# Hello\nsome text\n") is None


def test_find_managed_section_returns_span_for_valid_pair() -> None:
    text = f"before\n{MARKER_BEGIN}\ncontent\n{MARKER_END}\nafter\n"
    span = find_managed_section(text)
    assert isinstance(span, ManagedSection)
    assert span.begin_line == 1
    assert span.end_line == 3


def test_find_managed_section_ignores_indented_markers() -> None:
    text = f"  {MARKER_BEGIN}\n  {MARKER_END}\n"
    assert find_managed_section(text) is None


def test_find_managed_section_raises_on_duplicate_begin() -> None:
    text = f"{MARKER_BEGIN}\n{MARKER_BEGIN}\n{MARKER_END}\n"
    with pytest.raises(AgentsMdGenerationError, match="2 BEGIN"):
        find_managed_section(text)


def test_find_managed_section_raises_on_reversed_markers() -> None:
    text = f"{MARKER_END}\n{MARKER_BEGIN}\n"
    with pytest.raises(AgentsMdGenerationError, match="END marker"):
        find_managed_section(text)


def test_find_managed_section_raises_on_begin_only() -> None:
    with pytest.raises(AgentsMdGenerationError, match="1 BEGIN and 0 END"):
        find_managed_section(f"{MARKER_BEGIN}\nbody\n")


def test_find_managed_section_raises_on_end_only() -> None:
    with pytest.raises(AgentsMdGenerationError, match="0 BEGIN and 1 END"):
        find_managed_section(f"body\n{MARKER_END}\n")


def test_find_managed_section_raises_on_two_pairs() -> None:
    text = f"{MARKER_BEGIN}\na\n{MARKER_END}\n{MARKER_BEGIN}\nb\n{MARKER_END}\n"
    with pytest.raises(AgentsMdGenerationError, match="2 BEGIN and 2 END"):
        find_managed_section(text)


def test_find_managed_section_ignores_marker_substrings() -> None:
    # Markers must equal the line exactly (column-0 rule); substring shouldn't match.
    text = f"prefix {MARKER_BEGIN} suffix\nfoo {MARKER_END} bar\n"
    assert find_managed_section(text) is None


# ---------------------------------------------------------------------------
# replace_managed_section and append_managed_section
# ---------------------------------------------------------------------------


def test_replace_managed_section_substitutes_content() -> None:
    text = f"before\n{MARKER_BEGIN}\nold content\n{MARKER_END}\nafter\n"
    span = find_managed_section(text)
    assert span is not None
    result = replace_managed_section(text, span, "new content\n")
    assert MARKER_BEGIN in result
    assert MARKER_END in result
    assert "new content" in result
    assert "old content" not in result
    assert "before" in result
    assert "after" in result


def test_replace_managed_section_preserves_markers_and_surroundings_byte_for_byte() -> None:
    # Pin the exact output to catch off-by-one slicing errors.
    text = f"line 1\nline 2\n{MARKER_BEGIN}\nold\nmore old\n{MARKER_END}\nline 3\nline 4\n"
    span = find_managed_section(text)
    assert span is not None
    result = replace_managed_section(text, span, "new\nnew2\n")
    assert result == f"line 1\nline 2\n{MARKER_BEGIN}\nnew\nnew2\n{MARKER_END}\nline 3\nline 4\n"


def test_append_managed_section_adds_markers_at_eof() -> None:
    text = "# My file\nsome content\n"
    result = append_managed_section(text, "managed\n")
    assert result.endswith(f"{MARKER_END}\n")
    assert MARKER_BEGIN in result
    assert "managed" in result
    assert "some content" in result


def test_append_managed_section_on_empty_file() -> None:
    result = append_managed_section("", "managed\n")
    assert result == f"{MARKER_BEGIN}\nmanaged\n{MARKER_END}\n"


# ---------------------------------------------------------------------------
# hash file
# ---------------------------------------------------------------------------


def test_compute_hash_file_contents_format() -> None:
    content = compute_hash_file_contents("hello\n", b"config")
    tokens = content.strip().split()
    assert len(tokens) == 3
    assert tokens[0] == "v1"
    assert len(tokens[1]) == 64  # sha256 hex
    assert len(tokens[2]) == 64


def test_parse_hash_file_returns_model() -> None:
    sha = "a" * 64
    cfg = "b" * 64
    result = parse_hash_file(f"v1 {sha} {cfg}\n")
    assert isinstance(result, HashFileV1)
    assert result.version == "v1"
    assert result.agents_md_sha == sha
    assert result.config_yaml_sha == cfg


def test_parse_hash_file_raises_on_wrong_version() -> None:
    with pytest.raises(AgentsMdGenerationError, match="v1"):
        parse_hash_file(f"v2 {'a' * 64} {'b' * 64}\n")


def test_parse_hash_file_raises_on_malformed() -> None:
    with pytest.raises(AgentsMdGenerationError, match="3 space-separated tokens"):
        parse_hash_file("v1 onlytwo\n")


# ---------------------------------------------------------------------------
# render_workflow_section
# ---------------------------------------------------------------------------


def _load_default_config(tmp_path: Path) -> WorkflowConfig:
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


def test_render_workflow_section_contains_state_headings(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    section = render_workflow_section(config)
    assert "### intent" in section
    assert "### implementing" in section
    assert "### done" in section


_GOLDEN_PATH = Path(__file__).parent / "fixtures" / "agents_md_default_section.txt"


def test_render_workflow_section_matches_golden(tmp_path: Path) -> None:
    """Pin the rendered output against a fixture; regenerate with UPDATE_GOLDEN=1."""
    config = _load_default_config(tmp_path)
    actual = render_workflow_section(config)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        _GOLDEN_PATH.write_text(actual, encoding="utf-8")
    expected = _GOLDEN_PATH.read_text(encoding="utf-8")
    assert actual == expected, "rendered section differs from golden fixture; re-run with UPDATE_GOLDEN=1 if intended"


def test_render_workflow_section_is_byte_identical_across_calls(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    assert render_workflow_section(config) == render_workflow_section(config)


# ---------------------------------------------------------------------------
# generate_and_write_agents_md
# ---------------------------------------------------------------------------


def test_generate_and_write_writes_files(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    master = tmp_path / "master"
    master.mkdir()
    cm_cmd.generate_and_write_agents_md(config)
    agents_md = tmp_path / "master" / "AGENTS.md"
    assert agents_md.exists()
    assert MARKER_BEGIN in agents_md.read_text(encoding="utf-8")
    assert (tmp_path / ".agents_md_hash").exists()


def test_generate_and_write_is_idempotent(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    master = tmp_path / "master"
    master.mkdir()
    cm_cmd.generate_and_write_agents_md(config)
    first_content = (tmp_path / "master" / "AGENTS.md").read_text(encoding="utf-8")
    cm_cmd.generate_and_write_agents_md(config)
    second_content = (tmp_path / "master" / "AGENTS.md").read_text(encoding="utf-8")
    assert first_content == second_content


def test_generate_and_write_raises_on_malformed_markers(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    master = tmp_path / "master"
    master.mkdir()
    bad_text = f"{MARKER_END}\n{MARKER_BEGIN}\n"  # reversed
    (master / "AGENTS.md").write_text(bad_text, encoding="utf-8")
    with pytest.raises(AgentsMdGenerationError):
        cm_cmd.generate_and_write_agents_md(config)


def test_generate_and_write_agents_md_is_world_readable(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    (tmp_path / "master").mkdir()
    cm_cmd.generate_and_write_agents_md(config)
    mode = (tmp_path / "master" / "AGENTS.md").stat().st_mode & 0o777
    assert mode == 0o644


# ---------------------------------------------------------------------------
# CLI: workflow agents_md
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / WORKFLOW_ROOT_MARKER).touch()
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    for repo in ("master", "planner", "builder", "reviewer"):
        (tmp_path / repo).mkdir(exist_ok=True)
    return tmp_path / "config.yaml"


def test_agents_md_command_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "agents_md"])

    assert result.exit_code == 0
    agents_md = tmp_path / "master" / "AGENTS.md"
    assert agents_md.exists()
    assert "AGENTS.md written." in result.output
    assert "git diff" in result.output


def test_agents_md_command_no_git_invocations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    with patch.object(subprocess, "run") as mock_run:
        CliRunner().invoke(main, ["--config", str(config_path), "agents_md"])
        # No git commands should be run by the standalone agents_md command
        for call in mock_run.call_args_list:
            args = call.args[0] if call.args else []
            assert not (args and args[0] == "git"), f"Unexpected git call: {args}"


def test_agents_md_command_idempotent_prints_up_to_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["--config", str(config_path), "agents_md"])
    # Second run should be idempotent
    result = runner.invoke(main, ["--config", str(config_path), "agents_md"])
    assert result.exit_code == 0
    assert "up to date" in result.output


def test_agents_md_check_exits_one_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Write a stale AGENTS.md without markers
    (tmp_path / "master" / "AGENTS.md").write_text("# Stale content\n", encoding="utf-8")

    result = runner.invoke(main, ["--config", str(config_path), "agents_md", "--check"])
    assert result.exit_code == 1


def test_agents_md_check_exits_zero_when_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Generate first to bring into sync
    runner.invoke(main, ["--config", str(config_path), "agents_md"])

    result = runner.invoke(main, ["--config", str(config_path), "agents_md", "--check"])
    assert result.exit_code == 0


def test_agents_md_command_reports_malformed_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    # Reversed markers — find_managed_section raises AgentsMdGenerationError.
    (tmp_path / "master" / "AGENTS.md").write_text(f"{MARKER_END}\n{MARKER_BEGIN}\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["--config", str(config_path), "agents_md"])
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_agents_md_command_outside_project_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.yaml"
    config = {
        "agents": [{"name": "planner", "model": "test"}],
        "states": {"intent": {"description": "Plans being explored.", "agent": "planner", "commands": []}},
    }
    config_path.write_text(yaml.dump(config))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["--config", str(config_path), "agents_md"])
    assert result.exit_code != 0


def test_render_workflow_section_ends_with_newline(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    section = render_workflow_section(config)
    assert section.endswith("\n")
