"""Git helper functions for the workflow CLI.

Provides thin wrappers around git commands that respect dry-run mode.
All helpers that run git operations use cwd=repo_path and the remote named 'origin'.

Read-only guard queries (status/existence/ancestry checks used as preconditions or
resume postconditions) always run for real, even in dry-run mode, so resume previews
stay accurate. Only helpers that perform mutations honor dry_run.

One deliberate exception: deciding whether `transfer_branch_to_main` will fast-forward
or need a rebase requires a `git fetch origin` in the source repo. That fetch mutates
only remote-tracking refs (never the working tree or local branches), so it also runs
for real during resume classification (see each command's `_classify_*_steps`) -- an
accurate preview is impossible without it.
"""

from __future__ import annotations

import enum
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

from workflow.exceptions import WorkflowError
from workflow.shell import console, error_console, run_command, run_condition_command
from workflow.steps import require

GIT_REVPARSE_NO_UPSTREAM = 128


class CommitResult(enum.Enum):
    """Outcome of :func:`git_commit_if_changed`."""

    COMMITTED = "committed"
    NOTHING_TO_COMMIT = "nothing_to_commit"


def git_pull(repo_path: Path, *, dry_run: bool = False, skip_if_no_upstream: bool = False) -> None:
    """Run ``git pull`` in *repo_path*.

    In dry-run mode prints the command without executing it.
    Raises :class:`subprocess.CalledProcessError` on non-zero exit.
    """
    if skip_if_no_upstream:
        returncode = run_condition_command(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=repo_path,
        )
        if returncode == GIT_REVPARSE_NO_UPSTREAM:
            error_console.print("[yellow]\\[!] Branch has no upstream, skipping pull.[/yellow]")
            return
    run_command(["git", "pull"], cwd=repo_path, dry_run=dry_run)


def git_current_branch(repo_path: Path) -> str:
    """Return the name of the current branch in *repo_path*."""
    result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    assert result is not None
    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    return stdout.strip()


def git_commit_hash(repo_path: Path, *, short: bool = True) -> str:
    """Return the current commit hash in *repo_path*."""
    command = ["git", "rev-parse"]
    if short:
        command.append("--short")
    command.append("HEAD")
    result = run_command(command, cwd=repo_path, capture_output=True, text=True)
    assert result is not None
    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    return stdout.strip()


def git_push(repo_path: Path, *, dry_run: bool = False) -> None:
    """Run ``git push origin`` in *repo_path*."""
    run_command(["git", "push", "origin"], cwd=repo_path, dry_run=dry_run)


def git_fetch(repo_path: Path, *, dry_run: bool = False) -> None:
    """Run ``git fetch origin`` in *repo_path*."""
    run_command(["git", "fetch", "origin"], cwd=repo_path, dry_run=dry_run)


def git_status(repo_path: Path, *, dry_run: bool = False) -> None:
    """Print ``git status`` (short, with branch header) for *repo_path*."""
    run_command(["git", "status", "--short", "--branch"], cwd=repo_path, dry_run=dry_run)


def git_status_porcelain(repo_path: Path) -> str:
    """Return ``git status --porcelain`` output for *repo_path* (empty == clean tree).

    Read-only guard query: always runs for real, even in dry-run (see module docstring).
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode().strip()


def git_branch_exists(repo_path: Path, branch: str) -> bool:
    """Return whether *branch* exists locally in *repo_path*.

    Read-only guard query: always runs for real, even in dry-run (see module docstring).
    """
    result = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def git_ref_exists_remote(repo_path: Path, ref: str) -> bool:
    """Return whether ``origin`` has a branch named *ref* in *repo_path*.

    Queries the remote directly (``git ls-remote --heads``) rather than relying on
    local remote-tracking refs, since a prior ``fetch`` cannot be assumed. Read-only
    guard query: always runs for real, even in dry-run.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "ls-remote", "--exit-code", "--heads", "origin", ref],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def git_is_ancestor(repo_path: Path, ancestor: str, descendant: str) -> bool:
    """Return whether *ancestor* is an ancestor of (or equal to) *descendant* in *repo_path*.

    Read-only guard query: always runs for real, even in dry-run.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def git_config_get(repo_path: Path, key: str) -> str | None:
    """Return the configured value for *key* in *repo_path*, or ``None`` if unset.

    Read-only guard query: always runs for real, even in dry-run.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "config", "--get", key],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode().strip()


