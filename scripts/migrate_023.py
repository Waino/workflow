#!/usr/bin/env python
"""Single-use migration script for the 023 (out-of-band) plan series.

Idempotent: safe to re-run. Extended by later plans in the 023 series.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click

from workflow.git_helpers import BARE_REMOTE, LEGACY_BARE_REMOTE, git_is_valid_repo, git_remote_url
from workflow.layout import Layout
from workflow.metadata import LEGACY_ROOT_MARKER, METADATA_FILENAME, ProjectMetadata, write_metadata
from workflow.names import WorktreeName

_REPO_OVERRIDE_KEY_RE = re.compile(r"^(\s*(?:- )?)repo_override:", flags=re.MULTILINE)


def migrate_metadata_file(project_dir: Path, *, dry_run: bool) -> None:
    """Step 1: write a default .workflow.json if absent, then delete the legacy marker."""
    metadata_path = project_dir / METADATA_FILENAME
    if not metadata_path.exists():
        click.echo(f"writing default {metadata_path}")
        if not dry_run:
            write_metadata(project_dir, ProjectMetadata())
    else:
        click.echo(f"{metadata_path} already exists; skipping")

    legacy_marker = project_dir / LEGACY_ROOT_MARKER
    if legacy_marker.exists():
        click.echo(f"removing legacy {legacy_marker}")
        if not dry_run:
            legacy_marker.unlink()
    else:
        click.echo(f"{legacy_marker} not present; skipping")


def migrate_config_worktree_override_key(project_dir: Path, *, dry_run: bool) -> None:
    """Step 2: rewrite the config.yaml key ``repo_override`` to ``worktree_override``."""
    config_path = project_dir / "config.yaml"
    if not config_path.exists():
        click.echo(f"{config_path} not present; skipping")
        return

    text = config_path.read_text(encoding="utf-8")
    new_text, count = _REPO_OVERRIDE_KEY_RE.subn(r"\1worktree_override:", text)
    if count == 0:
        click.echo(f"{config_path} has no repo_override key; skipping")
        return

    click.echo(f"rewriting {count} occurrence(s) of 'repo_override:' to 'worktree_override:' in {config_path}")
    if not dry_run:
        config_path.write_text(new_text, encoding="utf-8")


def _run_git(args: list[str], *, cwd: Path, dry_run: bool) -> None:
    if dry_run:
        click.echo(f"would run: git -C {cwd} {' '.join(args)}")
        return
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603, S607


def _tracked_branches(repo_path: Path, remote: str) -> list[str]:
    """Return local branches whose upstream is on *remote*."""
    result = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short) %(upstream:remotename)", "refs/heads"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    branches = []
    for line in result.stdout.decode().splitlines():
        branch, _, upstream_remote = line.partition(" ")
        if upstream_remote == remote:
            branches.append(branch)
    return branches


def _rename_bare_remote(repo_path: Path, bare_path: Path, *, dry_run: bool) -> None:
    """Rename *repo_path*'s ``origin`` remote to ``bare`` when it points at *bare_path*.

    Leaves an ``origin`` pointing elsewhere alone (a legitimate upstream in oob mode) and
    warns about it. Re-points every branch tracking the old ``origin`` (not just ``main``)
    at ``bare``, since a rename alone does not reliably update branch upstream config on
    every git version.
    """
    if git_remote_url(repo_path, BARE_REMOTE) is not None:
        click.echo(f"{repo_path} already has a '{BARE_REMOTE}' remote; skipping")
        return
    origin_url = git_remote_url(repo_path, LEGACY_BARE_REMOTE)
    if origin_url is None:
        click.echo(f"{repo_path} has no '{LEGACY_BARE_REMOTE}' remote; skipping")
        return
    if Path(origin_url).resolve() != bare_path.resolve():
        click.echo(
            f"[!] {repo_path}: '{LEGACY_BARE_REMOTE}' points at {origin_url!r}, not the project's bare path; "
            "leaving as-is",
        )
        return

    tracked_branches = _tracked_branches(repo_path, LEGACY_BARE_REMOTE)
    click.echo(f"renaming '{LEGACY_BARE_REMOTE}' to '{BARE_REMOTE}' in {repo_path}")
    _run_git(["remote", "rename", LEGACY_BARE_REMOTE, BARE_REMOTE], cwd=repo_path, dry_run=dry_run)
    _run_git(["fetch", BARE_REMOTE], cwd=repo_path, dry_run=dry_run)
    for branch in tracked_branches:
        click.echo(f"re-pointing {repo_path} branch {branch!r} upstream to '{BARE_REMOTE}/{branch}'")
        _run_git(["branch", f"--set-upstream-to={BARE_REMOTE}/{branch}", branch], cwd=repo_path, dry_run=dry_run)


def migrate_bare_remote_rename(project_dir: Path, *, dry_run: bool) -> None:
    """Step 3: rename the workflow-managed remote from ``origin`` to ``bare`` everywhere.

    Out-of-band mode postdates this migration series, so every project it targets is
    in-band; a default :class:`ProjectMetadata` gives the right :class:`Layout` without
    depending on ``.workflow.json`` (:func:`migrate_metadata_file` may have only
    previewed writing it, in ``--dry-run``) or on ``config.yaml``.
    """
    layout = Layout(project_dir=project_dir, metadata=ProjectMetadata())
    for repo in layout.repos():
        bare_path = layout.bare_repo(repo)
        for worktree in WorktreeName:
            repo_path = layout.git_repo_root(worktree, repo)
            if not git_is_valid_repo(repo_path):
                click.echo(f"{repo_path} not present or not a git repo; skipping")
                continue
            _rename_bare_remote(repo_path, bare_path, dry_run=dry_run)


@click.command()
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Project root to migrate.",
)
@click.option("--dry-run", is_flag=True, help="Print actions without performing them.")
def main(project_dir: Path, *, dry_run: bool) -> None:
    """Migrate a pre-023 project directory to the current on-disk layout."""
    migrate_metadata_file(project_dir, dry_run=dry_run)
    migrate_config_worktree_override_key(project_dir, dry_run=dry_run)
    migrate_bare_remote_rename(project_dir, dry_run=dry_run)


if __name__ == "__main__":
    main()
