"""Custom exceptions for the workflow tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow.models import Plan


class WorkflowError(Exception):
    """Raised for workflow operation errors (e.g. tmux session not found)."""


class AgentsMdGenerationError(WorkflowError):
    """Raised when AGENTS.md cannot be safely (re)generated."""


class PlanNotFoundError(Exception):
    """Raised when a fuzzy path reference does not match any plan."""

    def __init__(self, fuzzy_path: str, repos_searched: list[str]) -> None:
        """Store the fuzzy path and the repos that were searched."""
        self.fuzzy_path = fuzzy_path
        self.repos_searched = repos_searched
        super().__init__(f"No plan found for {fuzzy_path!r} in repos: {repos_searched}")


class AmbiguousPlanError(Exception):
    """Raised when a fuzzy path reference matches multiple plans without a clear winner."""

    def __init__(self, fuzzy_path: str, candidates: list[Plan]) -> None:
        """Store the fuzzy path and the list of ambiguous candidate plans."""
        self.fuzzy_path = fuzzy_path
        self.candidates = candidates
        super().__init__(f"Ambiguous reference {fuzzy_path!r}: {len(candidates)} candidates")


class PlanCollisionError(Exception):
    """Raised when two tickets share the same sequence number with different slugs."""

    def __init__(self, sequence_number: str, colliding: list[Plan]) -> None:
        """Store the colliding sequence number and the tickets that share it."""
        self.sequence_number = sequence_number
        self.colliding = colliding
        super().__init__(f"Sequence number collision on {sequence_number!r}: {[p.path for p in colliding]}")
