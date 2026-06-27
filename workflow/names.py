"""Shared workflow identifiers and command-name helpers."""

from __future__ import annotations

from enum import StrEnum


class RepoName(StrEnum):
    """Workflow repos with fixed built-in responsibilities."""

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