def git_remote_url(repo_path: Path, name: str) -> str | None:
    """Return the configured URL for remote *name* in *repo_path*, or ``None`` if unset.

    Read-only guard query: always runs for real, even in dry-run.
    """
    return git_config_get(repo_path, f"remote.{name}.url")


def git_index_lock_present(repo_path: Path) -> bool:
    """Return whether a leftover ``index.lock`` is present in *repo_path*'s git dir.

    Handles both worktree repos (lock at ``.git/index.lock``) and bare repos (lock at
    ``index.lock`` directly under *repo_path*). Read-only guard query: always runs for
    real, even in dry-run.
    """
    git_dir = repo_path / ".git"
    lock_dir = git_dir if git_dir.is_dir() else repo_path
    return (lock_dir / "index.lock").exists()


def git_is_valid_repo(repo_path: Path, *, bare: bool = False) -> bool:
    """Return whether *repo_path* is a valid git repository, matching the expected *bare*-ness.

    Read-only guard query: always runs for real, even in dry-run.
    """
    if not repo_path.is_dir():
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--is-bare-repository"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return (result.stdout.decode().strip() == "true") == bare


def git_merge(repo_path: Path, branch: str, *, dry_run: bool = False) -> None:
    """Merge *branch* into the current branch in *repo_path*."""
    run_command(["git", "merge", "--ff-only", "--", branch], cwd=repo_path, dry_run=dry_run)


def git_checkout(repo_path: Path, branch: str, *, create: bool = False, dry_run: bool = False) -> None:
    """Check out *branch* in *repo_path*, optionally creating it with ``-b``."""
    args = ["git", "checkout"]
    if create:
        args.append("-b")
    args.append(branch)
    run_command(args, cwd=repo_path, dry_run=dry_run)


def git_add(repo_path: Path, file_path: Path, *, dry_run: bool = False) -> None:
    """Stage *file_path* in *repo_path*.

    *file_path* may be an absolute path or relative to *repo_path*.
    In dry-run mode prints the command without executing it.
    """
    run_command(["git", "add", str(file_path)], cwd=repo_path, dry_run=dry_run)


def git_commit(repo_path: Path, message: str, *, dry_run: bool = False, no_verify: bool = False) -> None:
    """Stage all tracked changes (``git add -u``) and commit in *repo_path*.

    Pass ``no_verify=True`` to skip pre-commit hooks (``--no-verify``).
    """
    run_command(["git", "add", "-u"], cwd=repo_path, dry_run=dry_run)
    commit_args = ["git", "commit", "-m", message]
    if no_verify:
        commit_args.append("--no-verify")
    run_command(commit_args, cwd=repo_path, dry_run=dry_run)


def git_commit_if_changed(
    repo_path: Path,
    message: str,
    *,
    dry_run: bool = False,
    no_verify: bool = False,
) -> CommitResult:
    """Commit staged changes in *repo_path*, returning a sentinel when there is nothing to commit.

    Unlike :func:`git_commit`, this distinguishes the "nothing to commit" no-op
    (git exits 1 with that message on stdout) from genuine git failures, which
    are re-raised unchanged. Callers should pre-stage their changes (or use the
    ``git add -u`` performed by :func:`git_commit`).
    """
    if dry_run:
        commit_args = ["git", "commit", "-m", message, *(["--no-verify"] if no_verify else [])]
        run_command(commit_args, cwd=repo_path, dry_run=True)
        return CommitResult.COMMITTED

    commit_args = ["git", "commit", "-m", message]
    if no_verify:
        commit_args.append("--no-verify")
    try:
        subprocess.run(commit_args, cwd=repo_path, check=True, capture_output=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or b"") + (exc.stderr or b"")
        # Git phrases the no-op differently when untracked (but unstaged) files are present.
        if any(
            pattern in output
            for pattern in (b"nothing to commit", b"nothing added to commit", b"no changes added to commit")
        ):
            return CommitResult.NOTHING_TO_COMMIT
        raise
    return CommitResult.COMMITTED


