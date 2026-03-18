# 003: Loose Path Resolution

Input: `intent/001_idea`

## Goal

Implement the loose path parsing that resolves human-friendly shorthand references
to canonical plan file paths. This is a core utility used by nearly every command.

## Scope

- Parse various shorthand forms into a canonical `Plan` object
- Collision validation: loud error if two non-intent plans share a sequence number
- Resolution context: when a command constrains the expected state, use that to disambiguate

## Supported input forms

From most specific to least specific:

1. `/absolute/path/to/docs/plans/planning/002_pydantic_models.md`
2. `planner/docs/plans/planning/002_pydantic_models.md` (relative with agent prefix)
3. `docs/plans/planning/002_pydantic_models.md` (relative without agent)
4. `planning/002_pydantic_models.md` (state + name)
5. `002_pydantic_models.md` (name only, with .md)
6. `002_pydantic_models` (name only)
7. `intent/012` (state + sequence number)
8. `012` (sequence number only)

## Resolution algorithm

```python
def resolve_plan(
    fuzzy_path: str,
    project_dir: Path,
    expected_states: list[PlanState] | None = None,
    agent: str | None = None,
) -> Plan:
    """Resolve a fuzzy path reference to a canonical Plan.

    Args:
        fuzzy_path: The human-provided path reference.
        project_dir: Root of the project (contains master/, planner/, etc.).
        expected_states: If the command constrains which states are valid, pass them here.
            Used for disambiguation when multiple matches exist.
        agent: If the command constrains which agent repo to search, pass it here.

    Returns:
        A Plan object with the resolved canonical path.

    Raises:
        PlanNotFoundError: No matching plan found.
        AmbiguousPlanError: Multiple matches found and cannot disambiguate.
        PlanCollisionError: Two non-intent plans share the same sequence number.
    """
```

### Steps

1. **Normalize**: strip `.md` suffix if present, normalize path separators.
2. **Extract components**: determine which parts are present (agent, state, sequence number, slug).
3. **Search**: scan `docs/plans/` subdirectories in the relevant repo(s).
   When `agent` is specified, search only that repo. Otherwise search `master/`.
4. **Filter**: if `expected_states` is provided, filter matches to those states.
5. **Collision check**: if multiple non-intent plans match the same sequence number, raise `PlanCollisionError`.
6. **Disambiguate**: if ambiguous between intent and non-intent, prefer non-intent.
   If still ambiguous, raise `AmbiguousPlanError`.

## Helper: find all plans

```python
def find_all_plans(project_dir: Path, repo: str = "master") -> list[Plan]:
    """Scan docs/plans/ subdirectories and return all plans found."""
```

This is also useful for `status`, `followthrough`, and collision validation.

## Helper: parse plan filename

```python
def parse_plan_filename(filename: str) -> tuple[list[int], str]:
    """Parse '002_pydantic_models' into ([2], 'pydantic_models').

    Handles hierarchical numbers like '012.1' -> ([12, 1], 'frobnicator_backend').
    """
```

## File organization

- `workflow/plan_resolver.py` — resolve_plan, find_all_plans, parse_plan_filename
- Custom exceptions in `workflow/exceptions.py`

## Dependencies

- Plan 002 (models): uses `Plan` and `PlanState` models.

## Risks

- The resolution logic has many edge cases. Thorough unit tests are essential,
  especially around disambiguation and collision detection.
- Performance: scanning directories on every resolution is fine for the expected
  number of plans (tens, not thousands). No need for caching.

