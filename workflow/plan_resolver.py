"""Plan file path resolution helpers."""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import libtmux
import libtmux.exc
from pydantic import BaseModel, ConfigDict
from rich.console import Group
from rich.text import Text

from workflow.audit import build_audit_command_repo_state, read_audit_log
from workflow.config import PromptCommand, ScriptCommand, WorkflowConfig
from workflow.exceptions import AmbiguousPlanError, PlanCollisionError, PlanNotFoundError, WorkflowError
from workflow.models import Plan, PlanState, Review, ReviewSubState
from workflow.names import RepoName

# Color styles for the SourcesDisagreeError rendering.
_REPO_STYLES: dict[RepoName, str] = {
    RepoName.master: "steel_blue3",
    RepoName.planner: "turquoise4",
    RepoName.builder: "gold3",
    RepoName.reviewer: "light_salmon3",
}
_STATE_STYLES: dict[PlanState, str] = {
    PlanState.INTENT: "steel_blue3",
    PlanState.PLANNING: "turquoise4",
    PlanState.TODO: "spring_green3",
    PlanState.IMPLEMENTING: "gold3",
    PlanState.CHECKING: "light_salmon3",
    PlanState.DONE: "grey70",
    PlanState.REJECTED: "grey30",
    PlanState.REVIEW: "deep_pink3",
}
_SOURCE_STYLES: dict[str, str] = {
    "subcommand": "grey70",
    "git": "aquamarine1",
    "audit": "violet",
    "tmux": "steel_blue3",
    "path": "light_salmon3",
}

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

# Minimum path depths and field counts for git/audit parsing
_MIN_RELATIVE_PLAN_PARTS = 4  # (docs, plans, state, filename)
_GIT_HEADER_FIELDS = 2  # "<commit_hash> <timestamp>"
_GIT_NAME_STATUS_FIELDS = 2  # "<status>\t<path>"
_GIT_RENAME_FIELDS = 3  # "<status>\t<old>\t<new>"


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
    """Raise PlanCollisionError if two tickets share a sequence number."""
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


def _top_level_number_from_name(plan_name: str) -> int | None:
    """Extract the top-level sequence number from a plan name like '003_foo'.

    Returns ``None`` when the name does not match the ``NNN_slug`` pattern.
    """
    candidate = Path(plan_name).stem.split("/")[-1]
    if "_" not in candidate:
        return None
    try:
        sequence_number, _slug = parse_plan_filename(candidate)
    except ValueError:
        return None
    return sequence_number[0]


def next_intent_number(project_dir: Path) -> int:
    """Return the lowest positive integer not yet reserved by any plan or audit log entry.

    Scans all repos for existing plans and the audit log for any previously
    created plan names, then returns the first gap starting from 1.
    """
    reserved = {plan.sequence_number[0] for plan in find_all_plans(project_dir)}
    for entry in read_audit_log(project_dir):
        for plan_name in [entry.input_plan, *entry.output_plans]:
            if plan_name is None:
                continue
            top_level = _top_level_number_from_name(plan_name)
            if top_level is not None:
                reserved.add(top_level)
    number = 1
    while number in reserved:
        number += 1
    return number


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


# ---------------------------------------------------------------------------
# Multi-source plan resolution
# ---------------------------------------------------------------------------

_T = TypeVar("_T")

_ALL_REPOS: frozenset[RepoName] = frozenset(RepoName)
_ALL_STATES: frozenset[PlanState] = frozenset(s for s in PlanState if s != PlanState.REVIEW)


class _Constraint(BaseModel):
    """One source's contribution to the resolver's repo and state constraints.

    ``None`` on a dimension means the source is silent on that dimension and
    contributes no constraint there.
    """

    model_config = ConfigDict(frozen=True)

    repos: frozenset[RepoName] | None = None
    states: frozenset[PlanState] | None = None


