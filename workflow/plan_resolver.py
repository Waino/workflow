"""Plan file path resolution helpers."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - used at runtime in path operations

from workflow.exceptions import PlanCollisionError
from workflow.models import Plan, PlanState, Review, ReviewSubState

# ISO 8601 timestamp as used in critique review filenames: 2026-03-18T14:30:00Z
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Abbreviated git commit hash: 4-40 lowercase hex chars
_COMMIT_RE = re.compile(r"^[0-9a-f]{4,40}$")

# State progression for disambiguation/deduplication (review and rejected are excluded)
_STATE_PROGRESSION: tuple[PlanState, ...] = (
    PlanState.INTENT,
    PlanState.PLANNING,
    PlanState.TODO,
    PlanState.IMPLEMENTING,
    PlanState.CHECKING,
    PlanState.DONE,
)

_REPOS = ("master", "planner", "builder", "reviewer")


def _state_rank(state: PlanState) -> int:
    """Return the 0-based progression rank of a state, or -1 if not ranked."""
    try:
        return _STATE_PROGRESSION.index(state)
    except ValueError:
        return -1


def parse_plan_filename(filename: str) -> tuple[list[int], str]:
    """Parse '002_pydantic_models' into ([2], 'pydantic_models').

    Splits on the first '_' only (maxsplit=1), so slugs containing underscores
    are preserved correctly: '012.1_foo_bar' -> ([12, 1], 'foo_bar').
    The leading numeric component may be hierarchical ('012.1') and is split on '.'.
    """
    num_part, slug = filename.split("_", maxsplit=1)
    sequence_number = [int(n) for n in num_part.split(".")]
    return sequence_number, slug


def parse_review_filename(
    filename: str, sub_state: ReviewSubState,
) -> tuple[list[int], str, datetime | None, str | None]:
    """Parse a review filename into (sequence_number, slug, review_timestamp, review_commit).

    Critique ('002_pydantic_models_2026-03-18T14:30:00Z'):
        Parse the ISO 8601 timestamp suffix from the right.
        Returns: ([2], 'pydantic_models', datetime(...), None)

    PR ('002_pydantic_models_0e6baa9'):
        Parse the commit hash suffix (hex string) from the right.
        Returns: ([2], 'pydantic_models', None, '0e6baa9')

    Full reviews are not parsed — callers must skip them.
    Parses from the right to correctly handle slugs containing underscores.
    """
    if sub_state == ReviewSubState.FULL:
        msg = "Full reviews are not parsed; callers should skip them"
        raise ValueError(msg)

    base, suffix = filename.rsplit("_", maxsplit=1)

    if sub_state == ReviewSubState.CRITIQUE:
        if not _TIMESTAMP_RE.match(suffix):
            msg = f"Invalid critique timestamp suffix: {suffix!r}"
            raise ValueError(msg)
        ts = datetime.strptime(suffix, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        seq, slug = parse_plan_filename(base)
        return seq, slug, ts, None

    # PR
    if not _COMMIT_RE.match(suffix):
        msg = f"Invalid PR commit hash suffix: {suffix!r}"
        raise ValueError(msg)
    seq, slug = parse_plan_filename(base)
    return seq, slug, None, suffix


def _check_collisions(plans: list[Plan]) -> None:
    """Raise PlanCollisionError if two non-intent, non-review plans share a sequence number."""
    groups: dict[str, list[Plan]] = defaultdict(list)
    for plan in plans:
        if plan.state not in (PlanState.INTENT, PlanState.REVIEW):
            groups[plan.sequence_number_str].append(plan)
    for seq_str, group in groups.items():
        if len({p.slug for p in group}) > 1:
            raise PlanCollisionError(seq_str, group)


def find_all_plans_in_repo(project_dir: Path, repo: str = "master") -> list[Plan]:
    """Scan docs/plans/ in a single repo and return all plans found.

    Raises PlanCollisionError if two non-intent plans with the same sequence number
    are found. Skips docs/plans/reviews/full/ — full review documents are write-only
    output and are never resolved by fuzzy path.
    """
    plans_dir = project_dir / repo / "docs" / "plans"
    plans: list[Plan] = []

    for state in PlanState:
        if state == PlanState.REVIEW:
            for sub_state in (ReviewSubState.CRITIQUE, ReviewSubState.PR):
                sub_dir = plans_dir / "reviews" / sub_state.value
                if not sub_dir.is_dir():
                    continue
                for md_file in sorted(sub_dir.glob("*.md")):
                    try:
                        seq, slug, ts, commit = parse_review_filename(md_file.stem, sub_state)
                    except ValueError:
                        continue
                    plans.append(
                        Review(
                            sequence_number=seq,
                            slug=slug,
                            path=md_file.resolve(),
                            review_sub_state=sub_state,
                            review_timestamp=ts,
                            review_commit=commit,
                        ),
                    )
        else:
            state_dir = plans_dir / state.value
            if not state_dir.is_dir():
                continue
            for md_file in sorted(state_dir.glob("*.md")):
                try:
                    seq, slug = parse_plan_filename(md_file.stem)
                except ValueError:
                    continue
                plans.append(Plan(sequence_number=seq, slug=slug, state=state, path=md_file.resolve()))

    _check_collisions(plans)
    return plans


def find_all_plans(project_dir: Path) -> list[Plan]:
    """Scan all repos and return a merged, deduplicated list of plans.

    When the same plan (same sequence_number_str + slug) appears in multiple repos,
    the copy with the most progressed state is kept.
    Reviews are always included without deduplication (each has a unique name).
    """
    merged: dict[str, Plan] = {}

    for repo in _REPOS:
        if not (project_dir / repo).is_dir():
            continue
        for plan in find_all_plans_in_repo(project_dir, repo):
            if isinstance(plan, Review):
                key = f"review:{plan.name}"
            else:
                key = f"{plan.sequence_number_str}:{plan.slug}"
            existing = merged.get(key)
            if existing is None or _state_rank(plan.state) > _state_rank(existing.state):
                merged[key] = plan

    return list(merged.values())