def git_mv(repo_path: Path, source: Path, target: Path, *, dry_run: bool = False) -> None:
    """Move *source* to *target* and stage the change in *repo_path*.

    *source* and *target* may be absolute paths; git is invoked with ``cwd=repo_path``.
    """
    run_command(["git", "mv", str(source), str(target)], cwd=repo_path, dry_run=dry_run)


def git_diff_branch(repo_path: Path, *extra_args: str) -> str:
    """Return ``git diff origin/main...HEAD`` output as a decoded string.

    Optionally passes *extra_args* to git (e.g. ``"--name-only"``).
    Raises :class:`subprocess.CalledProcessError` on non-zero exit.
    """
    result = run_command(
        ["git", "diff", "origin/main...HEAD", *extra_args],
        cwd=repo_path,
        capture_output=True,
    )
    assert result is not None
    return result.stdout.decode()


def refresh_main(repo_path: Path, *, dry_run: bool = False) -> None:
    """Bring *repo_path*'s local ``main`` (and ``origin/main``) up to date, without touching HEAD.

    If ``main`` is checked out, runs ``git pull --ff-only`` (the only case that touches the
    working tree, and only by fast-forwarding it). Otherwise runs ``git fetch origin
    main:main``, which updates ``refs/heads/main`` via the explicit refspec without touching
    HEAD or the working tree -- and, as a side effect of the fetch, opportunistically updates
    ``refs/remotes/origin/main`` too (the rebase path in :func:`transfer_branch_to_main`
    relies on that).

    No-ops with a warning if *repo_path* has no ``origin`` remote configured (some test
    fixtures construct bare-bones repos without one).
    """
    if git_remote_url(repo_path, "origin") is None:
        error_console.print(f"[yellow]\\[!] {repo_path} has no 'origin' remote; skipping refresh_main.[/yellow]")
        return
    if git_current_branch(repo_path) == "main":
        run_command(["git", "pull", "--ff-only"], cwd=repo_path, dry_run=dry_run)
    else:
        run_command(["git", "fetch", "origin", "main:main"], cwd=repo_path, dry_run=dry_run)


def park_on_main(repo_path: Path, *, dry_run: bool = False) -> None:
    """Check out ``main`` (if not already there) and refresh it via :func:`refresh_main`.

    Never fails on a dirty tree: the caller has already completed a successful handoff by the
    time this runs, so a cosmetic parking failure must not fail the command. On a dirty tree,
    warns and falls back to :func:`refresh_main` alone (leaving the working tree as-is).
    """
    if git_status_porcelain(repo_path):
        error_console.print(f"[yellow]\\[!] {repo_path} has uncommitted changes; not parking on main.[/yellow]")
        refresh_main(repo_path, dry_run=dry_run)
        return
    if git_current_branch(repo_path) != "main":
        git_checkout(repo_path, "main", dry_run=dry_run)
    refresh_main(repo_path, dry_run=dry_run)


class TransferResult(enum.Enum):
    """Which path :func:`transfer_branch_to_main` took."""

    FAST_FORWARD = "fast_forward"
    REBASED = "rebased"