class _PathHints(BaseModel):
    """Parsed hints from the user-supplied fuzzy path."""

    model_config = ConfigDict(frozen=True)

    constraint: _Constraint
    seq: list[int] | None
    slug: str | None

    def filename(self) -> str:
        """Reconstruct what is known of the filename for SourcesDisagreeError."""
        if self.seq is None:
            return "..."
        parts = [str(n) for n in self.seq]
        parts[0] = parts[0].zfill(3)
        seq_str = ".".join(parts)
        if self.slug is None:
            return seq_str
        return f"{seq_str}_{self.slug}.md"


class _GitTouch(BaseModel):
    """Newest git log touch of a plan file: commit timestamp and resulting state."""

    model_config = ConfigDict(frozen=True)

    timestamp: int
    state: PlanState


class _BestGitTouch(BaseModel):
    """Like _GitTouch but also records which repo clone the commit was found in."""

    model_config = ConfigDict(frozen=True)

    timestamp: int
    state: PlanState
    repo: RepoName


class _PathComponents(BaseModel):
    """Raw components extracted from a fuzzy path string by _extract_components."""

    model_config = ConfigDict(frozen=True)

    repo: RepoName | None = None
    state: PlanState | None = None
    seq: list[int] | None = None
    slug: str | None = None


class _Source(BaseModel):
    """A named constraint source contributing to plan resolution."""

    model_config = ConfigDict(frozen=True)

    label: str
    constraint: _Constraint


class _RepoStatePair(BaseModel):
    """A (repo, state) pair used as a hashable key in _union_report's contribution map."""

    model_config = ConfigDict(frozen=True)

    repo: RepoName
    state: PlanState


def _seq_to_str(seq: list[int]) -> str:
    """Format a sequence number list as the on-disk string form."""
    parts = [str(n) for n in seq]
    parts[0] = parts[0].zfill(3)
    return ".".join(parts)


def _intersect(values: list[frozenset[_T] | None]) -> frozenset[_T] | None:
    """Intersect non-None frozensets; return None if all are silent."""
    non_silent = [v for v in values if v is not None]
    if not non_silent:
        return None
    result = non_silent[0]
    for v in non_silent[1:]:
        result = result & v
    return result


def _extract_components(
    parts: list[str],
) -> _PathComponents:
    """Parse normalized path parts into a _PathComponents with repo/state/seq/slug hints."""
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

    return _PathComponents(repo=repo_hint, state=state_hint, seq=seq_hint, slug=slug_hint)


def _from_fuzzy_path(fuzzy_path: str) -> _PathHints:
    """Parse the fuzzy path and return its constraint plus seq/slug hints."""
    parts = [p for p in fuzzy_path.removesuffix(".md").replace("\\", "/").split("/") if p]
    components = _extract_components(parts)
    repos = frozenset({components.repo}) if components.repo is not None else None
    states = frozenset({components.state}) if components.state is not None else None
    return _PathHints(constraint=_Constraint(repos=repos, states=states), seq=components.seq, slug=components.slug)


def _agent_repo(agent: str | None, default: RepoName) -> RepoName:
    """Resolve an agent name string into a RepoName, falling back to *default*."""
    if agent is None:
        return default
    try:
        return RepoName(agent)
    except ValueError:
        return default


def _accumulate_subcommand_contribution(
    cmd: PromptCommand | ScriptCommand,
    state: PlanState,
    state_config: object,
    repos: set[RepoName],
    states: set[PlanState],
) -> None:
    """Add one command's contribution to the running repos/states sets."""
    agent = getattr(state_config, "agent", None)
    if isinstance(cmd, PromptCommand):
        # Prompts contribute no state constraint — agents may be asked about
        # plans in any state (UX goal: do not enforce the workflow rigidly).
        if agent is not None:
            with suppress(ValueError):
                repos.add(RepoName(agent))
        return
    states.add(state)
    # Plans may already have been transitioned by the previous step's agent.
    if cmd.transition is not None:
        states.add(cmd.transition)
    states.update(cmd.also_valid_in_states)
    if cmd.repo_override is not None:
        repos.add(cmd.repo_override)
    else:
        repos.add(_agent_repo(agent, RepoName.master))


