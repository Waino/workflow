"""Tests for workflow.agents_md and workflow.commands.agents_md."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from shutil import copyfile
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

import workflow.commands.agents_md as cm_cmd
from tests.conftest import create_project_marker
from workflow import main
from workflow.agents_md import (
    MARKER_BEGIN,
    MARKER_END,
    HashFileV2,
    ManagedSection,
    append_managed_section,
    compute_hash_file_contents,
    find_managed_section,
    normalise,
    parse_hash_file,
    render_workflow_section,
    replace_managed_section,
)
from workflow.config import WorkflowConfig, load_config
from workflow.exceptions import AgentsMdGenerationError, WorkflowError
from workflow.metadata import METADATA_FILENAME, CodeRepoMeta, ProjectMetadata, ProjectMode, write_metadata

# ---------------------------------------------------------------------------
# normalise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("foo   \nbar\n", "foo\nbar\n", id="strips_trailing_whitespace"),
        pytest.param("a\r\nb\r\n", "a\nb\n", id="converts_crlf"),
        pytest.param("hello", "hello\n", id="adds_trailing_newline"),
        pytest.param("a\n\n\n", "a\n", id="drops_trailing_blank_lines"),
    ],
)
def test_normalise(text: str, expected: str) -> None:
    assert normalise(text) == expected


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
    content = compute_hash_file_contents("hello\n", b"config", b"workflow_json")
    tokens = content.strip().split()
    assert len(tokens) == 4
    assert tokens[0] == "v2"
    assert len(tokens[1]) == 64  # sha256 hex
    assert len(tokens[2]) == 64
    assert len(tokens[3]) == 64


def test_parse_hash_file_returns_model() -> None:
    sha = "a" * 64
    cfg = "b" * 64
    wf = "c" * 64
    result = parse_hash_file(f"v2 {sha} {cfg} {wf}\n")
    assert isinstance(result, HashFileV2)
    assert result.version == "v2"
    assert result.agents_md_sha == sha
    assert result.config_yaml_sha == cfg
    assert result.workflow_json_sha == wf


def test_parse_hash_file_raises_on_wrong_version() -> None:
    with pytest.raises(AgentsMdGenerationError, match="v2"):
        parse_hash_file(f"v1 {'a' * 64} {'b' * 64} {'c' * 64}\n")


def test_parse_hash_file_raises_on_malformed() -> None:
    with pytest.raises(AgentsMdGenerationError, match="4 space-separated tokens"):
        parse_hash_file("v2 onlytwo\n")


# ---------------------------------------------------------------------------
# render_workflow_section
# ---------------------------------------------------------------------------


def _load_default_config(tmp_path: Path) -> WorkflowConfig:
    create_project_marker(tmp_path)
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
    canon = tmp_path / "canon"
    canon.mkdir()
    cm_cmd.generate_and_write_agents_md(config)
    agents_md = tmp_path / "canon" / "AGENTS.md"
    assert agents_md.exists()
    assert MARKER_BEGIN in agents_md.read_text(encoding="utf-8")
    assert (tmp_path / ".agents_md_hash").exists()


def test_generate_and_write_is_idempotent(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    canon = tmp_path / "canon"
    canon.mkdir()
    cm_cmd.generate_and_write_agents_md(config)
    first_content = (tmp_path / "canon" / "AGENTS.md").read_text(encoding="utf-8")
    cm_cmd.generate_and_write_agents_md(config)
    second_content = (tmp_path / "canon" / "AGENTS.md").read_text(encoding="utf-8")
    assert first_content == second_content


def test_generate_and_write_raises_on_malformed_markers(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    canon = tmp_path / "canon"
    canon.mkdir()
    bad_text = f"{MARKER_END}\n{MARKER_BEGIN}\n"  # reversed
    (canon / "AGENTS.md").write_text(bad_text, encoding="utf-8")
    with pytest.raises(AgentsMdGenerationError):
        cm_cmd.generate_and_write_agents_md(config)


def test_generate_and_write_agents_md_is_world_readable(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path)
    (tmp_path / "canon").mkdir()
    cm_cmd.generate_and_write_agents_md(config)
    mode = (tmp_path / "canon" / "AGENTS.md").stat().st_mode & 0o777
    assert mode == 0o644


# ---------------------------------------------------------------------------
# CLI: workflow agents_md
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    create_project_marker(tmp_path)
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    for repo in ("canon", "planner", "builder", "reviewer"):
        (tmp_path / repo).mkdir(exist_ok=True)
    return tmp_path / "config.yaml"


def test_agents_md_command_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "agents_md"])

    assert result.exit_code == 0
    agents_md = tmp_path / "canon" / "AGENTS.md"
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
    (tmp_path / "canon" / "AGENTS.md").write_text("# Stale content\n", encoding="utf-8")

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
    (tmp_path / "canon" / "AGENTS.md").write_text(f"{MARKER_END}\n{MARKER_BEGIN}\n", encoding="utf-8")

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


# ---------------------------------------------------------------------------
# prepare_agents_md_update / generate_and_write_agents_md — oob mode
# ---------------------------------------------------------------------------


def _load_oob_config(tmp_path: Path) -> WorkflowConfig:
    write_metadata(
        tmp_path,
        ProjectMetadata(mode=ProjectMode.oob, code_repos=[CodeRepoMeta(name="mypackage")]),
    )
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    for worktree in ("canon", "planner", "builder", "reviewer"):
        (tmp_path / worktree / "docs").mkdir(parents=True)
    (tmp_path / "canon" / "docs" / "body_of_agents.md").write_text("# Hand-written body\n", encoding="utf-8")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


def test_prepare_agents_md_update_oob_writes_four_targets_from_body(tmp_path: Path) -> None:
    config = _load_oob_config(tmp_path)

    update = cm_cmd.prepare_agents_md_update(config)

    assert update.write_targets == [
        tmp_path / "canon" / "AGENTS.md",
        tmp_path / "planner" / "AGENTS.md",
        tmp_path / "builder" / "AGENTS.md",
        tmp_path / "reviewer" / "AGENTS.md",
    ]
    assert update.new_text.startswith("# Hand-written body")
    assert MARKER_BEGIN in update.new_text


def test_prepare_agents_md_update_oob_missing_body_raises(tmp_path: Path) -> None:
    config = _load_oob_config(tmp_path)
    (tmp_path / "canon" / "docs" / "body_of_agents.md").unlink()
    with pytest.raises(AgentsMdGenerationError, match=r"body_of_agents\.md not found"):
        cm_cmd.prepare_agents_md_update(config)


def test_agents_md_command_oob_missing_body_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _load_oob_config(tmp_path)
    cm_cmd.generate_and_write_agents_md(config)
    before = (tmp_path / "builder" / "AGENTS.md").read_text(encoding="utf-8")
    (tmp_path / "canon" / "docs" / "body_of_agents.md").unlink()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "agents_md"])

    assert result.exit_code == 1
    assert "body_of_agents.md not found" in result.output
    assert (tmp_path / "builder" / "AGENTS.md").read_text(encoding="utf-8") == before


def test_agents_md_command_oob_success_message_names_the_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_oob_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(tmp_path / "config.yaml"), "agents_md"])

    assert result.exit_code == 0, result.output
    assert "every worktree" in result.output
    assert "docs/body_of_agents.md" in result.output
    assert "git diff" not in result.output


def test_prepare_agents_md_update_requires_metadata(tmp_path: Path) -> None:
    config = _load_default_config(tmp_path).model_copy(update={"metadata": None})
    with pytest.raises(WorkflowError, match="not inside a workflow project directory"):
        cm_cmd.prepare_agents_md_update(config)


def test_prepare_agents_md_update_oob_rejects_body_containing_markers(tmp_path: Path) -> None:
    config = _load_oob_config(tmp_path)
    body_path = tmp_path / "canon" / "docs" / "body_of_agents.md"
    body_path.write_text(f"{MARKER_BEGIN}\nstuff\n{MARKER_END}\n", encoding="utf-8")

    with pytest.raises(AgentsMdGenerationError, match="must not contain workflow markers"):
        cm_cmd.prepare_agents_md_update(config)


def test_generate_and_write_agents_md_oob_writes_four_identical_copies_and_one_hash(tmp_path: Path) -> None:
    config = _load_oob_config(tmp_path)

    written = cm_cmd.generate_and_write_agents_md(config)

    assert len(written) == 4
    contents = {path.read_text(encoding="utf-8") for path in written}
    assert len(contents) == 1
    assert (tmp_path / ".agents_md_hash").exists()


def test_generate_and_write_agents_md_oob_is_idempotent(tmp_path: Path) -> None:
    config = _load_oob_config(tmp_path)
    cm_cmd.generate_and_write_agents_md(config)
    update = cm_cmd.prepare_agents_md_update(config)
    assert update.agents_md_unchanged
    assert update.hash_unchanged


def test_prepare_agents_md_update_in_band_unaffected_by_oob_changes(tmp_path: Path) -> None:
    """Regression: in-band generation is byte-identical to before the oob refactor."""
    config = _load_default_config(tmp_path)
    (tmp_path / "canon").mkdir()

    update = cm_cmd.prepare_agents_md_update(config)

    assert update.write_targets == [tmp_path / "canon" / "AGENTS.md"]
    golden = _GOLDEN_PATH.read_text(encoding="utf-8")
    assert update.new_text == f"{MARKER_BEGIN}\n{golden}{MARKER_END}\n"


@pytest.mark.parametrize("oob", [False, True], ids=["in_band", "oob"])
def test_hash_covers_workflow_json_only_in_oob(tmp_path: Path, *, oob: bool) -> None:
    """In-band AGENTS.md doesn't render .workflow.json, so its hash must not depend on it."""
    config = _load_oob_config(tmp_path) if oob else _load_default_config(tmp_path)
    (tmp_path / "canon").mkdir(exist_ok=True)
    workflow_json = tmp_path / METADATA_FILENAME
    workflow_json.write_text('{"any": "content"}', encoding="utf-8")

    workflow_json_sha = parse_hash_file(cm_cmd.prepare_agents_md_update(config).new_hash).workflow_json_sha

    expected = hashlib.sha256(workflow_json.read_bytes() if oob else b"").hexdigest()
    assert workflow_json_sha == expected


def test_agents_md_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--config", str(config_path), "--dry-run", "agents_md"])

    assert result.exit_code == 0
    assert f"would write: {tmp_path / 'canon' / 'AGENTS.md'}" in result.output
    assert ".agents_md_hash" in result.output
    assert not (tmp_path / "canon" / "AGENTS.md").exists()
    assert not list(tmp_path.rglob(".agents_md_hash"))


# ---------------------------------------------------------------------------
# read-first instructions (in-band is pinned by the golden fixture)
# ---------------------------------------------------------------------------


def test_render_workflow_section_oob_reads_idea_and_index(tmp_path: Path) -> None:
    section = render_workflow_section(_load_oob_config(tmp_path))
    assert "reading `docs/plans/intent/001_idea.md`" in section
    assert "If `docs/plans/index_of_decisions.md` exists" in section
