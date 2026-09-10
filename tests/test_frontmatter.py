"""Tests for workflow.frontmatter."""

from pathlib import Path

import pytest

from workflow.exceptions import WorkflowError
from workflow.frontmatter import (
    PlanFrontmatter,
    RepoBranch,
    parse_frontmatter,
    render_frontmatter,
    strip_frontmatter,
    write_frontmatter,
)

_BODY = "# 002: Foo\n\nSome plan body.\n"


def test_parse_frontmatter_returns_none_when_absent() -> None:
    assert parse_frontmatter(_BODY) is None


def test_strip_frontmatter_returns_full_text_as_body_when_absent() -> None:
    fm, body = strip_frontmatter(_BODY)
    assert fm is None
    assert body == _BODY


def test_round_trip_parse_and_render() -> None:
    fm = PlanFrontmatter(onto="001_base", branches={"docs": RepoBranch(branch="feat/001_base", base="abc1234")})
    text = render_frontmatter(fm) + _BODY

    parsed, body = strip_frontmatter(text)

    assert parsed == fm
    assert body == _BODY


def test_render_frontmatter_uses_fences_and_yaml() -> None:
    fm = PlanFrontmatter(onto="001_base", branches={"docs": RepoBranch(branch="feat/001_base", base="abc1234")})

    rendered = render_frontmatter(fm)

    assert rendered.startswith("---\n")
    assert rendered.count("---\n") == 2
    assert "onto: 001_base" in rendered
    assert "branch: feat/001_base" in rendered


def test_parse_frontmatter_malformed_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "002_foo.md"
    text = "---\nonto: [unterminated\n---\nbody\n"

    with pytest.raises(WorkflowError, match="malformed frontmatter"):
        parse_frontmatter(text, path=path)


def test_parse_frontmatter_missing_closing_fence_raises() -> None:
    text = "---\nonto: 001_base\nno closing fence here\n"

    with pytest.raises(WorkflowError, match="no closing '---' fence"):
        parse_frontmatter(text)


def test_parse_frontmatter_invalid_model_raises() -> None:
    text = "---\nbranches: not-a-dict\n---\nbody\n"

    with pytest.raises(WorkflowError, match="malformed frontmatter"):
        parse_frontmatter(text)


def test_parse_frontmatter_preserves_unknown_extra_key() -> None:
    text = "---\nonto: 001_base\nfuture_key: some_value\n---\nbody\n"

    fm = parse_frontmatter(text)

    assert fm is not None
    assert fm.onto == "001_base"
    assert fm.model_extra == {"future_key": "some_value"}


def test_write_frontmatter_inserts_block_and_preserves_body(tmp_path: Path) -> None:
    path = tmp_path / "002_foo.md"
    path.write_text(_BODY, encoding="utf-8")
    fm = PlanFrontmatter(onto="001_base", branches={"docs": RepoBranch(branch="feat/001_base", base="abc1234")})

    write_frontmatter(path, fm)

    text = path.read_text(encoding="utf-8")
    parsed, body = strip_frontmatter(text)
    assert parsed == fm
    assert body == _BODY


def test_write_frontmatter_replaces_existing_block_byte_identical_body(tmp_path: Path) -> None:
    path = tmp_path / "002_foo.md"
    old_fm = PlanFrontmatter(onto="001_base", branches={"docs": RepoBranch(branch="feat/001_base", base="abc1234")})
    path.write_text(render_frontmatter(old_fm) + _BODY, encoding="utf-8")
    new_fm = PlanFrontmatter(onto="001_base", branches={"docs": RepoBranch(branch="feat/001_base", base="def5678")})

    write_frontmatter(path, new_fm)

    text = path.read_text(encoding="utf-8")
    parsed, body = strip_frontmatter(text)
    assert parsed == new_fm
    assert body == _BODY