def _from_subcommand(config: WorkflowConfig, command_name: str | None) -> _Constraint:
    """Derive a constraint from a CLI subcommand's appearances in the config.

    Prompt commands contribute the agent's repo (master is implicitly excluded
    because prompts dispatch to agents only). Script commands contribute their
    ``repo_override`` if set, otherwise the state's agent or master. Scripts
    also contribute the states they are defined in (plus their transition target).
    """
    if command_name is None:
        return _Constraint()
    repos: set[RepoName] = set()
    states: set[PlanState] = set()
    found = False
    for state, state_config in config.states.items():
        for cmd in state_config.commands:
            if cmd.name != command_name or not isinstance(cmd, (PromptCommand, ScriptCommand)):
                continue
            found = True
            _accumulate_subcommand_contribution(cmd, state, state_config, repos, states)
            # Contribute the repo for each state listed in also_valid_in_states.
            if isinstance(cmd, ScriptCommand):
                for extra_state in cmd.also_valid_in_states:
                    extra_cfg = config.states.get(extra_state)
                    extra_agent = getattr(extra_cfg, "agent", None) if extra_cfg is not None else None
                    repos.add(_agent_repo(extra_agent, RepoName.master))
    if not found:
        return _Constraint()
    return _Constraint(
        repos=frozenset(repos) if repos else None,
        states=frozenset(states) if states else None,
    )


def _parse_path_to_state(path_str: str) -> PlanState | None:
    """Map a `docs/plans/<state>/<file>` path to its PlanState; reviews return None."""
    parts = Path(path_str).parts
    if len(parts) < _MIN_RELATIVE_PLAN_PARTS or parts[0] != "docs" or parts[1] != "plans":
        return None
    if parts[2] == "reviews":
        return None
    try:
        return PlanState(parts[2])
    except ValueError:
        return None


def _git_log_chosen_path(lines: list[str]) -> str | None:
    """From the `--name-status` lines after the header, pick the path of interest."""
    adds: list[str] = []
    deletes: list[str] = []
    chosen: str | None = None
    for line in lines:
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= _GIT_RENAME_FIELDS:
            return parts[2]
        if status in ("A", "M") and len(parts) >= _GIT_NAME_STATUS_FIELDS:
            chosen = parts[1]
            if status == "A":
                adds.append(parts[1])
        elif status == "D" and len(parts) >= _GIT_NAME_STATUS_FIELDS:
            deletes.append(parts[1])
    if chosen is not None:
        return chosen
    # Same-commit D + A with matching basename → treat as rename to the A path.
    for a in adds:
        if any(Path(a).name == Path(d).name for d in deletes):
            return a
    return None


def _parse_git_log_output(output: str) -> _GitTouch | None:
    """Parse the `--format='%H %ct' --name-status` output to a _GitTouch."""
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if not lines:
        return None
    header = lines[0].split()
    if len(header) < _GIT_HEADER_FIELDS:
        return None
    try:
        timestamp = int(header[1])
    except ValueError:
        return None
    chosen = _git_log_chosen_path(lines[1:])
    if chosen is None:
        return None
    state = _parse_path_to_state(chosen)
    if state is None:
        return None
    return _GitTouch(timestamp=timestamp, state=state)


def _git_newest_touch(repo_path: Path, seq: list[int], slug: str | None) -> _GitTouch | None:
    """Find the newest non-merge commit in *repo_path* touching a plan file matching seq/slug."""
    seq_str = _seq_to_str(seq)
    glob = f"{seq_str}_{slug}.md" if slug else f"{seq_str}_*.md"
    pathspecs = [f":(glob)docs/plans/*/{glob}", f":(glob)docs/plans/reviews/*/{glob}"]
    argv = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--all",
        "--no-merges",
        "--max-count=1",
        "--format=%H %ct",
        "--name-status",
        "--",
        *pathspecs,
    ]
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _parse_git_log_output(result.stdout)