def branch_needs_rebase(source_repo: Path, branch: str) -> bool:
    """Fetch *source_repo* and return whether *branch* is **not** based on ``origin/main``.

    Used both by :func:`transfer_branch_to_main` itself and by each command's resume
    classification, so a `--dry-run` preview can announce the rebase before it happens
    (see the module docstring's fetch-during-classification exception).
    """
    git_fetch(source_repo)
    head = branch if git_branch_exists(source_repo, branch) else f"origin/{branch}"
    return not git_is_ancestor(source_repo, "origin/main", head)


def _merge_tree_conflict_paths(output: str) -> list[str]:
    """Best-effort extraction of conflicting paths from ``git merge-tree --write-tree`` output.

    Ponytail: regex-matches the ``CONFLICT (...): ... in <path>`` lines `git merge-tree`
    prints for content/rename conflicts; upgrade path is a full parse of its machine-readable
    sections if this ever needs to drive logic rather than just an error message (decision 5:
    merge-tree is for messaging only, never a gate).
    """
    return [m.group(1).strip() for m in re.finditer(r"CONFLICT \([^)]*\):.*?in (.+)$", output, re.MULTILINE)]


def _rebase_onto_main(  # noqa: PLR0913
    source_repo: Path,
    canon_repo: Path,
    branch: str,
    *,
    allow_rebase: bool,
    other_worktrees: Sequence[Path],
    post_rebase_verify: Callable[[], None] | None,
    dry_run: bool,
) -> TransferResult:
    """The rebase path of :func:`transfer_branch_to_main`; see its docstring."""
    plan_name = branch.split("/", 1)[-1]
    require(
        allow_rebase,
        f"{branch} is not fast-forwardable onto origin/main; rerun with the rebase step "
        f"enabled (`workflow rebase {plan_name}`) once it has been rebased.",
    )
    require(
        not git_status_porcelain(source_repo),
        f"{source_repo} has uncommitted changes; commit or discard them before rebasing {branch}.",
    )
    require(
        git_current_branch(source_repo) == branch,
        f"{source_repo} is not on {branch}; check it out first (git checkout {branch}).",
    )
    for worktree in other_worktrees:
        if worktree.exists() and git_current_branch(worktree) == branch:
            msg = f"{branch} is checked out in {worktree}; check out a different branch there first."
            raise WorkflowError(msg)

    if dry_run:
        console.print(f"[dim]would rebase {branch} onto origin/main in {source_repo}[/dim]")
        return TransferResult.REBASED

    try:
        run_command(["git", "rebase", "origin/main"], cwd=source_repo)
    except subprocess.CalledProcessError as exc:
        # REBASE_HEAD, not HEAD: mid-rebase, HEAD is detached at the 'onto' commit
        # (origin/main) itself, which would make merge-tree report no conflicts at all.
        conflict_result = subprocess.run(
            ["git", "merge-tree", "--write-tree", "origin/main", "REBASE_HEAD"],  # noqa: S607
            cwd=source_repo,
            capture_output=True,
            check=False,
        )
        conflicts = _merge_tree_conflict_paths(conflict_result.stdout.decode())
        abort = subprocess.run(
            ["git", "rebase", "--abort"],  # noqa: S607
            cwd=source_repo,
            capture_output=True,
            check=False,
        )
        if abort.returncode != 0:
            error_console.print(
                f"[yellow]\\[!] git rebase --abort failed in {source_repo}: {abort.stderr.decode()}[/yellow]",
            )
        conflict_note = f" Conflicting paths: {', '.join(conflicts)}." if conflicts else ""
        msg = f"rebase of {branch} onto origin/main failed.{conflict_note} Recovery: workflow rebase {plan_name}"
        raise WorkflowError(msg) from exc

    if post_rebase_verify is not None:
        post_rebase_verify()

    run_command(["git", "push", "--force-with-lease", "origin", branch], cwd=source_repo)
    git_fetch(canon_repo, dry_run=dry_run)
    git_pull(canon_repo, dry_run=dry_run)
    git_merge(canon_repo, f"origin/{branch}", dry_run=dry_run)
    git_push(canon_repo, dry_run=dry_run)
    return TransferResult.REBASED


