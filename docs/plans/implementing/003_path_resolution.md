# 003: Loose Path Resolution

Input: `intent/001_idea`

## Goal

Implement the loose path parsing that resolves human-friendly shorthand references
to canonical plan file paths. This is a core utility used by nearly every command.

## Scope

- Add a `Review` subclass of `Plan` carrying all review-specific fields as required fields;
  remove `review_sub_state` from `Plan` (it belongs only on `Review`)
- Parse various shorthand forms into a canonical `Plan` object
- Collision validation: loud error if two non-intent plans share a sequence number,
  checked whenever a scan is performed (no dedicated separate pass)
- Resolution context: when a command constrains the expected state, use that to disambiguate
- Cross-repo disambiguation: most-progressed state wins; ambiguity within a single repo is an error

## Changes to the models

### Plan

Plan 002 defines `Plan` with `sequence_number`, `slug`, `state`, `review_sub_state`, and `path`.
Remove `review_sub_state` from `Plan` (it moves to `Review`) and add a validator that prevents
`state == REVIEW` on a plain `Plan`:

```python
class Plan(BaseModel):
    sequence_number: list[int] = Field(min_length=1)
    slug: str
    state: PlanState
    path: Path

    @model_validator(mode="after")
    def state_must_not_be_review(self) -> Plan:
        if self.state == PlanState.REVIEW:
            msg = "Use Review, not Plan, for review documents"
            raise ValueError(msg)
        return self

    @property
    def sequence_number_str(self) -> str: ...

    @property
    def name(self) -> str:
        return f"{self.sequence_number_str}_{self.slug}"
```

### Review

Add `Review` as a subclass of `Plan`. All review-specific fields live here as required fields,
so no optional fields pollute the base class. The `name` property is overridden to produce the
review document naming convention.

`Review` overrides `state_must_not_be_review` with a no-op to prevent the inherited validator
from firing on the fixed `state = PlanState.REVIEW`.

```python
class Review(Plan):
    """A review document under docs/plans/reviews/{sub_state}/."""

    state: PlanState = PlanState.REVIEW
    review_sub_state: ReviewSubState
    review_timestamp: datetime | None = None  # set for CRITIQUE
    review_commit: str | None = None          # set for PR

    @model_validator(mode="after")
    def state_must_not_be_review(self) -> Review:  # no-op: overrides Plan's validator
        return self

    @model_validator(mode="after")
    def validate_review_fields(self) -> Review:
        if self.review_sub_state == ReviewSubState.CRITIQUE and self.review_timestamp is None:
            msg = "CRITIQUE review requires review_timestamp"
            raise ValueError(msg)
        if self.review_sub_state == ReviewSubState.PR and self.review_commit is None:
            msg = "PR review requires review_commit"
            raise ValueError(msg)
        return self

    @property
    def name(self) -> str:
        base = f"{self.sequence_number_str}_{self.slug}"
        if self.review_sub_state == ReviewSubState.PR:
            return f"{base}_{self.review_commit}"
        if self.review_sub_state == ReviewSubState.CRITIQUE and self.review_timestamp is not None:
            return f"{base}_{self.review_timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        return base
```

`find_all_plans_in_repo` returns `list[Plan]`; `Review` instances appear in that list via
polymorphism. Callers that need to distinguish narrow with `isinstance(plan, Review)`.

Full review files (`docs/plans/reviews/full/`) are skipped by `find_all_plans_in_repo` — they
are write-only output documents that no CLI command resolves by fuzzy path.

## Supported input forms

From most specific to least specific:

1. `/absolute/path/to/docs/plans/planning/002_pydantic_models.md`
2. `planner/docs/plans/planning/002_pydantic_models.md` (relative with agent prefix)
3. `docs/plans/planning/002_pydantic_models.md` (relative without agent)
4. `planning/002_pydantic_models.md` (state + name, with .md)
5. `planning/002_pydantic_models` (state + name)
6. `002_pydantic_models.md` (name only, with .md)
7. `002_pydantic_models` (name only)
8. `intent/012` (state + sequence number)
9. `012` (sequence number only)

## Resolution algorithm

```python
def resolve_plan(
    fuzzy_path: str,
    project_dir: Path,
    expected_states: Collection[PlanState] | None = None,
    repo: str | None = None,
) -> Plan:
    """Resolve a fuzzy path reference to a canonical Plan.

    Args:
        fuzzy_path: The human-provided path reference.
        project_dir: Root of the project (contains master/, planner/, etc.).
        expected_states: If the command constrains which states are valid, pass them here.
            Used for disambiguation when multiple matches exist.
        repo: Which repo to search ('master', 'planner', 'builder', 'reviewer').
            If None, search master/. Some commands (doublecheck) may pass multiple
            repos; those callers should resolve per-repo and merge results.

    Returns:
        A Plan object with the resolved canonical path.

    Raises:
        PlanNotFoundError: No matching plan found. Carries: fuzzy_path, repos searched.
        AmbiguousPlanError: Multiple matches found and cannot disambiguate.
            Carries: fuzzy_path, list of candidate Plan objects.
        PlanCollisionError: Two non-intent plans share the same sequence number.
            Carries: sequence number (int), list of colliding Plan objects.
    """
```

