"""Shared precondition/resume vocabulary for script commands.

Deliberately lightweight: helpers plus a small two-pass collector, not a
framework that owns execution. Commands classify every step's guard up front
(read-only), render the resulting preview, then run only the steps that are
not already satisfied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from workflow.exceptions import WorkflowError
from workflow.shell import console


def require(condition: bool, message: str) -> None:  # noqa: FBT001
    """Precondition guard: raise :class:`WorkflowError` with *message* when *condition* is false."""
    if not condition:
        raise WorkflowError(message)


class StepStatus(enum.Enum):
    """Classification of a resumable step, decided before any mutation runs."""

    SATISFIED = "satisfied"
    """Postcondition already holds: skip."""

    WILL_RUN = "will_run"
    """Not yet done: run normally."""

    NEEDS_CONTINUE = "needs_continue"
    """Rough/partial state detected: requires --continue to proceed."""


_STATUS_MARKER = {
    StepStatus.SATISFIED: "[skip]",
    StepStatus.WILL_RUN: "[run] ",
    StepStatus.NEEDS_CONTINUE: "[!!]  ",
}


@dataclass(frozen=True)
class _StepDecision:
    name: str
    status: StepStatus
    reason: str = ""


@dataclass
class ResumePlan:
    """Collects step decisions, renders the preview, and gates on --continue.

    Two-pass usage: evaluate every guard (read-only) to classify each step, then
    :meth:`render` the preview, then :meth:`abort_unless_continue`, then run only
    the steps whose :meth:`should_run` is true.
    """

    title: str
    _decisions: list[_StepDecision] = field(default_factory=list)

    def satisfied(self, name: str, reason: str) -> None:
        """Record that *name*'s postcondition already holds."""
        self._decisions.append(_StepDecision(name, StepStatus.SATISFIED, reason))

    def will_run(self, name: str) -> None:
        """Record that *name* will run normally."""
        self._decisions.append(_StepDecision(name, StepStatus.WILL_RUN))

    def needs_continue(self, name: str, reason: str) -> None:
        """Record that *name* is in a rough state that requires --continue."""
        self._decisions.append(_StepDecision(name, StepStatus.NEEDS_CONTINUE, reason))

    def render(self) -> None:
        """Print the preview: one ``[skip]``/``[run]``/``[!!]`` line per step.

        Titled "Resume plan" only when a step is already done or needs ``--continue``.
        """
        resuming = any(decision.status != StepStatus.WILL_RUN for decision in self._decisions)
        console.print(f"{'Resume' if resuming else 'Execution'} plan for `{self.title}`:", markup=False)
        for decision in self._decisions:
            marker = _STATUS_MARKER[decision.status]
            line = f"  {marker} {decision.name}"
            if decision.reason:
                line += f"    {decision.reason}"
            console.print(line, markup=False)

    def should_run(self, name: str) -> bool:
        """Return whether the step *name* should run (i.e. is not SATISFIED)."""
        for decision in self._decisions:
            if decision.name == name:
                return decision.status != StepStatus.SATISFIED
        msg = f"no resume decision recorded for step {name!r}"
        raise WorkflowError(msg)

    def abort_unless_continue(self, *, cont: bool) -> bool:
        """Return True (caller must exit) if a NEEDS_CONTINUE step exists and cont is False; callers render first."""
        has_rough = any(decision.status == StepStatus.NEEDS_CONTINUE for decision in self._decisions)
        return has_rough and not cont
