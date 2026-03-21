# PR Review: 003_path_resolution @ 0f57880

Plan: `docs/plans/checking/003_path_resolution.md`
Reviewed: `workflow/models.py`, `workflow/plan_resolver.py`, `workflow/exceptions.py`,
`tests/test_models.py`, `tests/test_plan_resolver.py`

## Verdict

**Approve with minor fixes.** The implementation is correct and well-structured.
All requirements from the plan are met. The test suite is thorough and covers the
important edge cases. The issues below are a small spec drift, one untested
edge case, and one silent deduplication behaviour that can quietly mislead.

---

## Findings

### 1. `PlanCollisionError.sequence_number` type: `str` vs `int` in plan (minor)

The plan states: *"Carries: sequence number (int), list of colliding Plan objects."*
The implementation stores it as `str` (the zero-padded string, e.g. `"002"`):

```python
def __init__(self, sequence_number: str, colliding: list[Plan]) -> None:
```

The `str` form is more useful — callers have the human-readable label without
re-formatting. The plan spec should be updated to say `str`.

### 2. `_check_collisions` silently skips Review plans (plan says "non-intent")

The plan specifies: *"loud error if two non-intent plans share a sequence number"*.
Reviews are non-intent but are excluded from the collision check alongside INTENT:

```python
if plan.state not in (PlanState.INTENT, PlanState.REVIEW):
```

This is correct in practice — reviews always share the slug of their base plan
so their slugs would never diverge — but it is an undocumented deviation from
the spec. The plan should explicitly state that review documents are also excluded.

### 3. `find_all_plans` silently keeps first winner on same-state tie (plan says raise)

The plan's step 7 says: *"If two matches have the same state in different repos, raise
`AmbiguousPlanError`."* However, this applies to `resolve_plan`, not `find_all_plans`.
`find_all_plans` is used for `status` and `followthrough`; silently keeping the first
is the right behaviour there. The test even notes this:

```python
# Same plan at same state in two repos: first repo in _REPOS order wins silently
```

**No code change needed**, but the plan should clarify that the AmbiguousPlanError
rule applies to `resolve_plan` only.

### 4. Rejected plans silently lose in cross-repo dedup (unspecified behaviour)

`_state_rank` returns `-1` for `REJECTED` and `REVIEW`.
In `find_all_plans`, the condition `_state_rank(plan.state) > _state_rank(existing.state)`
means that a plan at INTENT (rank 0) will *replace* a REJECTED copy (rank -1).
If a plan is rejected in `master` but still appears at INTENT in `planner` (e.g. on a
branch not yet merged), `find_all_plans` will surface the INTENT copy, hiding the rejection.

This is an edge case the plan does not specify. It is low risk given the forward-only
workflow, but worth documenting. Add a note in `find_all_plans`'s docstring:

> Rejected plans (rank -1) are always superseded by any ranked state including INTENT.
> This means a rejected plan may not appear as rejected in the merged list if an
> earlier-state copy exists in another repo.

### 5. `Review` allows `FULL` sub-state without validation (minor gap)

`validate_review_fields` checks CRITIQUE and PR sub-states but not FULL:

```python
if self.review_sub_state == ReviewSubState.CRITIQUE and self.review_timestamp is None:
    ...
if self.review_sub_state == ReviewSubState.PR and self.review_commit is None:
    ...
# FULL falls through with no validation
```

A `Review(review_sub_state=ReviewSubState.FULL, ...)` can be constructed without
error. Since `find_all_plans_in_repo` never constructs FULL reviews and `resolve_plan`
raises `PlanNotFoundError` for them, this has no correctness impact. But it is a
surprising gap. Add an explicit reject in the validator:

```python
if self.review_sub_state == ReviewSubState.FULL:
    msg = "Use full review documents directly; do not construct Review objects for them"
    raise ValueError(msg)
```

### 6. No test for malformed filenames being silently skipped

A file like `README.md` or `CHANGELOG.md` in a state directory would cause
`parse_plan_filename` to raise `ValueError`, which is caught and the file is
skipped. This is the correct behaviour but there is no test for it.

Add one test to `TestFindAllPlansInRepo`:

```python
def test_malformed_filename_skipped(self, tmp_path: Path) -> None:
    # Files that don't match the NNN_slug pattern are silently ignored
    d = tmp_path / "master" / "docs" / "plans" / "planning"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# readme\n")
    assert find_all_plans_in_repo(tmp_path, "master") == []
```

---

## Positive observations

- **`Review.validate_review_fields` adds a `state != REVIEW` guard** not in the plan.
  This is a good defensive improvement; it has its own test (`test_review_rejects_non_review_state`).
- **`_disambiguate` correctly handles intent vs. non-intent tie-breaking** and is tested.
- **Absolute path fast-path** handles reviews, full-review rejection, out-of-project paths,
  and non-existent files — all tested.
- **`parse_review_filename` parses from the right**, correctly handling slugs with underscores.
- **86 tests, all passing.**

---

## Required changes before accept

| # | File | Change |
|---|------|--------|
| 1 | `docs/plans/checking/003_path_resolution.md` | Update `PlanCollisionError` to say `str` not `int`; clarify REVIEW exclusion from collision check; clarify step 7 AmbiguousPlanError applies to `resolve_plan` only |
| 2 | `workflow/models.py` | Add FULL sub-state rejection to `validate_review_fields` |
| 3 | `workflow/plan_resolver.py` | Add docstring note to `find_all_plans` about REJECTED rank behaviour |
| 4 | `tests/test_plan_resolver.py` | Add `test_malformed_filename_skipped` |

