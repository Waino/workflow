# 005: Plan State Transitions, Git Helpers, and Doublecheck

Input: `intent/001_idea`

## Goal

Implement the core operations for transitioning plans between states and the git helper
functions that the workflow commands use for pushing, pulling, and merging.
Also implement the doublecheck verification suite as a reusable function.

## Scope

- Plan state transitions (moving files between `docs/plans/` subdirectories)
- Git helper functions (pull, push, fetch, merge, branch creation)
- Doublecheck verification suite (reusable function + standalone script command)

## Plan state transitions

```python
def transition_plan(plan: Plan, target_state: PlanState, repo_dir: Path) -> Plan:
    """Move a plan file from its current state subdirectory to the target state subdirectory.

    Returns a new Plan object with the updated state and path.
    Does NOT commit — the caller is responsible for committing.
    """
    source = plan.path
    target_dir = repo_dir / "docs" / "plans" / target_state.value
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    source.rename(target)
    return plan.model_copy(update={"state": target_state, "path": target})
```

## Git helpers

Thin wrappers around git commands, all respecting dry-run mode.

```python
def git_pull(repo_dir: Path, *, dry_run: bool = False) -> None:
    """Pull from the bare repo."""

def git_push(repo_dir: Path, *, dry_run: bool = False) -> None:
    """Push to the bare repo."""

def git_fetch(repo_dir: Path, *, dry_run: bool = False) -> None:
    """Fetch from the bare repo."""

def git_merge(repo_dir: Path, branch: str, *, dry_run: bool = False) -> None:
    """Merge a branch into the current branch."""

def git_checkout(repo_dir: Path, branch: str, *, create: bool = False, dry_run: bool = False) -> None:
    """Checkout a branch, optionally creating it."""

def git_commit(repo_dir: Path, message: str, *, dry_run: bool = False) -> None:
    """Stage all changes and commit."""

def git_current_branch(repo_dir: Path) -> str:
    """Return the name of the current branch."""

def git_commit_hash(repo_dir: Path, short: bool = True) -> str:
    """Return the current commit hash."""
```

These all delegate to `run_command` from plan 004.

## Push-pull-merge sequence

Several commands (todo, implement, accept) share a common git sequence.
Extract as a reusable helper:

```python
def transfer_branch_to_main(
    source_repo: Path,
    master_repo: Path,
    bare_repo: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Push from source agent repo to bare, pull to master, merge to main, push back.

    1. git push (source_repo → bare)
    2. git pull (master ← bare)
    3. git merge branch (in master)
    4. git push (master → bare)
    """
```

## Doublecheck

Implemented as a reusable function, also exposed as a standalone CLI command.

```python
def doublecheck(plan: Plan, project_dir: Path, config: WorkflowConfig) -> list[CheckResult]:
    """Run all verification checks. Returns a list of results (pass/warn/fail)."""
```

### Checks

1. **File scope verification**: planner only touches `docs/plans/`, builder does not touch `docs/plans/intent/`.
   Compare changed files on the branch against `main`.
2. **Noqa/nosec listing**: list all `noqa` and `nosec` comments added in the branch diff.
   This is informational (warning), not a failure.
3. **Git hooks verification**: `.git/hooks/pre-commit` is a symlink to `.git-pre-commit-hook`.
   No other hooks are active.
4. **CLAUDE.md integrity**: verify that the hash of CLAUDE.md on the branch matches
   the known-good hash stored in a separate file (e.g. `.claude_md_hash`).
   The hash file is updated by `workflow claude.md` and should be committed alongside CLAUDE.md.
   This avoids having to write updated hashes into the config.

```python
class CheckResult(BaseModel):
    """Result of a single doublecheck verification."""
    check_name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    details: list[str] = []  # e.g. list of noqa comments found
```

## File organization

- `workflow/state.py` — transition_plan
- `workflow/git_helpers.py` — all git helper functions, transfer_branch_to_main
- `workflow/doublecheck.py` — doublecheck function, CheckResult, individual check functions

## Dependencies

- Plan 002 (models): Plan, PlanState, WorkflowConfig
- Plan 004 (CLI framework): run_command, dry-run support

## Risks

- Git operations are the most error-prone part of the system. Robust error handling
  and clear error messages are essential.
- The doublecheck file scope verification requires diffing branches, which assumes
  the branch exists and is based on main. Edge cases around rebases and force-pushes
  should be handled gracefully.

