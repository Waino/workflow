"""Plan file path resolution helpers."""

from __future__ import annotations

import re
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

from workflow.exceptions import AmbiguousPlanError, PlanCollisionError, PlanNotFoundError
from workflow.models import Plan, PlanState, Review, ReviewSubState
from workflow.names import RepoName

# Timestamp as used in critique review filenames: 2026-03-18T14-30-00Z (dashes, no colons)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")

# Abbreviated git commit hash: 4-40 lowercase hex chars
_COMMIT_RE = re.compile(r"^[0-9a-f]{4,40}$")

# State progression for disambiguation/deduplication (REVIEW is excluded; REJECTED ranks highest)
_STATE_PROGRESSION: tuple[PlanState, ...] = (
    PlanState.INTENT,
    PlanState.PLANNING,
    PlanState.TODO,
    PlanState.IMPLEMENTING,
    PlanState.CHECKING,
    PlanState.DONE,
    PlanState.REJECTED,
)

# Minimum path depths for absolute-path parsing
_MIN_PLAN_PARTS = 5  # (repo, docs, plans, state, filename)
_MIN_REVIEW_PARTS = 6  # (repo, docs, plans, reviews, sub_state, filename)


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
    filename: str,
    sub_state: ReviewSubState,
) -> tuple[list[int], str, datetime | None, str | None]:
    """Parse a review filename into (sequence_number, slug, review_timestamp, review_commit).

    Critique ('002_pydantic_models_2026-03-18T14-30-00Z'):
        Parse the timestamp suffix (dashes, no colons) from the right.
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
        ts = datetime.strptime(suffix, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
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


def find_all_plans_in_repo(project_dir: Path, repo: str = RepoName.master) -> list[Plan]:
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
                            path=md_file.relative_to(project_dir),
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
                plans.append(Plan(sequence_number=seq, slug=slug, state=state, path=md_file.relative_to(project_dir)))

    _check_collisions(plans)
    return plans


def find_all_plans(project_dir: Path) -> list[Plan]:
    """Scan all repos and return a merged, deduplicated list of plans.

    When the same plan (same sequence_number_str + slug) appears in multiple repos,
    the copy with the most progressed state is kept.
    State ordering: intent < planning < todo < implementing < checking < done < rejected.
    REJECTED ranks highest, so a rejected copy always wins regardless of other repos.
    REVIEW is not ranked and is never deduplicated (each review has a unique name).
    """
    merged: dict[str, Plan] = {}

    for repo in RepoName:
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


def _resolve_absolute_review(fuzzy_path: str, path: Path, sub_state_str: str) -> Review:
    """Parse a Review from a validated absolute path inside reviews/{sub_state}/."""
    try:
        sub_state = ReviewSubState(sub_state_str)
    except ValueError:
        raise PlanNotFoundError(fuzzy_path, []) from None
    if sub_state == ReviewSubState.FULL:
        raise PlanNotFoundError(fuzzy_path, [])
    try:
        seq, slug, ts, commit = parse_review_filename(path.stem, sub_state)
    except ValueError:
        raise PlanNotFoundError(fuzzy_path, []) from None
    return Review(
        sequence_number=seq,
        slug=slug,
        path=path,
        review_sub_state=sub_state,
        review_timestamp=ts,
        review_commit=commit,
    )


def _parse_absolute_parts(fuzzy_path: str, path: Path, parts: tuple[str, ...]) -> Plan:
    """Construct a Plan from the parts of a validated absolute path."""
    if len(parts) < _MIN_PLAN_PARTS or parts[1] != "docs" or parts[2] != "plans":
        raise PlanNotFoundError(fuzzy_path, [])
    if parts[3] == "reviews":
        if len(parts) < _MIN_REVIEW_PARTS:
            raise PlanNotFoundError(fuzzy_path, [])
        return _resolve_absolute_review(fuzzy_path, path, parts[4])
    try:
        state = PlanState(parts[3])
    except ValueError:
        raise PlanNotFoundError(fuzzy_path, []) from None
    if state == PlanState.REVIEW:
        raise PlanNotFoundError(fuzzy_path, [])
    try:
        seq, slug = parse_plan_filename(path.stem)
    except ValueError:
        raise PlanNotFoundError(fuzzy_path, []) from None
    return Plan(sequence_number=seq, slug=slug, state=state, path=path)


def _resolve_absolute(fuzzy_path: str, project_dir: Path) -> Plan:
    """Resolve an absolute path to a Plan directly, without a directory scan."""
    path = Path(fuzzy_path).resolve()
    if not path.exists():
        raise PlanNotFoundError(fuzzy_path, [])
    try:
        relative = path.relative_to(project_dir.resolve())
    except ValueError:
        raise PlanNotFoundError(fuzzy_path, []) from None
    return _parse_absolute_parts(fuzzy_path, relative, relative.parts)


def _disambiguate(fuzzy_path: str, matches: list[Plan], repos_searched: list[str]) -> Plan:
    """Return the single unambiguous match, or raise an appropriate error.

    When the only ambiguity is intent vs. exactly one non-intent plan,
    the non-intent plan is preferred (it is the canonical, progressed document).
    """
    if not matches:
        raise PlanNotFoundError(fuzzy_path, repos_searched)
    if len(matches) == 1:
        return matches[0]
    non_intent = [p for p in matches if p.state != PlanState.INTENT]
    if len(non_intent) == 1:
        return non_intent[0]
    raise AmbiguousPlanError(fuzzy_path, matches)


def _extract_components(
    parts: list[str],
) -> tuple[RepoName | None, PlanState | None, list[int] | None, str | None]:
    """Parse normalized path parts into (repo_hint, state_hint, seq_hint, slug_hint)."""
    state_values = {s.value: s for s in PlanState}
    repo_hint: RepoName | None = None
    state_hint: PlanState | None = None
    seq_hint: list[int] | None = None
    slug_hint: str | None = None

    idx = 0
    if idx < len(parts):
        with suppress(ValueError):
            repo_hint = RepoName(parts[idx])
            idx += 1

    if idx + 1 < len(parts) and parts[idx] == "docs" and parts[idx + 1] == "plans":
        idx += 2

    if idx < len(parts) and parts[idx] in state_values:
        state_hint = state_values[parts[idx]]
        idx += 1

    if idx < len(parts):
        name_part = parts[idx]
        if "_" in name_part:
            with suppress(ValueError):
                seq_hint, slug_hint = parse_plan_filename(name_part)
        else:
            with suppress(ValueError):
                seq_hint = [int(n) for n in name_part.split(".")]

    return repo_hint, state_hint, seq_hint, slug_hint


def resolve_plan(
    fuzzy_path: str,
    project_dir: Path,
    expected_states: Collection[PlanState] | None = None,
    repo: str | None = None,
) -> Plan:
    """Resolve a fuzzy path reference to a canonical Plan.

    Args:
        fuzzy_path: The human-provided path reference. Supported forms (most
            to least specific): absolute path, repo/docs/plans/state/name,
            docs/plans/state/name, state/name, name, state/seq, seq.
        project_dir: Root of the project (contains master/, planner/, etc.).
        expected_states: Constrain which states are valid; used to disambiguate
            when multiple matches exist.
        repo: Which repo to search. Defaults to 'master'. A repo prefix
            embedded in fuzzy_path takes precedence over this parameter.

    Returns:
        A Plan object with the resolved canonical path.

    Raises:
        PlanNotFoundError: No matching plan found.
        AmbiguousPlanError: Multiple matches and no clear winner.
        PlanCollisionError: Two non-intent plans share the same sequence number.
    """
    # Step 1: Absolute path fast-path
    if Path(fuzzy_path).is_absolute():
        return _resolve_absolute(fuzzy_path, project_dir)

    # Step 2: Normalize — strip .md, split on forward slash, drop empty parts
    parts = [p for p in fuzzy_path.removesuffix(".md").replace("\\", "/").split("/") if p]

    # Steps 3: Extract repo/state/seq/slug hints from the path components
    repo_hint, state_hint, seq_hint, slug_hint = _extract_components(parts)

    # Step 4: Search one repo
    target_repo = repo_hint or repo or RepoName.master
    plans = find_all_plans_in_repo(project_dir, target_repo)

    # Step 5: Filter by extracted components
    matches: list[Plan] = plans
    if seq_hint is not None:
        matches = [p for p in matches if p.sequence_number == seq_hint]
    if slug_hint is not None:
        matches = [p for p in matches if p.slug == slug_hint]
    if state_hint is not None:
        matches = [p for p in matches if p.state == state_hint]

    # Step 6: Filter by expected_states
    if expected_states:
        matches = [p for p in matches if p.state in expected_states]

    # Step 7: Disambiguate
    return _disambiguate(fuzzy_path, matches, [target_repo])
