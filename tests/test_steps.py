"""Tests for workflow.steps."""

from __future__ import annotations

import pytest

from workflow.exceptions import WorkflowError
from workflow.steps import ResumePlan, require


def test_require_passes_when_condition_true() -> None:
    require(condition=True, message="unused")  # must not raise


def test_require_raises_with_message_when_condition_false() -> None:
    with pytest.raises(WorkflowError, match="boom"):
        require(condition=False, message="boom")


def test_resume_plan_should_run_reflects_satisfied_status() -> None:
    plan = ResumePlan(title="init")
    plan.satisfied("clone bare", "already a valid bare clone")
    plan.will_run("clone agents")

    assert plan.should_run("clone bare") is False
    assert plan.should_run("clone agents") is True


def test_resume_plan_should_run_unknown_step_raises() -> None:
    plan = ResumePlan(title="init")
    with pytest.raises(WorkflowError, match="no resume decision"):
        plan.should_run("nonexistent")


def test_resume_plan_render_prints_all_steps(capsys: pytest.CaptureFixture[str]) -> None:
    plan = ResumePlan(title="init")
    plan.satisfied("a", "already done")
    plan.will_run("b")
    plan.needs_continue("c", "rough state; pass --continue")
    plan.render()

    out = capsys.readouterr().out
    assert "Resume plan for `init`:" in out
    assert "[skip]" in out
    assert "a" in out
    assert "already done" in out
    assert "[run]" in out
    assert "b" in out
    assert "[!!]" in out
    assert "c" in out
    assert "--continue" in out


def test_abort_unless_continue_false_when_no_rough_steps() -> None:
    plan = ResumePlan(title="init")
    plan.satisfied("a", "done")
    plan.will_run("b")

    assert plan.abort_unless_continue(cont=False) is False


def test_abort_unless_continue_true_when_rough_and_not_continue(capsys: pytest.CaptureFixture[str]) -> None:
    plan = ResumePlan(title="init")
    plan.needs_continue("c", "rough")

    assert plan.abort_unless_continue(cont=False) is True
    assert "Resume plan for `init`:" in capsys.readouterr().out


def test_abort_unless_continue_false_when_rough_and_continue_given() -> None:
    plan = ResumePlan(title="init")
    plan.needs_continue("c", "rough")

    assert plan.abort_unless_continue(cont=True) is False