def _from_git_history(
    project_dir: Path,
    seq: list[int],
    slug: str | None,
    *,
    provides_repo: bool = False,
) -> _Constraint:
    """Pick the newest commit touching the plan file; derive state (and optionally repo).

    By default the returned constraint carries only ``states``; ``repos`` is
    left silent because the same transition commit is replayed (via pull/
    fetch) into both ``master`` and the agent's clone, and git's `%ct`
    tie-break would otherwise arbitrarily pick one. Callers that genuinely
    need the source-of-edit repo (currently only ``push``) pass
    ``provides_repo=True``.

    Ties on ``%ct`` (one-second resolution) are broken by state progression:
    the more progressed state wins. Tickets only move forward, so a later
    commit with the more progressed state reflects the true current state
    even when its committer date matches an older sibling.
    """
    best: _BestGitTouch | None = None
    for repo in RepoName:
        repo_path = project_dir / repo
        if not (repo_path / ".git").exists():
            continue
        touch = _git_newest_touch(repo_path, seq, slug)
        if touch is None:
            continue
        if (
            best is None
            or touch.timestamp > best.timestamp
            or (touch.timestamp == best.timestamp and _state_rank(touch.state) > _state_rank(best.state))
        ):
            best = _BestGitTouch(timestamp=touch.timestamp, state=touch.state, repo=repo)
    if best is None:
        return _Constraint()
    repos = frozenset({best.repo}) if provides_repo else None
    return _Constraint(repos=repos, states=frozenset({best.state}))


def _name_matches_seq(plan_name: str, seq_str: str) -> bool:
    """Return True when *plan_name* references the same sequence number as *seq_str*."""
    candidate = Path(plan_name).stem.split("/")[-1]
    if "_" not in candidate:
        return candidate == seq_str
    num_part, _ = candidate.split("_", maxsplit=1)
    return num_part == seq_str


def _from_audit_log(
    project_dir: Path,
    seq: list[int],
    mapping: dict[str, tuple[RepoName, PlanState]],
) -> _Constraint:
    """Derive a constraint from the most recent audit entry mentioning the sequence number.

    The state dimension is widened to include every state ranked at or after
    the recorded one in ``_STATE_PROGRESSION``.  Normally every transition is
    performed by a script command that also writes an audit entry, so the audit
    and the on-disk state stay in sync.  As a safety net, widening tolerates
    the rare case where a plan was moved outside a logged script command (e.g. a
    manual ``git mv``, or an ad-hoc agent action).  In those cases the recorded
    state is a *lower bound* on the plan's current state; tickets only ever move
    forward (012 §Assumptions 1), so anything ranked at or after the recorded
    state is still consistent with the audit evidence.
    """
    seq_str = _seq_to_str(seq)
    most_recent: tuple[RepoName, PlanState] | None = None
    for entry in read_audit_log(project_dir):
        names = [entry.input_plan, *entry.output_plans]
        if not any(n is not None and _name_matches_seq(n, seq_str) for n in names):
            continue
        hit = mapping.get(entry.command)
        if hit is not None:
            most_recent = hit
    if most_recent is None:
        return _Constraint()
    repo, state = most_recent
    rank = _state_rank(state)
    forward = frozenset(_STATE_PROGRESSION[rank:]) if rank >= 0 else frozenset({state})
    return _Constraint(repos=frozenset({repo}), states=forward)


def _active_tmux_repo(session_name: str) -> RepoName | None:
    """Return the RepoName of the active window in the named tmux session, or None."""
    try:
        server = libtmux.Server()
        if not server.is_alive():
            return None
        session = server.sessions.get(session_name=session_name, default=None)
        if session is None:
            return None
        active = session.active_window
        if active is None or active.name is None:
            return None
        try:
            return RepoName(active.name)
        except ValueError:
            return None
    except (OSError, libtmux.exc.LibTmuxException):
        return None