### Steps

1. **Absolute path fast-path**: if `fuzzy_path` is an absolute path, resolve it directly.
   - If the file does not exist, raise `PlanNotFoundError`.
   - If the path does not fall within `project_dir`, raise `PlanNotFoundError`
     (paths outside the project are not valid plan references).
   - Parse the path components to construct and return a `Plan` directly — no search needed.

2. **Normalize**: strip `.md` suffix if present, normalize path separators.

3. **Extract components**: determine which parts are present (repo prefix, state, sequence number, slug).
   The repo prefix is a known repo name (`master`, `planner`, `builder`, `reviewer`) as the first
   path component. The state is a known `PlanState` value. Everything else is the plan name.

4. **Search**: call `find_all_plans_in_repo` on the target repo (from step 3 if embedded in the path,
   else from the `repo` parameter, else `master`). Collision detection happens inside `find_all_plans_in_repo`.

5. **Filter by components**: apply the extracted sequence number, slug, and state to narrow the matches.

6. **Filter by expected_states**: if `expected_states` is provided, further filter to those states.

7. **Disambiguate**:
   - If one match remains: return it.
   - If multiple matches remain within a single repo: raise `AmbiguousPlanError`.
     Exception: if ambiguous only between `intent` and one non-intent match, prefer the non-intent.
   - If matches span multiple repos (only possible when the caller searches multiple repos,
     e.g. `doublecheck`): pick the match with the most progressed state.
     State ordering: `intent < planning < todo < implementing < checking < done`.
     `rejected` and `review` are not compared against regular states.
     If two matches have the same state in different repos, raise `AmbiguousPlanError`.
   - If no matches remain: raise `PlanNotFoundError`.

## Helper: find_all_plans_in_repo

```python
def find_all_plans_in_repo(project_dir: Path, repo: str = "master") -> list[Plan]:
    """Scan docs/plans/ in a single repo and return all plans found.

    Raises PlanCollisionError if two non-intent plans with the same sequence number
    are found. This check is performed here rather than in a separate pass,
    so collision detection is free whenever a scan is already needed.

    Skips docs/plans/reviews/full/ — full review documents are write-only output
    and are never resolved by fuzzy path.
    """
```

## Helper: find_all_plans

```python
def find_all_plans(project_dir: Path) -> list[Plan]:
    """Scan all repos and return a merged, deduplicated list of plans.

    When the same plan appears in multiple repos (e.g. 'master' and 'builder'),
    the copy with the most progressed state is kept.
    State ordering for deduplication: intent < planning < todo < implementing < checking < done.
    """
```

Useful for `status`, `followthrough`, and any command that needs a global view of all plans.

## Helper: parse_plan_filename

```python
def parse_plan_filename(filename: str) -> tuple[list[int], str]:
    """Parse '002_pydantic_models' into ([2], 'pydantic_models').

    Splits on the first '_' only (maxsplit=1), so slugs containing underscores
    are preserved correctly: '012.1_foo_bar' -> ([12, 1], 'foo_bar').
    The leading numeric component may be hierarchical ('012.1') and is split on '.'.
    """
```

## Helper: parse_review_filename

```python
def parse_review_filename(
    filename: str, sub_state: ReviewSubState
) -> tuple[list[int], str, datetime | None, str | None]:
    """Parse a review filename into (sequence_number, slug, review_timestamp, review_commit).

    Critique ('002_pydantic_models_2026-03-18T14:30:00Z'):
        Parse the ISO 8601 timestamp suffix from the right.
        The remainder is the base plan name, parsed by parse_plan_filename.
        Returns: ([2], 'pydantic_models', datetime(...), None)

    PR ('002_pydantic_models_0e6baa9'):
        Parse the commit hash suffix (hex string) from the right.
        The remainder is the base plan name.
        Returns: ([2], 'pydantic_models', None, '0e6baa9')

    Full reviews are not parsed — callers should skip them (see find_all_plans_in_repo).

    Parses from the right to correctly handle slugs that contain underscores.
    """
```

## File organization

- `workflow/plan_resolver.py` — `resolve_plan`, `find_all_plans`, `find_all_plans_in_repo`,
  `parse_plan_filename`, `parse_review_filename`
- `workflow/exceptions.py` — `PlanNotFoundError`, `AmbiguousPlanError`, `PlanCollisionError`
- `workflow/models.py` — remove `review_sub_state` from `Plan`; add `Review` subclass

## Dependencies

- Plan 002 (models): extends `Plan` and uses `PlanState`, `ReviewSubState`.

## Risks

- The resolution logic has many edge cases. Thorough unit tests are essential,
  especially around disambiguation, collision detection, and review filename parsing.
- The `doublecheck` command can apply to either the `planner` or `reviewer` repo.
  `resolve_plan` must be called for both repos and the results merged using the
  cross-repo disambiguation rule (most-progressed state wins). Test this path explicitly,
  including the case where the same plan appears in both repos at the same state
  (should raise `AmbiguousPlanError`).
- Performance: scanning directories on every resolution is fine for the expected
  number of plans (tens, not thousands). No need for caching.

