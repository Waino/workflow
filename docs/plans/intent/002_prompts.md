## Full review

Analyze the current state of the whole codebase, checking for various code smells.

### Architecture

- Overengineered frameworks
- Unnecessary abstractions
- Tangled import dependencies
- Duplicated logic across code paths, e.g. a dry-run mode that duplicates some of the computation in the normal mode.
- Heavy reimplementations, when a standard dependency would suffice

### Only considering the happy path

- Error handling: missing entirely, overbroad catch-all
- Missing timeouts/retries on I/O
- Weak input validation

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

### Misc

- Excessively verbose logging
- Inefficient algorithms
- Long string literals in code (should be in templates or asset files)
- Configuration in code