def _from_tmux(config: WorkflowConfig) -> _Constraint:
    """Derive a soft repo constraint from the active tmux window, if any."""
    repo = _active_tmux_repo(config.tmux.session_name)
    if repo is None:
        return _Constraint()
    if repo == RepoName.master:
        return _Constraint(repos=frozenset({RepoName.master}))
    return _Constraint(repos=frozenset({repo, RepoName.master}))


def _filesystem_matches(
    project_dir: Path,
    repos: frozenset[RepoName],
    states: frozenset[PlanState],
    seq: list[int],
    slug: str | None,
) -> list[Plan]:
    """Find Plan files in the *repos* x *states* grid whose seq (and slug) match."""
    matches: list[Plan] = []
    for repo in repos:
        if not (project_dir / repo).is_dir():
            continue
        for plan in find_all_plans_in_repo(project_dir, repo):
            if isinstance(plan, Review):
                continue
            if plan.state not in states:
                continue
            if plan.sequence_number != seq:
                continue
            if slug is not None and plan.slug != slug:
                continue
            matches.append(plan)
    return matches


class SourcesDisagreeError(WorkflowError):
    """Raised when the resolver's constraint sources have an empty intersection.

    Carries structured per-(repo, state) contribution data so the CLI can render
    a colorized, aligned table on stderr instead of treating the message as a
    single red blob.
    """

    def __init__(self, fuzzy_path: str, filename: str, contributions: dict[_RepoStatePair, list[str]]) -> None:
        """Store the fuzzy path and contributions; build a plain-text fallback message."""
        self.fuzzy_path = fuzzy_path
        self.filename = filename
        self.contributions = contributions
        lines = [
            f"No matching plan for {fuzzy_path!r}. Sources disagree:",
            *[
                f"  {rs.repo}/docs/plans/{rs.state.value}/{self.filename}   {', '.join(contributions[rs])}"
                for rs in sorted(contributions, key=lambda k: (k.repo.value, k.state.value))
            ],
        ]
        super().__init__("\n".join(lines))

    def __rich__(self) -> Group:
        """Render as a colorized, aligned table for `rich.console.Console.print`."""
        sorted_rows = sorted(self.contributions, key=lambda k: (k.repo.value, k.state.value))
        repo_w = max((len(rs.repo.value) for rs in sorted_rows), default=0)
        state_w = max((len(rs.state.value) for rs in sorted_rows), default=0)
        header = Text(f"No matching plan for {self.fuzzy_path!r}. Sources disagree:")
        rendered: list[Text] = [header]
        for rs in sorted_rows:
            line = Text("  ")
            line.append(rs.repo.value.rjust(repo_w), style=_REPO_STYLES.get(rs.repo, ""))
            line.append("/docs/plans/")
            line.append(rs.state.value, style=_STATE_STYLES.get(rs.state, ""))
            line.append(f"/{self.filename}" + " " * (state_w - len(rs.state.value) + 3))
            for i, src in enumerate(self.contributions[rs]):
                if i > 0:
                    line.append(", ")
                line.append(src, style=_SOURCE_STYLES.get(src, ""))
            rendered.append(line)
        return Group(*rendered)


def _union_report(fuzzy_path: str, filename: str, sources: list[_Source]) -> SourcesDisagreeError:
    """Build a SourcesDisagreeError listing the (repo, state) cells each source supports."""
    contributions: dict[_RepoStatePair, list[str]] = defaultdict(list)
    for source in sources:
        if source.constraint.repos is None and source.constraint.states is None:
            continue
        repos = source.constraint.repos if source.constraint.repos is not None else _ALL_REPOS
        states = source.constraint.states if source.constraint.states is not None else _ALL_STATES
        for r in repos:
            for s in states:
                contributions[_RepoStatePair(repo=r, state=s)].append(source.label)
    return SourcesDisagreeError(fuzzy_path, filename, dict(contributions))


