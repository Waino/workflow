## Command: full review

Analyze the current state of the whole codebase, checking for various code smells.

### Architecture

- Overengineered frameworks
- Unnecessary abstractions
- Tangled import dependencies
- Duplicated logic across code paths, e.g. a dry-run mode that duplicates some of the computation in the normal mode.
- Heavy reimplementations, when a standard dependency would suffice

### Functional correctness

- In particular, review interactions between critical or complex components.

### Only considering the happy path

- Error handling: missing entirely, overbroad catch-all
- Missing timeouts/retries on I/O
- Weak input validation

### Use of pydantic

- Pydantic should be used throughout: avoid returning tuples or dicts
- Types should not be too generic. E.g. `any` or `dict[str, any]` requires very good reasons.
- Are defaults set, where appropriate?
- Is there validation, where appropriate?
- Fields should have docstrings.

### Async misuse

- Blocking inside async functions
- Fire-and-forget tasks without timeout or cancellation
- Race conditions

### Dead code

- Search for dead code: run vulture, analyze the results

### Testing

- Propose integration tests:
  considering the software as a whole, which critical paths should be covered by integration-style tests?
- Only testing the happy path
- Missing edge cases
- Mocking the system-under-test
- Tests not asserting anything meaningful

### Documentation

- Developer docs, user docs: complete and up to date
- Quality of comments in the codebase
    - Prefer commentin on why, not how
    - With an exception for anything unavoidably complex or surprising: for these also comment on the how

### Misc

- Excessively verbose logging
- Inefficient algorithms
- Long string literals in code (should be in templates or asset files)
- Configuration in code
- Logical, consistent naming scheme
- Readability and maintainability