def transfer_branch_to_main(  # noqa: PLR0913
    source_repo: Path,
    canon_repo: Path,
    *,
    branch: str | None = None,
    allow_rebase: bool = False,
    other_worktrees: Sequence[Path] = (),
    post_rebase_verify: Callable[[], None] | None = None,
    dry_run: bool = False,
) -> TransferResult:
    """Land *source_repo*'s branch on ``main``: fast-forward when possible, else rebase.

    Uses *branch* as the merge ref when given (so callers with a fixed feature-branch
    convention, e.g. ``accept``, don't depend on what *source_repo* happens to be
    checked out to); otherwise reads *source_repo*'s current branch.

    Decides fast-forwardability in *source_repo* (via :func:`branch_needs_rebase`)
    before pushing anything, so the problem is discovered before any push rather than
    when canon's merge fails. On the fast-forward path (today's behaviour): ``git push``
    in *source_repo*, ``git pull``/``git merge --ff-only``/``git push`` in *canon_repo*.

    On the rebase path -- refused unless *allow_rebase* is ``True``, since a rebase must
    never be a surprise -- *source_repo* is rebased onto ``origin/main``, force-pushed
    with ``--force-with-lease``, then merged into *canon_repo* the same way. Guarded by:
    *source_repo*'s tree must be clean; *source_repo* must be checked out to *branch*;
    *branch* must not be checked out in any of *other_worktrees*. Never mutates anything
    in ``--dry-run``. On a rebase conflict, aborts the rebase (best effort) and raises
    with the conflicting paths and the recovery (``workflow rebase <plan>``).

    *post_rebase_verify*, when given, runs after a successful rebase and before
    anything is pushed or merged -- it should raise on failure to abort before publishing
    a tree nobody has verified. Only invoked on the rebase path.

    Every step is individually idempotent (a merge of an already-merged ref is a
    harmless no-op), so re-running the whole helper on a partially-completed prior
    transfer is safe. Callers that want to skip the transfer altogether on resume
    should not call this at all (see each command's classify step).

    Does *not* park *source_repo* back on ``main`` -- callers do that afterwards via
    :func:`park_on_main`, and only after their own transition commit has also reached
    *canon_repo* and been pushed (see the module docstring's ordering note in each
    caller); parking here, before that push, is exactly the staleness bug this
    function's callers exist to avoid.

    Raises :class:`~workflow.exceptions.WorkflowError` if *canon_repo* is not on
    ``'main'`` (it always should be at rest; this guards against clobbering a feature
    branch checked out there by mistake).
    """
    require(
        git_current_branch(canon_repo) == "main",
        "transfer_branch_to_main: canon_repo is not on 'main'; won't clobber a feature branch",
    )
    branch = branch or git_current_branch(source_repo)

    if not branch_needs_rebase(source_repo, branch):
        try:
            git_push(source_repo, dry_run=dry_run)
        except subprocess.CalledProcessError:
            if dry_run:
                raise
            # A resumed run: the branch was already rebased locally in a prior attempt
            # (whose force-push then failed) but never force-pushed, so a plain push is
            # rejected as non-fast-forward. Safe to force here: branch_needs_rebase just
            # confirmed origin/main is an ancestor of the local branch.
            run_command(["git", "push", "--force-with-lease", "origin", branch], cwd=source_repo)
        git_pull(canon_repo, dry_run=dry_run)
        # Merge via the remote-tracking ref so the branch need not exist locally.
        git_merge(canon_repo, f"origin/{branch}", dry_run=dry_run)
        git_push(canon_repo, dry_run=dry_run)
        return TransferResult.FAST_FORWARD

    return _rebase_onto_main(
        source_repo,
        canon_repo,
        branch,
        allow_rebase=allow_rebase,
        other_worktrees=other_worktrees,
        post_rebase_verify=post_rebase_verify,
        dry_run=dry_run,
    )