def _is_review_path(fuzzy_path: str) -> bool:
    """Return True when *fuzzy_path* names a review document (any `reviews/` segment)."""
    return "reviews" in Path(fuzzy_path.replace("\\", "/")).parts


def resolve_plan(
    fuzzy_path: str,
    project_dir: Path,
    *,
    config: WorkflowConfig,
    command_name: str | None = None,
    git_provides_repo: bool = False,
) -> Plan:
    """Resolve a fuzzy path reference to a canonical Plan.

    Combines constraints from the fuzzy path, the named CLI subcommand (if any),
    git history across all repo clones, the audit log, and the active tmux
    window. The result is the unique Plan whose (repo, state, sequence number)
    triple lies in the intersection of those constraints.

    Args:
        fuzzy_path: The user-provided path reference. Absolute paths and full
            `repo/docs/plans/state/name` paths short-circuit further inference.
        project_dir: Root of the project (contains master/, planner/, etc.).
        config: Workflow configuration; provides the subcommand and tmux sources.
        command_name: Name of the CLI subcommand being resolved. ``None`` skips
            the subcommand source entirely.
        git_provides_repo: When True, the git data source contributes a repo
            constraint (the clone with the newest commit touching the plan
            file). Only ``push`` opts in: it needs to identify the clone with
            unpushed manual edits. All other callers leave git silent on the
            repo dimension so transition commits don't tie-break to ``master``
            and conflict with the subcommand's agent-owned repo.

    Raises:
        PlanNotFoundError: No plan matches the combined constraints.
        AmbiguousPlanError: Multiple plans match and no clear winner.
        PlanCollisionError: Two tickets share the same sequence number.
        WorkflowError: The constraint sources disagree (empty intersection).
    """
    if Path(fuzzy_path).is_absolute():
        return _resolve_absolute(fuzzy_path, project_dir)

    if _is_review_path(fuzzy_path):
        # Reviews are resolved by a separate entry point (not yet implemented).
        raise PlanNotFoundError(fuzzy_path, [])

    hints = _from_fuzzy_path(fuzzy_path)
    if hints.seq is None:
        raise PlanNotFoundError(fuzzy_path, [])

    # Fast path: hints already fix repo and state — skip git/audit/tmux entirely.
    fast_repos = hints.constraint.repos
    fast_states = hints.constraint.states
    if fast_repos is not None and len(fast_repos) == 1 and fast_states is not None and len(fast_states) == 1:
        matches = _filesystem_matches(project_dir, fast_repos, fast_states, hints.seq, hints.slug)
        if matches:
            return _disambiguate(fuzzy_path, matches, sorted(fast_repos))

    audit_mapping = build_audit_command_repo_state(config)
    sources: list[_Source] = [
        _Source(label="path", constraint=hints.constraint),
        _Source(label="subcommand", constraint=_from_subcommand(config, command_name)),
        _Source(
            label="git",
            constraint=_from_git_history(project_dir, hints.seq, hints.slug, provides_repo=git_provides_repo),
        ),
        _Source(label="audit", constraint=_from_audit_log(project_dir, hints.seq, audit_mapping)),
        _Source(label="tmux", constraint=_from_tmux(config)),
    ]

    repos = _intersect([s.constraint.repos for s in sources])
    states = _intersect([s.constraint.states for s in sources])

    if repos == frozenset() or states == frozenset():
        raise _union_report(fuzzy_path, hints.filename(), sources)

    effective_repos = repos if repos is not None else _ALL_REPOS
    effective_states = states if states is not None else _ALL_STATES
    matches = _filesystem_matches(project_dir, effective_repos, effective_states, hints.seq, hints.slug)
    return _disambiguate(fuzzy_path, matches, sorted(effective_repos))
