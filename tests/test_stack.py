"""Tests for workflow.stack."""

from __future__ import annotations

import re
from pathlib import Path
from shutil import copyfile
from unittest.mock import MagicMock

import pytest

from workflow.config import WorkflowConfig, load_config
from workflow.exceptions import WorkflowError
from workflow.frontmatter import PlanFrontmatter, RepoBranch, render_frontmatter
from workflow.models import Plan, PlanState
from workflow.stack import (
    _MAX_STACK_DEPTH,
    branch_for_plan,
    resolve_stack,
    stack_child,
    stack_entries,
    tip_plan_for_branch,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _silence_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a dead libtmux server so resolve_plan's tmux source never touches a real session."""
    dead = MagicMock()
    dead.is_alive.return_value = False
    monkeypatch.setattr("workflow.plan_resolver.libtmux.Server", lambda: dead)


def _make_config(tmp_path: Path) -> WorkflowConfig:
    copyfile(Path(__file__).parents[1] / "docs" / "default_config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path / "config.yaml", project_dir=tmp_path)


def _make_plan(  # noqa: PLR0913 - test helper, kept explicit rather than bundling into a struct
    root: Path,
    repo: str,
    state: PlanState,
    filename: str,
    *,
    onto: str | None = None,
    branch: str | None = None,
    base: str | None = None,
) -> Plan:
    """Write a plan file (optionally with `onto`/`branches` frontmatter) and return its Plan."""
    d = root / repo / "docs" / "plans" / state.value
    d.mkdir(parents=True, exist_ok=True)
    body = f"# {filename}\n"
    if onto is not None or branch is not None:
        branches = {"docs": RepoBranch(branch=branch or "", base=base or "")} if branch is not None else {}
        fm = PlanFrontmatter(onto=onto, branches=branches)
        body = render_frontmatter(fm) + body
    (d / filename).write_text(body, encoding="utf-8")
    seq, slug = filename.removesuffix(".md").split("_", maxsplit=1)
    return Plan(
        sequence_number=[int(seq)],
        slug=slug,
        state=state,
        path=Path(repo) / "docs" / "plans" / state.value / filename,
    )


# ---------------------------------------------------------------------------
# branch_for_plan
# ---------------------------------------------------------------------------


def test_branch_for_plan_defaults_without_frontmatter(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")

    assert branch_for_plan(tmp_path, plan) == "feat/001_base"


def test_branch_for_plan_uses_recorded_branch(tmp_path: Path) -> None:
    plan = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "002_stacked.md",
        onto="001_base",
        branch="feat/001_base",
        base="abc1234",
    )

    assert branch_for_plan(tmp_path, plan) == "feat/001_base"


# ---------------------------------------------------------------------------
# resolve_stack
# ---------------------------------------------------------------------------


def test_resolve_stack_depth_one_unstacked(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    plan = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")

    stack = resolve_stack(tmp_path, plan, config)

    assert [p.name for p in stack] == ["001_base"]


def test_resolve_stack_depth_two(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")
    stacked = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "002_stacked.md",
        onto="001_base",
        branch="feat/001_base",
        base="abc1234",
    )

    stack = resolve_stack(tmp_path, stacked, config)

    assert [p.name for p in stack] == ["001_base", "002_stacked"]


def test_resolve_stack_depth_three(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")
    _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "002_middle.md",
        onto="001_base",
        branch="feat/001_base",
        base="b1",
    )
    tip = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "003_tip.md",
        onto="002_middle",
        branch="feat/001_base",
        base="c1",
    )

    stack = resolve_stack(tmp_path, tip, config)

    assert [p.name for p in stack] == ["001_base", "002_middle", "003_tip"]


def test_resolve_stack_cycle_raises(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    a = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_a.md", onto="002_b", branch="feat/001_a", base="x")
    _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "002_b.md", onto="001_a", branch="feat/001_a", base="y")

    with pytest.raises(WorkflowError, match="cycle"):
        resolve_stack(tmp_path, a, config)


def test_resolve_stack_unresolvable_onto_raises(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    plan = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "002_stacked.md",
        onto="999_missing",
        branch="feat/999_missing",
        base="abc",
    )

    with pytest.raises(WorkflowError, match="999_missing"):
        resolve_stack(tmp_path, plan, config)


def test_resolve_stack_depth_cap_raises(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    chain_length = _MAX_STACK_DEPTH + 5
    _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_p1.md")
    for i in range(2, chain_length + 1):
        onto_name = f"{i - 1:03d}_p{i - 1}"
        _make_plan(
            tmp_path,
            "canon",
            PlanState.IMPLEMENTING,
            f"{i:03d}_p{i}.md",
            onto=onto_name,
            branch="feat/001_p1",
            base=f"c{i}",
        )
    tip = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        f"{chain_length + 1:03d}_tip.md",
        onto=f"{chain_length:03d}_p{chain_length}",
        branch="feat/001_p1",
        base="ctip",
    )

    with pytest.raises(WorkflowError, match="depth"):
        resolve_stack(tmp_path, tip, config)


# ---------------------------------------------------------------------------
# stack_child
# ---------------------------------------------------------------------------


def test_stack_child_none_when_unstacked(tmp_path: Path) -> None:
    base = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")

    assert stack_child(tmp_path, base) is None


def test_stack_child_ignores_rejected(tmp_path: Path) -> None:
    base = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")
    _make_plan(
        tmp_path,
        "canon",
        PlanState.REJECTED,
        "002_stacked.md",
        onto="001_base",
        branch="feat/001_base",
        base="abc",
    )

    assert stack_child(tmp_path, base) is None


def test_stack_child_returns_active_child(tmp_path: Path) -> None:
    base = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")
    child = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "002_stacked.md",
        onto="001_base",
        branch="feat/001_base",
        base="abc",
    )

    result = stack_child(tmp_path, base)

    assert result is not None
    assert result.name == child.name


# ---------------------------------------------------------------------------
# tip_plan_for_branch
# ---------------------------------------------------------------------------


def test_tip_plan_for_branch_unstacked(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")

    result = tip_plan_for_branch(tmp_path, "feat/001_base")

    assert result is not None
    assert result.name == plan.name


def test_tip_plan_for_branch_returns_the_tip_of_a_stack(tmp_path: Path) -> None:
    _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")
    tip = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "002_stacked.md",
        onto="001_base",
        branch="feat/001_base",
        base="abc",
    )

    result = tip_plan_for_branch(tmp_path, "feat/001_base")

    assert result is not None
    assert result.name == tip.name


def test_tip_plan_for_branch_none_when_no_match(tmp_path: Path) -> None:
    _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")

    assert tip_plan_for_branch(tmp_path, "feat/999_missing") is None


# ---------------------------------------------------------------------------
# stack_entries
# ---------------------------------------------------------------------------


def test_stack_entries_empty_when_unstacked(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    plan = _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")

    assert stack_entries(tmp_path, plan, config) == []


def test_stack_entries_commit_ranges_for_three_plan_stack(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    _make_plan(tmp_path, "canon", PlanState.IMPLEMENTING, "001_base.md")
    _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "002_middle.md",
        onto="001_base",
        branch="feat/001_base",
        base="b1",
    )
    tip = _make_plan(
        tmp_path,
        "canon",
        PlanState.IMPLEMENTING,
        "003_tip.md",
        onto="002_middle",
        branch="feat/001_base",
        base="c1",
    )

    entries = stack_entries(tmp_path, tip, config)

    assert [(e.name, e.commit_range) for e in entries] == [
        ("001_base", "origin/main..b1"),
        ("002_middle", "b1..c1"),
        ("003_tip", "c1..HEAD"),
    ]


# ---------------------------------------------------------------------------
# Regression: the branch-name literal belongs in exactly one place.
# ---------------------------------------------------------------------------


def test_no_feat_branch_literal_outside_stack_module() -> None:
    """`branch_for_plan` is THE branch-name helper; no other module may hardcode `feat/`."""
    package_root = Path(__file__).parents[1] / "workflow"
    pattern = re.compile(r"""['"]feat/""")
    offending: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name == "stack.py":
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offending.append(str(path.relative_to(package_root)))

    assert offending == []
