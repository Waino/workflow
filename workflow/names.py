"""Shared workflow identifiers and command-name helpers."""

from __future__ import annotations

from enum import StrEnum

DOCS_REPO = "docs"
"""Frontmatter key for the docs repo's branch entry (the only key used in in-band mode)."""


class WorktreeName(StrEnum):
    """The four agent-side worktrees: fixed, built-in clones, not to be confused with git repos."""

    canon = "canon"
    planner = "planner"
    builder = "builder"
    reviewer = "reviewer"


class AgentName(StrEnum):
    """Built-in workflow agent roles."""

    planner = "planner"
    builder = "builder"
    reviewer = "reviewer"


def command_cli_name(name: str) -> str:
    """Convert a human-readable command name to the CLI form."""
    return name.replace(" ", "-")
